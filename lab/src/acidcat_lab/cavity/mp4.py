"""ISO-BMFF (MP4/M4A) mdat coverage-gap cavity: unreferenced bytes inside mdat.

An MP4/M4A file is a tree of boxes (size + FourCC + payload). Media samples live
in the `mdat` box; where each sample sits is described entirely by the sample
tables in `moov` (stco/co64 chunk offsets + stsz sample sizes + stsc). A decoder
only ever touches the byte ranges those tables point at. So any region of `mdat`
that no sample references is dead space the decoder never reads, a true cavity
INSIDE a top-level box (not a tail parasite past the last box, and not a spare
`free`/`skip` box, which every tool already knows to look at).

The clean way to make one without rewriting a single offset: append the payload
to the END of the `mdat` payload and grow only `mdat`'s own size field. The
existing samples do not move, so every stco offset stays correct; if `moov`
follows `mdat` it simply shifts down, and its internal offsets still point at the
unmoved samples. The file plays bit-identically; the appended bytes are a
coverage gap.

mdat hiding itself is documented (OpenPuff, videostego); what is under-tooled is
the coverage-gap framing, summing stsz sizes vs the mdat payload, and the
offset-free variant that grows only mdat's size so no chunk offset needs rewriting.
"""


import os
import struct


MAGIC = b"ACMP"                    # marker at the head of our cavity
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"edts"}


def _iter_boxes(buf, start, end):
    """Yield (fourcc, box_start, header_len, box_size) for boxes in [start,end)."""
    pos = start
    while pos + 8 <= end:
        size = struct.unpack_from(">I", buf, pos)[0]
        fourcc = buf[pos + 4:pos + 8]
        hdr = 8
        if size == 1:                              # 64-bit largesize
            size = struct.unpack_from(">Q", buf, pos + 8)[0]
            hdr = 16
        elif size == 0:                            # extends to end
            size = end - pos
        if size < hdr or pos + size > end:
            break
        yield fourcc, pos, hdr, size
        pos += size


def _find_top(buf, fourcc):
    for fc, pos, hdr, size in _iter_boxes(buf, 0, len(buf)):
        if fc == fourcc:
            return pos, hdr, size
    return None


def _walk_stsz(buf, start, end, out):
    """Collect total referenced sample bytes from every stsz/stz2 in the tree."""
    for fc, pos, hdr, size in _iter_boxes(buf, start, end):
        body = pos + hdr
        if fc in _CONTAINERS:
            _walk_stsz(buf, body, pos + size, out)
        elif fc == b"stsz":
            # version+flags(4), sample_size(4), sample_count(4), [sizes...]
            samp_size = struct.unpack_from(">I", buf, body + 4)[0]
            count = struct.unpack_from(">I", buf, body + 8)[0]
            if samp_size:
                out[0] += samp_size * count
            else:
                for i in range(count):
                    out[0] += struct.unpack_from(">I", buf, body + 12 + 4 * i)[0]


def referenced_bytes(buf):
    total = [0]
    _walk_stsz(buf, 0, len(buf), total)
    return total[0]


def embed(buf, payload):
    m = _find_top(buf, b"mdat")
    if not m:
        raise ValueError("no mdat box")
    pos, hdr, size = m
    if hdr != 8:
        raise ValueError("64-bit mdat not handled in this PoC")
    cavity = MAGIC + struct.pack(">I", len(payload)) + payload
    insert_at = pos + size                         # end of mdat payload
    new = bytearray(buf)
    new[insert_at:insert_at] = cavity
    struct.pack_into(">I", new, pos, size + len(cavity))   # grow mdat size field
    return bytes(new)


def extract(buf):
    m = _find_top(buf, b"mdat")
    if not m:
        return b""
    pos, hdr, size = m
    region = buf[pos + hdr:pos + size]
    i = region.rfind(MAGIC)                         # our cavity sits at the tail
    if i == -1:
        return b""
    n = struct.unpack_from(">I", region, i + 4)[0]
    return region[i + 8:i + 8 + n]


def analyze(buf):
    m = _find_top(buf, b"mdat")
    if not m:
        return ["no mdat box"]
    pos, hdr, size = m
    payload = size - hdr
    ref = referenced_bytes(buf)
    gap = payload - ref
    out = [f"mdat payload: {payload:,} bytes",
           f"referenced by samples (sum stsz): {ref:,} bytes",
           f"coverage gap: {gap:,} bytes "
           + ("(CAVITY)" if gap > 0 else "(none)")]
    region = buf[pos + hdr:pos + size]
    if MAGIC in region:
        out.append(f"  ^ contains our marker {MAGIC!r} at gap tail")
    return out
