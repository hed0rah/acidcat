"""Plant a payload in a RIFF JUNK chunk.

A JUNK chunk is spec'd as ignorable padding, and conformant readers skip it. In
the RF64/BW64 world (EBU TECH 3306) it is specifically the reserved placeholder
that becomes a ds64 chunk once a file passes 4 GB, so until then its content is
free bytes. A plain WAV carrying a JUNK chunk full of non-zero data plays
normally everywhere while smuggling a payload INSIDE the container -- not
appended after it. The RIFF size field stays honest, nothing is malformed, and
no ordinary reader has any reason to complain.

That is what makes it worth building: it is the clean case of a hiding spot the
format itself sanctions. `acidcat cavity` finds it by accounting for every byte
rather than by looking for damage, and the pair of them is tested together in
tests/test_lab_loop.py -- this plants, that finds, and CI fails if it does
not.
"""


import struct

MAGIC = b"ACJK"                     # marker inside the JUNK chunk so extract finds ours


def _iter_chunks(wav):
    """Yield (cid, start_of_header, data_offset, size) for each RIFF chunk."""
    pos = 12
    while pos + 8 <= len(wav):
        cid = wav[pos:pos + 4]
        size = struct.unpack_from("<I", wav, pos + 4)[0]
        yield cid, pos, pos + 8, size
        pos += 8 + size + (size & 1)


def embed(wav, payload):
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    body = MAGIC + payload
    junk = b"JUNK" + struct.pack("<I", len(body)) + body
    if len(junk) & 1:                                    # RIFF chunks pad to even
        junk += b"\x00"
    # splice the JUNK chunk in right after the "WAVE" tag (before the first chunk)
    out = bytearray(wav)
    out[12:12] = junk
    riff_size = struct.unpack_from("<I", out, 4)[0] + len(junk)
    struct.pack_into("<I", out, 4, riff_size)
    return bytes(out)


def extract(wav):
    for cid, _hdr, doff, size in _iter_chunks(wav):
        if cid in (b"JUNK", b"PAD ") and wav[doff:doff + 4] == MAGIC:
            return wav[doff + 4:doff + size]
    return b""


def analyze(wav):
    out = []
    for cid, hdr, doff, size in _iter_chunks(wav):
        if cid in (b"JUNK", b"PAD "):
            blob = wav[doff:doff + size]
            nonzero = any(blob)
            out.append(f"{cid.decode('latin-1')} at 0x{hdr:04x}: {size} bytes, "
                       f"{'NON-ZERO (cavity)' if nonzero else 'all zero (padding)'}")
    return out or ["no JUNK/PAD chunks found"]
