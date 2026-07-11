"""COUNT-kind repair for RIFF: clamp a table-count field that claims more
records than the payload can physically hold.

A COUNT field says how many fixed-size records a chunk contains, and its correct
value is a function of the payload: how many records actually fit. The witness is
payload capacity. When a chunk declares more records than `4 + n*record` (cue) or
`36 + n*record` (smpl loops) can hold, a reader that trusts the count walks off
the end of the chunk -- so the count is clamped down to what fits. Only the
over-capacity direction is repaired: a count *smaller* than capacity is left alone
(a chunk may legitimately carry trailing padding or sampler-specific data).

Length-preserving -- it overwrites the 4-byte count field in place and never
touches audio. Currently WAV/RF64 `cue ` and `smpl`.
"""

import struct

# chunk id -> (count field offset in payload, fixed header before records,
# record size). capacity = (payload_size - header) // record.
_COUNTED = {
    b"cue ": (0, 4, 24),          # num_cue_points, then 24-byte cue points
    b"smpl": (28, 36, 24),        # num_sample_loops @28, 36-byte header, 24-byte loops
}


def is_target(data):
    return len(data) >= 12 and data[:4] in (b"RIFF", b"RF64") \
        and data[8:12] == b"WAVE"


def _riff_chunks(data):
    """Yield (id, payload_offset, payload_size) for top-level RIFF chunks."""
    pos = 12
    n = len(data)
    while pos + 8 <= n:
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if pos + 8 + size > n:
            break
        yield cid, pos + 8, size
        pos += 8 + size + (size & 1)


def _violations(data):
    """List of (count_field_abs_offset, cid, declared, capacity) for chunks whose
    count exceeds capacity."""
    out = []
    for cid, poff, psize in _riff_chunks(data):
        spec = _COUNTED.get(cid)
        if not spec:
            continue
        coff, header, rec = spec
        if psize < coff + 4:
            continue
        declared = struct.unpack_from("<I", data, poff + coff)[0]
        capacity = max(0, (psize - header) // rec)
        if declared > capacity:
            out.append((poff + coff, cid, declared, capacity))
    return out


def analyze(data):
    """Read-only: ``[{path, field, old, new, kind, witness}]`` for over-capacity
    counts."""
    out = []
    for _off, cid, declared, capacity in _violations(data):
        name = cid.decode("latin-1").strip()
        out.append({
            "path": name,
            "field": "num_cue_points" if cid == b"cue " else "num_sample_loops",
            "old": declared, "new": capacity, "kind": "count",
            "witness": f"payload holds {capacity} record(s)",
        })
    return out


def repair(data):
    """Return (new_bytes, changes) with over-capacity counts clamped to what the
    payload holds. Length-preserving."""
    vios = _violations(data)
    if not vios:
        return data, []
    out = bytearray(data)
    for off, _cid, _declared, capacity in vios:
        struct.pack_into("<I", out, off, capacity)
    return bytes(out), analyze(data)
