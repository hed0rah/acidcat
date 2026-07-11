"""Offset-table repair for ISO-BMFF (MP4/M4A): rebuild a broken stco/co64
chunk-offset table from the witness the file already carries.

An stco/co64 entry is a *derived* field of the OFFSET kind: the absolute file
position of a run of samples in mdat. Its correct value is a function of where
mdat actually sits plus the sample sizes (stsz) and the sample-to-chunk map
(stsc) -- so a table that points at the wrong place (the classic result of a
re-mux or a metadata insertion that moved mdat without patching the table, which
a player experiences as silence or a crash) can be rebuilt from first principles,
independent of the wrong stored values. That independent witness is what makes
this a repair and not a guess.

Scope, deliberately conservative for a first offset-kind repairer:
  * single media track (one stco/co64, one stsz, one stsc, one mdat). Multi-track
    files interleave chunks in mdat, so per-track contiguous layout does not hold;
    those are left untouched.
  * the repair fires only when the stored table is actually broken (an entry
    points outside mdat), so a healthy file is never rewritten.
  * the rebuilt table must fit inside mdat and account for no more bytes than mdat
    holds, or the repair is refused (the witness did not check out).

The patch is length-preserving -- it overwrites offset fields in place and never
moves a byte of mdat -- so it is even safer than a size-cascade rewrite.
"""

import struct

from acidcat.core import mp4 as mp4mod


class Mp4RepairError(ValueError):
    """The offset tables could not be repaired with confidence."""


def _read_fullbox_u32s(data, payload_off, count_off):
    """Read (entry_count, [u32 ...]) from a FullBox table whose count sits at
    ``count_off`` from the payload and whose u32 entries follow it."""
    count = struct.unpack_from(">I", data, payload_off + count_off)[0]
    base = payload_off + count_off + 4
    return count, base


def _parse_stsz(data, box):
    """Sample sizes: (sample_size, sample_count, [sizes] or None)."""
    p = box["offset"] + box["hdr"]
    sample_size = struct.unpack_from(">I", data, p + 4)[0]
    sample_count = struct.unpack_from(">I", data, p + 8)[0]
    if sample_size != 0:
        return sample_size, sample_count, None
    sizes = list(struct.unpack_from(">%dI" % sample_count, data, p + 12))
    return 0, sample_count, sizes


def _parse_stsc(data, box):
    """Sample-to-chunk runs: list of (first_chunk, samples_per_chunk)."""
    p = box["offset"] + box["hdr"]
    n = struct.unpack_from(">I", data, p + 4)[0]
    runs = []
    for i in range(n):
        fc, spc, _ = struct.unpack_from(">III", data, p + 8 + i * 12)
        runs.append((fc, spc))
    return runs


def _parse_stco(data, box):
    """Chunk offsets: (is64, entry_count, entries_base_off, [values])."""
    p = box["offset"] + box["hdr"]
    is64 = box["type"] == b"co64"
    n = struct.unpack_from(">I", data, p + 4)[0]
    base = p + 8
    fmt = ">%d%s" % (n, "Q" if is64 else "I")
    values = list(struct.unpack_from(fmt, data, base))
    return is64, n, base, values


def _samples_per_chunk(runs, nchunks):
    """Expand the run-length stsc into a per-chunk (1-based) sample count."""
    spc = [0] * (nchunks + 1)
    for i, (first_chunk, per) in enumerate(runs):
        last_chunk = runs[i + 1][0] - 1 if i + 1 < len(runs) else nchunks
        for c in range(first_chunk, last_chunk + 1):
            if 1 <= c <= nchunks:
                spc[c] = per
    return spc


def _chunk_byte_sizes(spc, sample_size, sizes, nchunks):
    """Bytes in each chunk (1-based), summing its samples' sizes. Raises if the
    sample tables are internally inconsistent."""
    out = [0] * (nchunks + 1)
    idx = 0
    for c in range(1, nchunks + 1):
        n = spc[c]
        if sizes is None:
            out[c] = n * sample_size
            idx += n
        else:
            if idx + n > len(sizes):
                raise Mp4RepairError("stsc references more samples than stsz lists")
            out[c] = sum(sizes[idx:idx + n])
            idx += n
    return out


def _find_boxes(data):
    """Locate the single mdat and the one stco/co64, stsz, stsc. Returns a dict
    or raises Mp4RepairError when the file is out of scope (multi-track, etc.)."""
    mdat = stsz = stsc = None
    stco = []
    for b in mp4mod.iter_boxes(data):
        t = b["type"]
        if b["truncated"]:
            continue
        if t == b"mdat" and mdat is None:
            mdat = b
        elif t in (b"stco", b"co64"):
            stco.append(b)
        elif t == b"stsz":
            stsz = b if stsz is None else "multi"
        elif t == b"stsc":
            stsc = b if stsc is None else "multi"
    if mdat is None:
        raise Mp4RepairError("no mdat box")
    if len(stco) != 1 or stsz in (None, "multi") or stsc in (None, "multi"):
        raise Mp4RepairError("not a single-track layout (offset repair is "
                             "conservative and only handles one media track)")
    return {"mdat": mdat, "stco": stco[0], "stsz": stsz, "stsc": stsc}


def repair_mp4(data):
    """Rebuild a broken chunk-offset table. Returns (new_bytes, changes) where
    changes is a list of ``{path, field, old, new}``; an empty list means the
    table was already valid. Raises Mp4RepairError when out of scope or when the
    rebuilt table cannot be confidently witnessed."""
    boxes = _find_boxes(data)
    mdat = boxes["mdat"]
    mdat_start = mdat["offset"] + mdat["hdr"]
    mdat_end = mdat["offset"] + mdat["size"]

    is64, nchunks, entries_base, stored = _parse_stco(data, boxes["stco"])
    sample_size, sample_count, sizes = _parse_stsz(data, boxes["stsz"])
    runs = _parse_stsc(data, boxes["stsc"])
    spc = _samples_per_chunk(runs, nchunks)
    chunk_bytes = _chunk_byte_sizes(spc, sample_size, sizes, nchunks)

    total = sum(chunk_bytes[1:])
    if total > mdat_end - mdat_start:
        raise Mp4RepairError("sample tables describe more data than mdat holds")

    # the table is broken only if a stored entry lands outside mdat, or the chunk
    # it points at would overrun mdat. a healthy (possibly padded) layout is left
    # exactly as found -- no false positives.
    def _broken(offs):
        for c in range(1, nchunks + 1):
            o = offs[c - 1]
            if o < mdat_start or o + chunk_bytes[c] > mdat_end:
                return True
        return False

    if not _broken(stored):
        return data, []

    # rebuild from the witness: chunks laid out contiguously from mdat's payload
    rebuilt = []
    cur = mdat_start
    for c in range(1, nchunks + 1):
        rebuilt.append(cur)
        cur += chunk_bytes[c]
    if _broken(rebuilt):                      # the witness itself must check out
        raise Mp4RepairError("rebuilt table does not fit mdat; layout not "
                             "contiguous-from-start (out of scope)")

    out = bytearray(data)
    step = 8 if is64 else 4
    fmt = ">Q" if is64 else ">I"
    changed = 0
    for i, (old, new) in enumerate(zip(stored, rebuilt)):
        if old != new:
            struct.pack_into(fmt, out, entries_base + i * step, new)
            changed += 1
    box_id = "co64" if is64 else "stco"
    changes = [{"path": box_id, "field": "chunk_offsets",
                "old": f"{changed} of {nchunks} wrong",
                "new": f"rebuilt from mdat @ 0x{mdat_start:08x}"}]
    return bytes(out), changes
