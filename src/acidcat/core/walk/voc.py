"""Creative Voice File (.VOC) walker.

The sample format of the DOS era, written by the Sound Blaster's own tools and
carried inside almost every Build-engine and early-90s game archive. A 26-byte
header, then a chain of typed blocks: a type byte, a 24-bit little-endian
length, and a payload. The chain ends at a bare type-0 byte, which carries no
length field at all -- the single most common way to get the walk wrong.

Two sound blocks exist and a file may hold either or both:

    01  the original: a time constant and a codec byte, then samples
    09  v1.20's replacement: an explicit u32 rate, bit width, channel count
        and format code, then samples

Block 02 continues whichever came before it and carries NO format bytes of its
own, so a reader that treats every sound block as self-describing loses the
rate on every file longer than one block. `!BOSS.VOC` is 8194 bytes of block 01
followed by four 8192-byte continuations and a 4605-byte tail; its block 02
opens `7f 7d 7c` directly against block 01's closing `81 81 81 7f 7d 7f`, which
is what a continuation looks like when you check rather than assume.

Everything below was derived from 896 real specimens (331 in DUKE3D.GRP, 565 in
SW.GRP) rather than from the published spec, and where the two could be
compared the specimens agreed:

    header size     26 in all 896
    checksum        (~version + 0x1234) & 0xFFFF in all 896, no exceptions
    blocks present  01 (805), 09 (212 readable + 1 truncated), 05 (34), 02 (18)
    absent          03, 04, 06, 07 and 08 occur in no specimen
    codec byte      0 in every block 01; no ADPCM anywhere in the corpus
    format code     0 (209) and 4 (3) in block 09
    channels        1 in every block 09; no stereo anywhere

WHAT THE CORPUS CANNOT SETTLE. Not one of the 896 files carries a block 01 and
a block 09 together, so the time-constant formula has no in-file cross-check
here: nothing states the same stream's rate twice for the two to be compared.
The formula below is the documented one, and the seven constants in use produce
5988, 8000, 10870, 10989, 11111, 21739 and 22222 Hz -- values that cluster
around the era's 11025 and 22050 in exactly the way a coarse integer divisor
would. That is circumstantial support and it is not proof, which is why the
field carries the constant it was derived from rather than the rate alone.

Block types 03, 04, 06, 07 and 08 do not occur in that corpus. They are framed
here from their documented layouts so the chain does not derail on a file that
has them, and they are NOT claimed as verified -- a walker that says it checked
something it never saw is the more expensive kind of wrong.
"""

import struct

from acidcat.core.infra.findings import coded, defect
from acidcat.core.infra.limits import hit
from acidcat.core.walk.base import _f, _open, _size

MAGIC = b"Creative Voice File\x1a"
_HDR_MIN = 26
# 64 MB: the largest specimen measured is 1.1 MB, so this is roughly sixty
# times the real maximum. A cap that bites is reported, never swallowed.
_READ_CAP = 64 * 1024 * 1024
# Blocks whose payload is sample data, and how the rate reaches them.
_SOUND = (1, 9)

# codec byte in block 01. Only 0 occurs in the corpus; the rest are framed and
# named so a file carrying one is identified rather than decoded as noise.
_PACK = {0: "8-bit unsigned PCM", 1: "4-bit Creative ADPCM",
         2: "2.6-bit Creative ADPCM", 3: "2-bit Creative ADPCM"}
# format code in block 09. 0 and 4 are the two seen, and both were confirmed by
# decoding: code 0 samples sit around 0x80, code 4 samples sit around 0.
_FMT = {0: ("8-bit unsigned PCM", 8, False), 1: ("4-bit Creative ADPCM", 4, False),
        2: ("2.6-bit Creative ADPCM", 3, False), 3: ("2-bit Creative ADPCM", 2, False),
        4: ("16-bit signed PCM", 16, True), 6: ("A-law", 8, False),
        7: ("mu-law", 8, False), 0x200: ("4-bit ADPCM (16-bit source)", 4, False)}
# documented payload sizes for the non-sound blocks, used only to frame them
_FIXED = {3: 3, 4: 2, 6: 2, 7: 0, 8: 4}


def rate_from_constant(tc):
    """Sample rate for a block 01 time constant, or None if it cannot be one.

    The DOS formula, 1000000 / (256 - tc), and it does not land on the round
    numbers a modern reader expects: the corpus's commonest constant is 165,
    which is 10989 Hz and not the 11025 the same sounds carry when they are
    rewritten as block 09. Reporting 11025 here would be tidier and wrong.
    """
    if not 0 <= tc <= 255 or tc == 0:
        return None
    denom = 256 - tc
    return int(round(1000000.0 / denom)) if denom > 0 else None


def _blocks(data, warns):
    """Walk the block chain. Yields (type, offset, payload_offset, length)."""
    if len(data) < _HDR_MIN:
        return
    start = struct.unpack_from("<H", data, 20)[0]
    if not _HDR_MIN <= start <= len(data):
        warns.append(defect(
            "pointer.dangling",
            f"header claims the blocks start at {start}, which is "
            f"outside the file; walking from {_HDR_MIN} instead"))
        start = _HDR_MIN
    i = start
    while i < len(data):
        kind = data[i]
        if kind == 0:                      # terminator: one byte, no length
            return
        if i + 4 > len(data):
            warns.append(defect("header.truncated",
                                f"block header at 0x{i:x} is cut off by the end of "
                                f"the file"))
            return
        length = data[i + 1] | (data[i + 2] << 8) | (data[i + 3] << 16)
        body = i + 4
        if body + length > len(data):
            have = max(0, len(data) - body)
            warns.append(defect(
                "size.overrun",
                f"block {kind:02d} at 0x{i:x} declares {length:,} "
                f"bytes but only {have:,} remain; reading what is there"))
            length = have
            yield kind, i, body, length
            return
        yield kind, i, body, length
        i = body + length
    warns.append(defect("required.missing",
                        "the block chain ran to the end of the file without a "
                        "terminator"))


def parse_voc(data):
    """{version, streams: [...], texts: [...], blocks: [...]}, best effort.

    A `stream` is one sound block plus every continuation that follows it, which
    is the unit a decoder actually wants: continuations carry no format of their
    own, so they only mean anything attached to the block that set it.
    """
    warns = []
    version = struct.unpack_from("<H", data, 22)[0] if len(data) >= 24 else 0
    checksum = struct.unpack_from("<H", data, 24)[0] if len(data) >= 26 else 0
    streams, texts, blocks, cur = [], [], [], None

    for kind, off, body, length in _blocks(data, warns):
        blocks.append({"type": kind, "offset": off, "length": length})
        if kind == 1:
            tc = data[body] if length >= 1 else None
            pack = data[body + 1] if length >= 2 else None
            cur = {"offset": body + 2, "size": max(0, length - 2), "block": off,
                   "rate": rate_from_constant(tc) if tc is not None else None,
                   "bits": 8, "channels": 1, "signed": False,
                   "codec": _PACK.get(pack, f"unknown codec {pack}"),
                   "pcm": pack == 0, "time_constant": tc, "parts": 1}
            streams.append(cur)
        elif kind == 9:
            if length >= 12:
                rate, bits, ch, fmt = struct.unpack_from("<IBBH", data, body)
                name, fbits, signed = _FMT.get(fmt, (f"unknown format {fmt}", 0, False))
                cur = {"offset": body + 12, "size": max(0, length - 12),
                       "block": off, "rate": rate or None,
                       "bits": bits or fbits or None, "channels": ch or 1,
                       "signed": signed, "codec": name, "pcm": fmt in (0, 4),
                       "time_constant": None, "parts": 1}
                streams.append(cur)
            else:
                warns.append(defect("chunk.short",
                                    f"block 09 at 0x{off:x} is {length} bytes, too "
                                    f"short for its 12-byte header"))
                cur = None
        elif kind == 2:
            if cur is None:
                warns.append(defect("chunk.order",
                                    f"continuation block at 0x{off:x} follows no sound "
                                    f"block; its format is unknown"))
            else:
                cur["size"] += length
                cur["parts"] += 1
        elif kind == 5:
            texts.append(data[body:body + length].split(b"\0", 1)[0]
                         .decode("latin-1", "replace"))
        elif kind == 8:
            # precedes a block 01 and overrides its constant; framed, unverified
            warns.append(coded("layout.unmeasured",
                               f"block 08 at 0x{off:x}: extended format header, which "
                               f"no specimen in the reference corpus carries"))
    return {"version": version, "checksum": checksum, "streams": streams,
            "texts": texts, "blocks": blocks, "warnings": warns}


def _version(v):
    return f"{v >> 8}.{v & 0xFF:02d}"


def _duration(s):
    """Seconds of audio in a stream, or None when the rate is not knowable."""
    if not s["rate"] or not s["bits"] or not s["channels"]:
        return None
    per_sec = s["rate"] * s["channels"] * (s["bits"] / 8.0)
    return (s["size"] / per_sec) if per_sec > 0 else None


def inspect_voc(filepath):
    file_size = _size(filepath)
    with _open(filepath) as fh:
        data = fh.read(min(file_size, _READ_CAP))
    if len(data) < len(MAGIC) or not data.startswith(MAGIC):
        return [], [defect("magic.mismatch", "not a Creative Voice File (.voc)")]

    file_warns = []
    if file_size > _READ_CAP:
        file_warns.append(hit(
            "read_bytes", _READ_CAP, file_size,
            f"file is {file_size:,} bytes; only the first "
            f"{_READ_CAP // (1024 * 1024)} MB was read, so blocks past that "
            f"point are not described"))

    info = parse_voc(data)
    file_warns.extend(info["warnings"])

    want = (~info["version"] + 0x1234) & 0xFFFF
    hdr_warns = []
    if len(data) < _HDR_MIN:
        hdr_warns.append(defect(
            "header.truncated",
            f"header truncated: {len(data)} bytes of the "
            f"{_HDR_MIN}-byte header are present"))
    elif info["checksum"] != want:
        hdr_warns.append(defect(
            "checksum.mismatch",
            f"header checksum is 0x{info['checksum']:04x}, and the version "
            f"field says it should be 0x{want:04x}"))

    total = sum(s["size"] for s in info["streams"])
    chunks = [{
        "id": "VOC", "offset": 0, "size": _HDR_MIN, "payload_base": 0,
        "summary": (f"Creative Voice File v{_version(info['version'])} -- "
                    f"{len(info['streams'])} stream(s), {total:,} B of samples"),
        "fields": [
            _f(0x00, 20, "magic", "Creative Voice File\\x1a"),
            # guarded like parse_voc's own reads: a file that is just the
            # 20-byte magic reached this unpack and raised (the one crasher a
            # full truncation sweep found across twelve formats)
            *([_f(0x14, 2, "header_size", struct.unpack_from("<H", data, 20)[0],
                  "offset of the first block")] if len(data) >= 22 else []),
            *([_f(0x16, 2, "version", _version(info["version"]),
                  f"0x{info['version']:04x}"),
               _f(0x18, 2, "checksum", f"0x{info['checksum']:04x}",
                  "(~version + 0x1234) & 0xFFFF")] if len(data) >= 26 else []),
        ],
        "warnings": hdr_warns,
    }]

    for n, s in enumerate(info["streams"], 1):
        secs = _duration(s)
        rate = f"{s['rate']} Hz" if s["rate"] else "rate unknown"
        parts = f", {s['parts']} blocks" if s["parts"] > 1 else ""
        w = []
        if not s["rate"]:
            w.append(coded("value.assumed",
                           "no usable rate in this block, so the duration and the "
                           "playback speed are unknown rather than assumed"))
        if not s["pcm"]:
            w.append(coded("decode.partial",
                           f"{s['codec']} is not linear PCM; these bytes are a codec "
                           f"and play as noise if fed to a PCM player"))
        if s["size"] == 0:
            w.append(defect("required.missing", "the sound block carries no samples"))
        if s["offset"] + s["size"] > file_size:
            w.append(defect("size.overrun",
                            f"samples run past the end of the file "
                            f"(@0x{s['offset']:x} + {s['size']:,})"))
        fields = [
            _f(None, 0, "sample_rate", s["rate"] if s["rate"] else "unknown",
               (f"time constant {s['time_constant']}, "
                f"1000000/(256-{s['time_constant']})"
                if s["time_constant"] is not None else "stated by the block")),
            _f(None, 0, "bits_per_sample", s["bits"] or "unknown"),
            _f(None, 0, "channels", s["channels"]),
            _f(None, 0, "encoding", s["codec"],
               "unsigned, centred on 0x80" if s["bits"] == 8 and s["pcm"]
               else ("signed, centred on 0" if s["signed"] else None)),
        ]
        if secs is not None:
            fields.append(_f(None, 0, "duration", f"{secs:.3f} s"))
        chunks.append({
            "id": f"snd[{n}]", "offset": s["offset"], "size": s["size"],
            "summary": (f"{s['codec']} @ {rate}, {s['size']:,} B{parts}"
                        + (f", {secs:.2f} s" if secs is not None else "")),
            "fields": fields, "warnings": w, "payload_base": s["offset"],
        })

    for n, t in enumerate(info["texts"], 1):
        chunks.append({
            "id": f"txt[{n}]", "offset": 0, "size": len(t),
            "summary": f"text block: {t!r}" if t else "text block (empty)",
            "fields": [_f(None, 0, "text", t or "(empty)")], "warnings": [],
        })
    return chunks, file_warns
