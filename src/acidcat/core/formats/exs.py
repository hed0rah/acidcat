"""Logic EXS24 sampler instruments (.exs).

A flat list of chunks, each an 84-byte header and then its data:

    +0x00  u32   signature; byte 3 is the chunk type (low nibble) and flags
    +0x04  u32   data length
    +0x08  u32   id
    +0x0c  u32   flags, not decoded
    +0x10  4cc   'TBOS' (little-endian file) or 'SOBT' (big-endian)
    +0x14  char  name, 64 bytes, NUL-padded
    +0x54        data

Types: 0 instrument, 1 zone, 2 group, 3 sample, 4 parameters, 8 unknown (a
four-byte chunk). Big-endian files are the PowerPC-era Logic ones.

    instrument  +4 zones, +8 groups, +12 samples, +16 parameter chunks (u32)
    zone        +1 root key, +2 fine tune (s8), +3 pan (s8), +4 volume (s8),
                +6/+7 key range, +9/+10 velocity range, +12 sample start,
                +16 sample end, +20 loop start, +24 loop end (u32),
                +88 group index (s32, -1 none), +92 sample index (s32)
    sample      +0 data offset, +4 frames, +8 rate, +12 bits, +16 channels,
                +28 file type (4cc byte-reversed: 'EVAW' is WAVE), +32 file
                size, +80 path (256 bytes), +336 file name (256 bytes, in the
                592- and 600-byte forms)

Measured, not taken from a published spec: every one of 1,007 real files
walked to exactly end of file; the instrument counts matched the chunks in all
of them; and on 368 instruments shipped beside an SFZ of the same instrument
the zone fields matched the SFZ opcodes (every field of the 104-, 136- and
140-byte zones; the 148-byte zones where the converter kept region order,
and loop end one past the SFZ's inclusive one).
"""

import struct

MAGICS = {b"TBOS": "<", b"SOBT": ">"}
HEAD = 0x54
NAME_AT, NAME_LEN = 0x14, 64

TYPES = {0: "instrument", 1: "zone", 2: "group", 3: "sample", 4: "parameters"}

ZONE_MIN = 96           # the shortest zone that reaches the sample index
SAMPLE_MIN = 336


def endian(head):
    """'<' or '>' for an EXS24 file, else None. The first chunk must be the
    instrument, so the magic alone is not the whole test."""
    if len(head) < 0x14:
        return None
    e = MAGICS.get(bytes(head[0x10:0x14]))
    if e is None or head[3] & 0x0F != 0:
        return None
    return e


def chunk_type(head):
    return head[3] & 0x0F


def cstr(b):
    return bytes(b).split(b"\0", 1)[0].decode("utf-8", "replace")


def read_chunks(data, e, max_chunks):
    """(chunks, end, stop). Each chunk is {type, at, length, name}; stop is
    None at end of file, 'chunks' at `max_chunks`, or 'overrun'."""
    out = []
    pos = 0
    n = len(data)
    while pos + HEAD <= n:
        if len(out) >= max_chunks:
            return out, pos, "chunks"
        length = struct.unpack_from(e + "I", data, pos + 4)[0]
        if pos + HEAD + length > n:
            return out, pos, "overrun"
        out.append({"type": chunk_type(data[pos:pos + 4]), "at": pos,
                    "length": length,
                    "name": cstr(data[pos + NAME_AT:pos + NAME_AT + NAME_LEN])})
        pos += HEAD + length
    return out, pos, None if pos == n else "short"


def note_name(n):
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[n % 12]}{n // 12 - 2}"
