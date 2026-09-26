"""Sun / NeXT audio (.au, also .snd) walker.

The self-describing form of the raw PCM that came before it: a fixed 24-byte
big-endian header -- magic, the offset where audio begins, its size, an encoding
code, a sample rate and a channel count -- then an optional annotation string,
then the samples. It is where the ".snd" magic and the mu-law codec of early
Unix and NeXT workstations were written down, and it is deliberately minimal:
everything a decoder needs sits in six 32-bit fields.

Big-endian throughout, which is the tell that it comes from the Motorola side of
the 1980s and not the RIFF/Intel side. The data size may be 0xFFFFFFFF, meaning
"not known" -- a stream written before its own length was, so the real size is
whatever bytes follow the header. That case is common enough that treating the
literal 4294967295 as a byte count, rather than as the sentinel it is, is the
first way to get this format wrong.

The encoding CODE, not a bit-depth field, is what names the sample format. The
companded telephone codecs -- mu-law (code 1) and A-law (code 27) -- are eight
bits on disk but are NOT linear PCM: fed straight to a PCM player they play as
noise, so they are named and flagged rather than passed off as samples, the same
way the VOC walker treats them. The linear and float codes (2 through 7) are
real PCM and carry a usable bit depth; the fixed-point, emphasis, DSP-program
and ADPCM codes are framed from their documented layouts and named, not decoded,
so a file carrying one is identified rather than read as noise.

The id is "au"; the MPC2000 ".snd" is a different format (id "snd") with no
".snd" magic, told apart from this one at sniff time by content.
"""

import struct

from acidcat.core.infra.findings import defect
from acidcat.core.walk.base import _f, _open, _size

MAGIC = b".snd"
_HDR_MIN = 24
# we describe the header and its annotation, not the audio, so a small bounded
# read is enough; the annotation is a comment field, kilobytes at most in
# practice. a forged data offset cannot force a large read: it is clamped here.
_HEAD_CAP = 1 * 1024 * 1024
# annotation bytes rendered into the field, so a large or forged data offset does
# not turn a comment into a megabyte-long value.
_ANNOT_CAP = 4096
# data_size == this means "length not known" (a stream), not 4 GB of audio.
_UNKNOWN_SIZE = 0xFFFFFFFF

# encoding code -> (name, bits, signed, linear_pcm). bits is 0 where the code has
# no fixed on-disk sample width (programs, fragmented data, some ADPCM). linear
# marks the codes whose bytes are playable PCM; the companded and compressed
# codes are named and flagged, not decoded.
_ENC = {
    1:  ("8-bit G.711 mu-law", 8, False, False),
    2:  ("8-bit linear PCM", 8, True, True),
    3:  ("16-bit linear PCM", 16, True, True),
    4:  ("24-bit linear PCM", 24, True, True),
    5:  ("32-bit linear PCM", 32, True, True),
    6:  ("32-bit IEEE float", 32, True, True),
    7:  ("64-bit IEEE float", 64, True, True),
    8:  ("fragmented sampled data", 0, False, False),
    9:  ("nested sound structures", 0, False, False),
    10: ("DSP program", 0, False, False),
    11: ("8-bit fixed point", 8, True, False),
    12: ("16-bit fixed point", 16, True, False),
    13: ("24-bit fixed point", 24, True, False),
    14: ("32-bit fixed point", 32, True, False),
    16: ("SoundView display data (not audio)", 0, False, False),
    17: ("8-bit mu-law, silence run-length coded", 0, False, False),
    18: ("16-bit linear, emphasis", 16, True, False),
    19: ("16-bit linear, compressed", 16, True, False),
    20: ("16-bit linear, emphasis + compressed", 16, True, False),
    21: ("Music Kit DSP commands", 0, False, False),
    22: ("Music Kit DSP samples", 0, False, False),
    23: ("4-bit G.721 ADPCM", 4, False, False),
    24: ("G.722 ADPCM", 0, False, False),
    25: ("3-bit G.723 ADPCM", 3, False, False),
    26: ("5-bit G.723 ADPCM", 5, False, False),
    27: ("8-bit G.711 A-law", 8, False, False),
    28: ("AES", 0, False, False),
    29: ("8-bit delta mu-law", 0, False, False),
}
# 9, 16, 17, 22, 28 and 29 were absent until a cross-check against the Kaitai
# Struct au spec found them. Their absence was not a missing nicety: the walker
# warns that a code "is not one of the documented codes", and these six ARE
# documented (the Kaitai enum is assembled from the NeXT soundstruct header,
# libsndfile, sox and audiofile), so the tool was contradicting the format on
# six legal values. They carry bits 0 and linear False on purpose -- none is
# plain PCM and no width has been checked here against a real file, so duration
# stays unknown rather than plausible, which is what _FIXED_WIDTH below is for.
# codes whose on-disk width per sample is fixed, so size -> duration is exact:
# the linear and float PCM, the companded G.711 pair (one byte per sample), the
# fixed-point family and 16-bit emphasis. The compressed codes (19, 20) and the
# ADPCM tail (23-26) are framed, not claimed: their bits are nominal or coded
# widths that have not been checked against a real file, so duration stays
# unknown rather than plausible.
_FIXED_WIDTH = frozenset({1, 2, 3, 4, 5, 6, 7, 11, 12, 13, 14, 18, 27})


def _annotation(data, data_offset):
    """The comment between the header and the audio, NUL-trimmed, or ''.

    Bounded by _ANNOT_CAP so a forged data offset cannot render a comment field
    as a megabyte of text. Returns '' when the offset does not enclose one.
    """
    if data_offset <= _HDR_MIN or data_offset > len(data):
        return ""
    raw = data[_HDR_MIN:min(data_offset, _HDR_MIN + _ANNOT_CAP)]
    return raw.split(b"\0", 1)[0].decode("latin-1", "replace")


def parse_header(data):
    """The six header words as a dict, or None if this is not a .au header.

    Shared with the convert path so the 24-byte layout has one definition. Fields
    are big-endian: data_offset, data_size (0xFFFFFFFF = unknown), encoding (an
    _ENC code), sample_rate, channels.
    """
    if len(data) < _HDR_MIN or data[:4] != MAGIC:
        return None
    off, size, enc, rate, ch = struct.unpack_from(">IIIII", data, 4)
    return {"data_offset": off, "data_size": size, "encoding": enc,
            "sample_rate": rate, "channels": ch}


def _duration(size, rate, bits, channels, fixed):
    """Seconds of audio, or None when the on-disk sample width is not fixed.

    `fixed` is the encoding's membership in _FIXED_WIDTH, deliberately separate
    from `linear`: mu-law is not playable as PCM but is one byte per sample, so
    its duration is as knowable as 8-bit PCM's.
    """
    if not (fixed and size and rate and bits and channels):
        return None
    per_sec = rate * channels * (bits / 8.0)
    return (size / per_sec) if per_sec > 0 else None


def inspect_au(filepath):
    file_size = _size(filepath)
    with _open(filepath) as fh:
        data = fh.read(min(file_size, _HEAD_CAP))
    if len(data) < len(MAGIC) or data[:4] != MAGIC:
        return [], ["not a Sun/NeXT audio file (.au/.snd)"]

    file_warns = []
    if len(data) < _HDR_MIN:
        return ([{
            "id": "au", "offset": 0, "size": len(data),
            "summary": "Sun/NeXT audio, header truncated",
            "fields": [_f(0x00, 4, "magic", ".snd")],
            "warnings": [f"header truncated: {len(data)} of the {_HDR_MIN}-byte "
                         f"header are present"],
            "payload_base": 0,
        }], file_warns)

    data_offset, data_size, encoding, rate, channels = struct.unpack_from(
        ">IIIII", data, 4)
    name, bits, _signed, linear = _ENC.get(
        encoding, (f"unknown encoding {encoding}", 0, False, False))

    # effective audio size: the declared size, or what follows the header when
    # the file says it does not know (0xFFFFFFFF).
    unknown = data_size == _UNKNOWN_SIZE
    avail = max(0, file_size - data_offset) if data_offset <= file_size else 0
    eff_size = avail if unknown else data_size

    hdr_warns = []
    if data_offset < _HDR_MIN:
        hdr_warns.append(f"data offset {data_offset} is inside the "
                         f"{_HDR_MIN}-byte header")
    elif data_offset > file_size:
        hdr_warns.append(defect(
            "pointer.dangling",
            f"data offset {data_offset} points past the end of the "
            f"{file_size:,}-byte file"))
    if encoding not in _ENC:
        hdr_warns.append(f"encoding code {encoding} is not one of the documented "
                         f"Sun/NeXT codes")
    if rate == 0:
        hdr_warns.append("sample rate is 0")
    if channels == 0:
        hdr_warns.append("channel count is 0")

    annot = _annotation(data, data_offset)
    dsize = "unknown (streaming)" if unknown else f"{data_size:,}"
    hdr_size = data_offset if _HDR_MIN <= data_offset <= file_size else _HDR_MIN
    fields = [
        _f(0x00, 4, "magic", ".snd"),
        _f(0x04, 4, "data_offset", data_offset, "where the audio begins",
           enc=">I", raw=data_offset),
        _f(0x08, 4, "data_size", dsize, "0xFFFFFFFF means unknown/streaming",
           enc=">I", raw=data_size),
        _f(0x0C, 4, "encoding", f"{encoding} ({name})", enc=">I", raw=encoding),
        _f(0x10, 4, "sample_rate", rate, "Hz", enc=">I", raw=rate),
        _f(0x14, 4, "channels", channels, enc=">I", raw=channels),
    ]
    if annot:
        fields.append(_f(0x18, data_offset - _HDR_MIN, "annotation", annot,
                         "comment between the header and the audio"))

    # payload_base 0: the field offsets are file-absolute and this header has no
    # RIFF-style 8-byte prefix, so the default (offset + 8) would both misplace
    # every field and make the header claim eight bytes the audio owns.
    chunks = [{
        "id": "au", "offset": 0, "size": hdr_size,
        "summary": (f"Sun/NeXT audio -- {name} @ {rate} Hz, {channels} ch, "
                    f"{eff_size:,} B"),
        "fields": fields, "warnings": hdr_warns, "payload_base": 0,
    }]

    if data_offset <= file_size and eff_size > 0:
        secs = _duration(eff_size, rate, bits, channels, encoding in _FIXED_WIDTH)
        w = []
        if not linear:
            w.append(f"{name} is not linear PCM; these bytes are a codec and "
                     f"play as noise if fed to a PCM player")
        if data_offset + eff_size > file_size:
            w.append(defect("size.overrun",
                            f"audio runs past the end of the file "
                            f"(@0x{data_offset:x} + {eff_size:,}); "
                            f"{avail:,} bytes are there"))
        dfields = [
            _f(None, 0, "encoding", name),
            _f(None, 0, "sample_rate", rate if rate else "unknown"),
            _f(None, 0, "channels", channels),
        ]
        if bits:
            dfields.append(_f(None, 0, "bits_per_sample", bits))
        if secs is not None:
            dfields.append(_f(None, 0, "duration", f"{secs:.3f} s"))
        # a cut file's audio chunk owns what is there, not what was declared
        chunks.append({
            "id": "data", "offset": data_offset, "size": min(eff_size, avail),
            "summary": (f"{name} @ {rate} Hz, {eff_size:,} B"
                        + (f", {secs:.2f} s" if secs is not None else "")),
            "fields": dfields, "warnings": w, "payload_base": data_offset,
        })
    return chunks, file_warns
