"""Field value <-> bytes codec engine.

Everything needed to turn a walker field's display value back into its exact
on-disk bytes (and vice versa), independent of any UI: struct-format
inference verified against the real bytes, named codecs for layouts struct
cannot express (ID3 synchsafe, AIFF 80-bit float, u24be), and the three
bit-field encodings (linear `bits:`, enum `bitsmap:`, context-dependent enum
`bitsdyn:`). Extracted from tui_app.py so the TUI, the CLI, and tests share
one engine without a textual dependency.

The safety contract callers rely on: an `enc` annotation (walker-declared or
inferred) is trusted only after encode(decode(bytes)) reproduces the actual
on-disk bytes; a wrong annotation falls back to hex editing, never a blind
write.
"""

import struct

from acidcat.core.mp3 import (_CHANNEL_MODES as _MP3_CHANMODE,
                              _EMPHASIS as _MP3_EMPHASIS,
                              _VERSION as _MP3_VERSION, _LAYER as _MP3_LAYER)
from acidcat.core.aiff import (_LOOP_MODES as _AIFF_LOOP_MODES,
                               _AES_RATES, _AES_EMPHASIS)

# orders below exist so that tie breaks toward the format's native endianness;
# a walker-declared enc annotation always beats inference.
_ENC_TRY = ("<B", ">B", "<H", ">H", "<h", ">h", "<I", ">I", "<i", ">i",
            "<Q", ">Q", "<q", ">q", "<f", ">f", "<d", ">d")
_ENC_TRY_BE = (">B", "<B", ">H", "<H", ">h", "<h", ">I", "<I", ">i", "<i",
               ">Q", "<Q", ">q", "<q", ">f", "<f", ">d", "<d")

# walk_file labels of formats whose field payloads are big-endian, so infer_enc
# should try BE first there (RMID counts: its editable fields are the embedded
# SMF's, not the RIFF wrapper's).
_BE_FMTS = {"IFF/AIFF", "IFF/AIFC", "Standard MIDI File", "RMID (RIFF/MIDI)",
            "VST FXP preset", "ReCycle RX2", "FLAC", "MP3/MPEG audio", "MP4/M4A"}


def _field_abs(chunk, field):
    """Absolute file offset of a field, mirroring inspect's rule: field offsets
    are relative to the chunk payload base (payload_base, else offset + 8).
    Returns None for derived fields that carry no byte position."""
    if field.get("off") is None:
        return None
    base = chunk.get("payload_base")
    if base is None:
        base = (chunk.get("offset") or 0) + 8
    return base + field["off"]


def infer_enc(value, raw, prefer_be=False):
    """Find a struct format whose pack(value) reproduces `raw` exactly, so a new
    value can be re-encoded to the same on-disk layout. Verified against the real
    bytes (no guessing): returns the format string, or None if `value` is not a
    plain number or nothing round-trips (a rounded-for-display float, a string,
    an odd width) -- in which case the caller falls back to raw hex editing.

    Caveat: the round-trip proves the layout for the current value only. When
    the bytes are endian-symmetric both endiannesses verify, so `prefer_be`
    must reflect the format's native byte order or a later edit could write
    the new value with the wrong one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    for fmt in (_ENC_TRY_BE if prefer_be else _ENC_TRY):
        if struct.calcsize(fmt) != len(raw):
            continue
        try:
            if struct.pack(fmt, value) == raw:
                return fmt
        except (struct.error, OverflowError, ValueError):
            continue
    return None


def _synchsafe_encode(text):
    v = int(text, 0)
    if not 0 <= v < (1 << 28):
        raise ValueError("synchsafe value out of 28-bit range")
    return bytes([(v >> 21) & 0x7f, (v >> 14) & 0x7f, (v >> 7) & 0x7f, v & 0x7f])


def _synchsafe_decode(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _float80_encode(text):
    import math
    v = float(text)
    if v == 0:
        return b"\x00" * 10
    sign = 0x80 if v < 0 else 0
    v = abs(v)
    m, e = math.frexp(v)                          # v = m * 2^e, 0.5 <= m < 1
    exponent = e + 16382                          # inverse of the decoder below
    mantissa = int(round(m * (1 << 64)))
    if mantissa >> 64:                            # rounding overflow
        mantissa >>= 1
        exponent += 1
    return (bytes([sign | ((exponent >> 8) & 0x7f), exponent & 0xff])
            + mantissa.to_bytes(8, "big"))


def _float80_decode(b):
    from acidcat.core.aiff import _parse_ieee_extended
    f = _parse_ieee_extended(bytes(b))
    return int(f) if f == int(f) else f


# named non-struct encodings a walker may declare in a field's `enc`:
# name -> (byte length, encode(text)->bytes, decode(bytes)->number). Used for
# the bespoke layouts struct can't express (ID3 synchsafe, AIFF 80-bit float).
def _u24be_encode(text):
    v = int(text, 0)
    if not 0 <= v < (1 << 24):
        raise ValueError("value out of 24-bit range")
    return v.to_bytes(3, "big")


_CODECS = {
    "synchsafe": (4, _synchsafe_encode, _synchsafe_decode),
    "float80": (10, _float80_encode, _float80_decode),
    "u24be": (3, _u24be_encode, lambda b: int.from_bytes(bytes(b), "big")),
}


# bit-packed fields declare enc="bits:DELTA:CLEN:BITPOS:WIDTH:BIAS": the field
# lives inside a CLEN-byte container starting DELTA bytes from the field's own
# offset; its value occupies WIDTH bits starting BITPOS bits from the container
# MSB; the stored bits are (display value + BIAS) (e.g. FLAC channels store
# count-1, so BIAS=-1). Editing does a read-modify-write on the container so the
# neighbouring bit-fields sharing those bytes are preserved.
def parse_bitfield(enc):
    if not isinstance(enc, str) or not enc.startswith("bits:"):
        return None
    delta, clen, bitpos, width, bias = (int(x) for x in enc.split(":")[1:])
    return delta, clen, bitpos, width, bias


def bitfield_extract(container, bitpos, width, bias):
    shift = len(container) * 8 - bitpos - width
    return ((int.from_bytes(container, "big") >> shift) & ((1 << width) - 1)) - bias


def bitfield_apply(container, bitpos, width, bias, value):
    shift = len(container) * 8 - bitpos - width
    v = int(value) + bias
    if shift < 0 or v < 0 or v >= (1 << width):
        raise ValueError("bitfield value out of range")
    ci = int.from_bytes(container, "big")
    mask = ((1 << width) - 1) << shift
    return ((ci & ~mask) | (v << shift)).to_bytes(len(container), "big")


# enum bit-fields: like bit-fields, but the raw bits map to a label via a table
# (the walker's own decode table). enc="bitsmap:DELTA:CLEN:BITPOS:WIDTH:MAPID".
# The reverse map (label -> raw) lets the user edit by name; the same RMW writes.
_BITMAPS = {"mpeg_chanmode": dict(_MP3_CHANMODE),
            "mpeg_emphasis": dict(_MP3_EMPHASIS),
            "mpeg_version": dict(_MP3_VERSION),
            "mpeg_layer": dict(_MP3_LAYER),
            # protection bit is inverted: stored 0 = CRC present (protected)
            "mpeg_crc": {0: "protected", 1: "unprotected"},
            "aiff_loop_mode": dict(_AIFF_LOOP_MODES),
            "aes_rate": dict(_AES_RATES),
            "aes_emphasis": dict(_AES_EMPHASIS),
            "aes_pro": {0: "consumer", 1: "professional"},
            "aes_kind": {0: "PCM audio", 1: "non-audio"}}


def parse_bitsmap(enc):
    if not isinstance(enc, str) or not enc.startswith("bitsmap:"):
        return None
    _tag, delta, clen, bitpos, width, mapid = enc.split(":")
    return int(delta), int(clen), int(bitpos), int(width), mapid


def resolve_bitsmap(mapid, text):
    """User text (a label, case-insensitive, or a raw index) -> raw bits, or
    None if it is neither."""
    return _resolve_in_map(_BITMAPS.get(mapid, {}), text)


def _resolve_in_map(m, text):
    t = text.strip()
    for k, v in m.items():
        if str(v).lower() == t.lower():
            return k
    try:
        iv = int(t, 0)
    except ValueError:
        return None
    return iv if iv in m else None


# context-dependent enum bit-fields: the raw->value map is COMPUTED from the
# container bytes (e.g. MP3 bitrate depends on the version+layer bits in the same
# header word). enc="bitsdyn:DELTA:CLEN:BITPOS:WIDTH:DYNID"; _DYNMAPS[DYNID] is a
# function(container_bytes)->{raw: value}.
def _mpeg_ctx(container):
    word = int.from_bytes(container, "big")     # the 4-byte MPEG header word
    return (word >> 19) & 0x03, (word >> 17) & 0x03    # version_id, layer_id


def _mpeg_bitrate_map(container):
    from acidcat.core.mp3 import (_LAYER, _BR_V1_L1, _BR_V1_L2, _BR_V1_L3,
                                  _BR_V2_L1, _BR_V2_L23)
    vid, lid = _mpeg_ctx(container)
    layer, is_v1 = _LAYER.get(lid), vid == 0b11
    if layer == "Layer I":
        table = _BR_V1_L1 if is_v1 else _BR_V2_L1
    elif layer == "Layer II":
        table = _BR_V1_L2 if is_v1 else _BR_V2_L23
    elif layer == "Layer III":
        table = _BR_V1_L3 if is_v1 else _BR_V2_L23
    else:
        return {}
    return {i: v for i, v in enumerate(table) if v > 0}


def _mpeg_samplerate_map(container):
    from acidcat.core.mp3 import _SAMPLE_RATES
    vid, _lid = _mpeg_ctx(container)
    return {i: r for i, r in enumerate(_SAMPLE_RATES.get(vid, ())) if r > 0}


_DYNMAPS = {"mpeg_bitrate": _mpeg_bitrate_map,
            "mpeg_samplerate": _mpeg_samplerate_map}


def parse_bitsdyn(enc):
    if not isinstance(enc, str) or not enc.startswith("bitsdyn:"):
        return None
    _tag, delta, clen, bitpos, width, dynid = enc.split(":")
    return int(delta), int(clen), int(bitpos), int(width), dynid


def enc_size(enc):
    return _CODECS[enc][0] if enc in _CODECS else struct.calcsize(enc)


def encode_value(enc, text):
    """Encode user text as bytes for a field's declared encoding: a named codec
    (synchsafe, ...) or a struct format string. Ints accept 0x../0b.. prefixes.
    Raises ValueError/struct.error on bad input."""
    if enc in _CODECS:
        return _CODECS[enc][1](text)
    if enc[-1] in "fd":
        return struct.pack(enc, float(text))
    return struct.pack(enc, int(text, 0))


def decode_value(enc, b):
    if enc in _CODECS:
        return _CODECS[enc][2](b)
    return struct.unpack(enc, b)[0]
