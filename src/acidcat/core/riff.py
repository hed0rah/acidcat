"""
RIFF/WAVE chunk primitives.

The lenient traversal (iter_chunks / iter_spans) that the WAV walker and
the grammar strategy consume, container info, and the acid/smpl field
vetting helpers. Chunk field decoding lives in core/walk/wav.py.
"""

import os
import struct
from collections import namedtuple

# cap on payload bytes read per chunk (a forged size cannot force an unbounded
# allocation); the declared size is still reported in full.
PAYLOAD_CAP = 65536

# one traversed RIFF/WAVE region. size is the DECLARED chunk size (never
# clamped); payload is capped at PAYLOAD_CAP and may be short at EOF;
# payload_base is the absolute offset field offsets are measured from (offset+8).
Span = namedtuple("Span", "id offset payload_base payload size")


def iter_chunks(filepath):
    """
    Yield (chunk_id_str, offset, size) for each chunk in a RIFF/WAVE file.

    Lightweight iterator -- doesn't parse chunk contents.
    """
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        hdr = f.read(12)
        if len(hdr) < 12 or hdr[0:4] != b"RIFF" or hdr[8:12] != b"WAVE":
            return
        pos = 12
        while pos + 8 <= size:
            f.seek(pos)
            ch = f.read(8)
            if len(ch) < 8:
                break
            cid = ch[0:4].decode("ascii", errors="ignore")
            try:
                csz = struct.unpack("<I", ch[4:8])[0]
            except struct.error:
                break
            yield (cid, pos, csz)
            pos += 8 + csz
            if csz % 2 == 1:
                pos += 1


def iter_spans(filepath):
    """Lenient RIFF/WAVE traversal, the single source both the walker and the
    grammar strategy consume. Returns ``(spans, warnings)``. Enumerates via
    ``iter_chunks`` so the chunk-walk arithmetic has exactly one home, and adds
    the payload read plus the traversal warnings (riff_size mismatch, chunk
    overrun) in the walker's exact wording. Degrades, never raises.
    """
    file_size = os.path.getsize(filepath)
    spans, warns = [], []
    with open(filepath, "rb") as f:
        hdr = f.read(12)
        if len(hdr) < 12:
            return [], [f"file is {len(hdr)} bytes; a RIFF header needs 12"]
        if hdr[0:4] != b"RIFF" or hdr[8:12] != b"WAVE":
            return [], ["not a RIFF/WAVE container"]
        riff_size = struct.unpack("<I", hdr[4:8])[0]
        if riff_size + 8 != file_size:
            warns.append(
                f"riff_size says {riff_size + 8:,} bytes, file is "
                f"{file_size:,} ({file_size - riff_size - 8:+,})"
            )
        for cid, offset, size in iter_chunks(filepath):
            avail = max(0, file_size - offset - 8)
            if size > avail:
                warns.append(
                    f"chunk {cid!r} at 0x{offset:08x} claims {size:,} bytes "
                    f"but only {avail:,} remain"
                )
            f.seek(offset + 8)
            payload = f.read(min(size, PAYLOAD_CAP))
            spans.append(Span(cid, offset, offset + 8, payload, size))
    return spans, warns


def smpl_root_or_none(meta):
    """Coerce the SMPL `smpl_root_key` field to None when it is the
    documented "unset" sentinel value 0 (MIDI note C-1, which no
    legitimate sample chunk actually uses as its root). Returns the
    integer MIDI note for any non-zero value, or None.

    Use this at every call site that downstreams `smpl_root_key` into
    a key/root display. Without it the scan CSV and any future caller
    will ship `C-1` for files whose SMPL chunk is present but unset.
    """
    val = meta.get("smpl_root_key") if hasattr(meta, "get") else meta
    return val if val else None


def acid_root_or_none(meta):
    """Companion to `smpl_root_or_none` for the ACID `acid_root_note`
    field. Same zero-as-sentinel convention.
    """
    val = meta.get("acid_root_note") if hasattr(meta, "get") else meta
    return val if val else None


def effective_acid_beats(meta, duration):
    """Vet the acid num_beats field against the one-shot flag and the
    file's actual duration.

    Field measurement (2026-06-11, 400 ACIDized files): with the
    one-shot bit clear, num_beats reconciles with duration*tempo/60
    in ~93% of files. With the bit set it is a coin flip: batch
    taggers leave boilerplate 8-beat/120-bpm values in true
    one-shots, while some vendors set the bit on real loops that
    carry accurate beat counts. So: trust beats when the flag is
    clear; when it is set, keep beats only if they reconcile with
    the actual duration within 15%.
    """
    beats = meta.get("acid_beats")
    if not beats:
        return None
    if not meta.get("acid_one_shot"):
        return beats
    bpm = meta.get("bpm")
    if bpm and duration:
        expected = beats / bpm * 60
        if abs(expected - duration) / duration < 0.15:
            return beats
    return None


def get_riff_info(filepath):
    """Return RIFF container size and type string, or None if not RIFF."""
    with open(filepath, "rb") as f:
        hdr = f.read(12)
        if len(hdr) < 12 or hdr[0:4] != b"RIFF":
            return None
        riff_size = struct.unpack("<I", hdr[4:8])[0]
        riff_type = hdr[8:12].decode("ascii", errors="ignore")
        return {"size": riff_size, "type": riff_type}
