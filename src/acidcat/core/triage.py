"""Generic structural triage -- the "missing middle" between a full walker and
flat rejection.

When no format-specific walker matches, this recognizes an unknown *chunked
container* (and guesses whether it holds audio) from universal signals alone:

  * a printable 4-byte magic that opens an IFF/RIFF-style [tag][size] chunk grid
    which tiles the file to EOF (tried both endiannesses, and both the RIFF/FORM
    shape -- magic + size + form-type, chunks at +12 -- and the bare shape --
    magic + size, chunks at +8, e.g. BFD's BFDC),
  * audio-indicative chunk tags (fmt / data / SSND / COMM / ...),
  * the payload entropy of the largest chunk (compressed vs raw).

It is deliberately conservative: it only reports a container when the grid
actually tiles (or the wrapper size matches) and the tags are printable, so
random or non-container data still falls through to "unrecognized." The chunk
list it produces is also the starting point for writing the real walker.
"""

import math
import os
import struct
from collections import Counter

from acidcat.core.walk.base import _f

_READ_CAP = 4 * 1024 * 1024
_MIN_CHUNKS = 2
_TILE_SLACK = 3

# chunk tags that strongly imply audio content
_AUDIO_TAGS = {"fmt ", "data", "DATA", "SSND", "COMM", "strm", "smpl", "fact",
               "wave", "WAVE", "CDDA", "frm8"}


def _printable4(t):
    return len(t) == 4 and all(0x20 <= c < 0x7F for c in t)


def _walk_grid(b, total, start, endian):
    """Walk [4-byte tag][u32 size] chunks from `start`. Uses declared sizes to
    jump (so a huge trailing payload needs only its header in the read window)
    and validates each against the real file size. Returns (chunks, tiled)."""
    pos, chunks = start, []
    while pos + 8 <= len(b) and len(chunks) < 256:
        tag = b[pos:pos + 4]
        size = struct.unpack_from(endian + "I", b, pos + 4)[0]
        end = pos + 8 + size
        if not _printable4(tag) or size <= 0 or end > total:
            break
        chunks.append((tag, pos, size))
        pos = end
    tiled = bool(chunks) and abs(pos - total) <= _TILE_SLACK
    return chunks, tiled


def _entropy(b):
    if not b:
        return 0.0
    c = Counter(b)
    n = len(b)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def generic_walk(filepath):
    """Return walker-shaped (label, chunks, warnings) for an unknown chunked
    container, or None if the bytes are not a recognizable container."""
    total = os.path.getsize(filepath)
    if total < 12:
        return None
    with open(filepath, "rb") as f:
        b = f.read(min(total, _READ_CAP))
    magic = b[:4]
    if not _printable4(magic):
        return None
    outer = struct.unpack_from(">I", b, 4)[0]
    outer_le = struct.unpack_from("<I", b, 4)[0]
    wrapper_ok = any(abs(o + 8 - total) <= _TILE_SLACK for o in (outer, outer_le))

    best = None                                        # (score, chunks, tiled, endian, start)
    for start in (8, 12):
        for endian, ename in ((">", "big"), ("<", "little")):
            chunks, tiled = _walk_grid(b, total, start, endian)
            if len(chunks) < _MIN_CHUNKS:
                continue
            score = len(chunks) + (5 if tiled else 0) + (3 if wrapper_ok else 0)
            if best is None or score > best[0]:
                best = (score, chunks, tiled, ename, start)
    if best is None:
        return None
    _score, chunks, tiled, endian, start = best
    if not (tiled or wrapper_ok):                      # too weak -- not a container
        return None

    tags = [t.decode("latin1") for t, _, _ in chunks]
    audio = [t for t in tags if t in _AUDIO_TAGS]
    biggest = max(chunks, key=lambda c: c[2])
    seg = b[biggest[1] + 8: biggest[1] + 8 + min(biggest[2], 65536)]
    H = _entropy(seg)
    payload = "compressed/encrypted" if H > 6.5 else "raw/structured"

    conf = 0.4 + (0.3 if tiled else 0.0) + (0.3 if audio else 0.0)
    verdict = ("likely an AUDIO container" if audio
               else "chunked container (contents unknown)")
    label = ("unknown chunked container (likely audio)" if audio
             else "unknown chunked container")

    header = {
        "id": magic.decode("latin1"), "offset": 0, "size": 8,
        "summary": f"{verdict} -- {endian}-endian chunk grid, "
                   f"{len(chunks)} chunk(s), payload {payload} (H={H:.2f}), "
                   f"confidence {min(conf, 0.99):.2f}",
        "fields": [
            _f(0x00, 4, "magic", magic.decode("latin1"), "unknown format signature"),
            _f(0x04, 4, "declared_size", outer if endian == "big" else outer_le,
               "outer size field" + (" (= file - 8)" if wrapper_ok else "")),
            _f(None, 0, "chunk_grid", f"{endian}-endian, chunks at +{start}",
               "tiles to EOF" if tiled else "wrapper size matches"),
            _f(None, 0, "audio_tags", ", ".join(audio) if audio else "(none)",
               "audio-indicative chunk tags present"),
        ],
        "warnings": [],
    }
    out = [header]
    for tag, off, size in chunks:
        t = tag.decode("latin1")
        out.append({
            "id": t, "offset": off, "size": size,
            "summary": ("audio data/format chunk" if tag.decode("latin1") in _AUDIO_TAGS
                        else "chunk"),
            "fields": [], "warnings": [],
        })
    warns = ["generic structural triage: no format-specific walker; "
             "chunk names and sizes are decoded, payloads are not"]
    return label, out, warns
