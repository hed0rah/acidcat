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
from acidcat.core import framescan
from acidcat.core.sniff import sniff_bytes

MODES = ("strict", "normal", "aggressive")

_HEADER_BACKTRACK = 64 * 1024     # "back up a little" for a corrupt-extent header
_NORMAL_BLOB_MIN = 0.45           # headerless blobs need this confidence in 'normal'
_CONTAINER_CONF = 0.9             # a validated container is a strong recovery
_COALESCE_GAP = 32 * 1024        # merge headerless blob fragments within this gap
                                 # (a quiet passage inside a file is still one file)

# container magics whose payload can be audio; each is confirmed with sniff_bytes
# so a stray "RIFF" in noise is rejected, not trusted.
_CONTAINER_MAGICS = (b"RIFF", b"RF64", b"FORM", b"fLaC", b"OggS", b"ID3")
_AUDIO_CONTAINER_FMTS = {"wav", "rf64", "aiff", "aifc", "8svx", "flac", "ogg",
                         "sf2", "mp3"}


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
    if fmt == "mp3":
        # ID3v2-anchored only (bare frame-sync is too noisy to sweep on): a real
        # version byte and a synchsafe (7-bit) size are hard to hit by chance
        if off + 10 > n or data[off:off + 3] != b"ID3":
            return False
        return (data[off + 3] in (2, 3, 4) and data[off + 4] != 0xFF
                and all(data[off + 6 + k] < 0x80 for k in range(4)))
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


# declared-size formats: an absent extent means the size field itself is corrupt
_DECLARED_SIZE_FMTS = {"wav", "rf64", "sf2", "aiff", "aifc", "8svx"}
_HEADER_SLACK = 4096              # audio may start this far past a container header
_AUDIO_GAP_TOL = 4096            # bridge small non-audio gaps between audio sub-regions


def signature_sweep(data):
    """Find every validated audio container by magic (the PhotoRec engine).
    Returns container records sorted by offset, each with an ``extent`` (a
    trustworthy declared end, or None when the size is streaming/corrupt and must
    be resolved from the audio itself)."""
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
    return [hits[o] for o in sorted(hits)]


def _audio_chain_end(offset, upper, regions):
    """End of the contiguous audio run that begins just after a container header,
    or None if no audio starts near the header (a 16-bit or compressed payload
    the statistical pass can't see). Bounds a corrupt extent to the real audio."""
    chain_end = None
    for r in regions:
        s, e = r["start"], r["end"]
        if s >= upper:
            break
        if chain_end is None:
            if offset <= s <= offset + _HEADER_SLACK:
                chain_end = min(e, upper)
        elif s - chain_end <= _AUDIO_GAP_TOL:
            chain_end = min(e, upper)
        elif s > chain_end:
            break
    return chain_end


def _next_region_start(lo, upper, regions):
    """Start of the first audio region in (lo, upper), else None."""
    for r in regions:
        if lo < r["start"] < upper:
            return r["start"]
    return None


def _resolve_container_ends(data, containers, regions):
    """Fill each container's ``end``. A trusted declared extent is used as-is. A
    provisional extent is bounded by the audio that follows the header; failing
    that (undetectable payload) it is capped just before the next distinct audio
    region so a following blob survives, else at the next container / EOF."""
    offsets = [c["offset"] for c in containers]
    n = len(data)
    for i, c in enumerate(containers):
        extent = c.pop("extent")
        upper = offsets[i + 1] if i + 1 < len(offsets) else n
        if extent is not None:
            c["end"], c["streaming_extent"] = extent, False
            continue
        chain = _audio_chain_end(c["offset"], upper, regions)
        if chain is not None:
            c["end"] = chain
        else:
            nxt = _next_region_start(c["offset"] + _HEADER_SLACK, upper, regions)
            c["end"] = nxt if nxt is not None else upper
        c["streaming_extent"] = True
        if c["format"] in _DECLARED_SIZE_FMTS:
            c["corrupt_extent"] = True                 # declared size was unusable


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
    if rec["kind"] == "stream":                          # structural, like a container
        return True
    if mode == "aggressive":
        return True
    if mode == "strict":
        return rec["kind"] == "container"
    return rec["kind"] == "container" or rec["confidence"] >= _NORMAL_BLOB_MIN


def locate(data, *, mode="normal", scan_kwargs=None):
    """Locate, anchor, and classify audio regions at a forensics level.
    Returns records (offset/end/length + kind + confidence + evidence) sorted by
    offset, each ready to hand to `carve`. Never raises on content."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    containers = signature_sweep(data)
    # third engine: headerless compressed streams by frame-sync cadence (fast +
    # structural, so it runs in every mode, like the signature sweep).
    streams = framescan.find_mpeg_streams(data)
    # strict = validated structure only, so skip the (slow) statistical pass
    # entirely -- a signature-only run is fast even on a multi-hundred-MB image.
    regions = [] if mode == "strict" else audioscan.scan(data, **(scan_kwargs or {}))
    _resolve_container_ends(data, containers, regions)
    extents = [(c["offset"], c["end"]) for c in containers if c["end"] > c["offset"]]
    # a stream inside a container we already found is that file's payload, not new
    extents += [(s["offset"], s["end"]) for s in streams]
    records = list(containers) + [s for s in streams
                                  if not _within(s["offset"], [(c["offset"], c["end"])
                                                               for c in containers
                                                               if c["end"] > c["offset"]])]

    for region in regions:
        if _within(region["start"], extents):
            continue                                  # part of a container/stream we found
        records.append({
            "kind": "blob", "format": None, "offset": region["start"],
            "end": region["end"], "confidence": region["confidence"],
            "inspectable": False, "evidence": region["evidence"],
        })

    records = [r for r in records if _survives(r, mode)]
    records.sort(key=lambda r: r["offset"])
    records = _coalesce_blobs(records)
    for r in records:
        r["length"] = r["end"] - r["offset"]
    return records


def _coalesce_blobs(records):
    """Merge adjacent headerless-blob records within _COALESCE_GAP. Dynamic audio
    (a music dump with quiet passages) fragments into many below-gate windows;
    headerless recovery is inherently coarse, so nearby blob fragments collapse
    to one region. Containers are never merged, and a container between two blobs
    keeps them separate."""
    out = []
    for r in records:
        if r["kind"] == "blob" and out and out[-1]["kind"] == "blob" \
                and r["offset"] - out[-1]["end"] <= _COALESCE_GAP:
            prev = out[-1]
            prev["end"] = max(prev["end"], r["end"])
            prev["confidence"] = max(prev["confidence"], r["confidence"])
            continue
        out.append(r)
    return out
