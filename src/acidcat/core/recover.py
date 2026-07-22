"""Forensic recovery orchestration for `scan` -- the two engines, phases 2 and 3.

Two independent engines find audio, the way `scan` is "PhotoRec for audio":

  * SIGNATURE sweep -- scan forward for validated container magics (RIFF/WAVE,
    FORM/AIFF/8SVX, fLaC, OggS...). Finds real files even when their PCM is
    16-bit or compressed and the statistical detector can't see it. This is the
    PhotoRec half.
  * STATISTICAL pass -- core/audioscan locates signatureless raw PCM by its
    structure. This is the half PhotoRec cannot do.

A statistical hit that falls inside a container's real extent is part of that
file, not a separate find. A hit that doesn't is a headerless blob -- but before
calling it headerless we back up *a little* (64 KiB) for a container header whose
size field was corrupt so the sweep's extent missed it. That is the governing
rule: **a missing header never discards a hit**, it only downgrades a
"container" recovery to a "blob" recovery.

The forensics level decides what survives:

    strict      only validated containers.
    normal      containers, plus high-confidence headerless blobs.
    aggressive  every candidate -- best-effort carving of raw/unknown/corrupt.

Each record carries offset/end/length + kind + confidence + evidence, shaped to
feed `carve` (whose output feeds `convert` or `inspect`). Nothing here writes
bytes or walks a container -- that is the next stage in the pipe.
"""

import struct

from acidcat.core import audioscan
from acidcat.core.sniff import sniff_bytes

MODES = ("strict", "normal", "aggressive")

_HEADER_BACKTRACK = 64 * 1024     # "back up a little" for a corrupt-extent header
_NORMAL_BLOB_MIN = 0.45           # headerless blobs need this confidence in 'normal'
_CONTAINER_CONF = 0.9             # a validated container is a strong recovery

# container magics whose payload can be audio; each is confirmed with sniff_bytes
# so a stray "RIFF" in noise is rejected, not trusted.
_CONTAINER_MAGICS = (b"RIFF", b"RF64", b"FORM", b"fLaC", b"OggS")
_AUDIO_CONTAINER_FMTS = {"wav", "rf64", "aiff", "aifc", "8svx", "flac", "ogg", "sf2"}


_RIFF_MIN = 12                    # a RIFF/FORM smaller than its own header is corrupt


def _confirm_container(data, off, fmt):
    """Structural confirmation beyond the leading magic. RIFF/FORM are already
    validated by sniff_bytes (WAVE / form-type at +8). fLaC and OggS are only a
    4-byte magic there, weak enough to fire on chance or on the literal bytes in
    text/code, so confirm their first structure byte."""
    n = len(data)
    if fmt == "flac":
        # first block after 'fLaC' is STREAMINFO: type 0, always 34 bytes long
        if off + 8 > n or (data[off + 4] & 0x7F) != 0:
            return False
        block_len = struct.unpack_from(">I", b"\x00" + data[off + 5:off + 8], 0)[0]
        return block_len == 34
    if fmt == "ogg":
        # OggS page: stream-structure version 0 at +4, header_type uses 3 bits
        return off + 6 <= n and data[off + 4] == 0 and (data[off + 5] & 0xF8) == 0
    return True                                               # riff/form: sniff did it


def _container_extent(data, off, fmt):
    """End offset of a container at `off`, or None when it can't be trusted.
    RIFF/FORM carry a declared size; a size that is zero, sub-header, or runs
    past EOF is treated as CORRUPT (None) so recovery falls back to a provisional
    extent rather than a stub. Streaming formats (flac/ogg) also return None."""
    n = len(data)
    size = None
    if fmt in ("wav", "rf64", "sf2") and off + 8 <= n:
        size = struct.unpack_from("<I", data, off + 4)[0]     # RIFF: little-endian
    elif fmt in ("aiff", "aifc", "8svx") and off + 8 <= n:
        size = struct.unpack_from(">I", data, off + 4)[0]     # IFF/FORM: big-endian
    if size is None:
        return None                                           # flac/ogg: streaming
    end = off + 8 + size
    if size < _RIFF_MIN or end > n:
        return None                                           # corrupt declared size
    return end


def signature_sweep(data):
    """Find every validated audio container by magic (the PhotoRec engine).
    Returns container records sorted by offset. A container with no trustworthy
    declared size (streaming, or a corrupt size field) gets a provisional extent
    running to the next container start or EOF -- never a zero-length stub."""
    hits = {}
    for magic in _CONTAINER_MAGICS:
        idx = data.find(magic)
        while idx != -1:
            fmt = sniff_bytes(bytes(data[idx:idx + 20]))
            if fmt in _AUDIO_CONTAINER_FMTS and idx not in hits \
                    and _confirm_container(data, idx, fmt):
                hits[idx] = {
                    "kind": "container", "format": fmt, "offset": idx,
                    "extent": _container_extent(data, idx, fmt),
                    "confidence": _CONTAINER_CONF, "inspectable": True,
                    "evidence": None,
                }
            idx = data.find(magic, idx + 1)

    offsets = sorted(hits)
    records = []
    for i, off in enumerate(offsets):
        rec = hits[off]
        extent = rec.pop("extent")
        if extent is not None:
            rec["end"] = extent
            rec["streaming_extent"] = False
        else:
            # provisional: run to the next container start, or EOF
            rec["end"] = offsets[i + 1] if i + 1 < len(offsets) else len(data)
            rec["streaming_extent"] = True
        records.append(rec)
    return records


def backtrack_header(data, start, bound=_HEADER_BACKTRACK):
    """Scan backward a bounded distance from a region start for the nearest
    validated container header (the corrupt-extent rescue). Returns
    {found, format, container_start, distance} or {found: False}."""
    lo = max(0, start - bound)
    best_off, best_fmt = -1, None
    for magic in _CONTAINER_MAGICS:
        off = data.rfind(magic, lo, start + 4)
        if off > best_off:
            fmt = sniff_bytes(bytes(data[off:off + 20]))
            if fmt in _AUDIO_CONTAINER_FMTS and _confirm_container(data, off, fmt):
                best_off, best_fmt = off, fmt
    if best_off < 0:
        return {"found": False}
    return {"found": True, "format": best_fmt, "container_start": best_off,
            "distance": start - best_off}


def _within(offset, extents):
    """True if `offset` sits inside any [start, end) container extent."""
    for start, end in extents:
        if start <= offset < end:
            return True
    return False


def _survives(rec, mode):
    if mode == "aggressive":
        return True
    if mode == "strict":
        return rec["kind"] == "container"
    return rec["kind"] == "container" or rec["confidence"] >= _NORMAL_BLOB_MIN


def recover(data, *, mode="normal", scan_kwargs=None):
    """Locate, anchor, and classify audio recoveries at a forensics level.
    Returns records (offset/end/length + kind + confidence + evidence) sorted by
    offset, each ready to hand to `carve`. Never raises on content."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    containers = signature_sweep(data)
    extents = [(c["offset"], c["end"]) for c in containers if c["end"] > c["offset"]]
    records = list(containers)

    for region in audioscan.scan(data, **(scan_kwargs or {})):
        if _within(region["start"], extents):
            continue                                  # part of a container we found
        bt = backtrack_header(data, region["start"])
        if bt["found"] and not _within(bt["container_start"], extents):
            # a header sits just behind this PCM but its declared extent missed it
            # (corrupt/short size): anchor a container recovery rather than drop it
            records.append({
                "kind": "container", "format": bt["format"],
                "offset": bt["container_start"], "end": region["end"],
                "streaming_extent": True, "confidence": _CONTAINER_CONF,
                "inspectable": True, "evidence": region["evidence"],
                "corrupt_extent": True,
            })
        else:
            records.append({
                "kind": "blob", "format": None, "offset": region["start"],
                "end": region["end"], "confidence": region["confidence"],
                "inspectable": False, "evidence": region["evidence"],
            })

    records = [r for r in records if _survives(r, mode)]
    records.sort(key=lambda r: r["offset"])
    for r in records:
        r["length"] = r["end"] - r["offset"]
    return records
