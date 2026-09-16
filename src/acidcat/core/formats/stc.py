"""STC: a ZX Spectrum Sound Tracker module, as the ST Song Compiler wrote it.

The oldest of the Spectrum AY trackers still in wide use, and the one PT2
and PT3 descend from. No magic. A 27-byte header of pointers, then four
blocks in a fixed order, then the channel streams:

    0x00    1   delay (ticks per row)
    0x01    2   pointer to the positions block
    0x03    2   pointer to the ornaments block
    0x05    2   pointer to the pattern table
    0x07   18   identifier, usually "SONG BY ST COMPILE"
    0x19    2   size of the module
    0x1B   ...  samples, 99 bytes each: number, 32 rows of 3 bytes,
                repeat position, repeat length; up to the positions block
    pos     ...  count - 1, then (pattern, transposition) pairs
    orn     ...  ornaments, 33 bytes each: number, 32 semitone offsets
    (gap)   ...  sometimes a line of text the compiler left here
    pat     ...  pattern table, 7 bytes each: number, three pointers to
                the channel streams; ended by 0xFF
    ...          channel streams, sized by the next pointer

Every pointer is an absolute offset. Records carry their own number, so
sample 9 can follow sample 7 with nothing between. Some compilers put
the ornaments BEFORE the positions block; both orders are read, and each
block ends where the next begins. Identification is arithmetic: the
samples come first and are a whole number of records, the positions
block ends exactly at the block after it, the pattern table comes last
and ends at 0xFF, and every stream pointer lands past it.

Spec: the ST Song Compiler's own layout as read by zxtune and ayfly.
"""

import struct

HEADER = 27
DELAY_AT = 0x00
POSITIONS_PTR_AT = 0x01
ORNAMENTS_PTR_AT = 0x03
PATTERNS_PTR_AT = 0x05
IDENT_AT, IDENT_LEN = 0x07, 18
SIZE_AT = 0x19
SAMPLE_RECORD = 99
SAMPLE_ROWS, SAMPLE_ROW = 32, 3
ORNAMENT_RECORD = 33
PATTERN_ENTRY = 7
PATTERN_END = 0xFF
CHANNELS = 3
MAX_PATTERNS = 64
MAX_SAMPLES = 32
MAX_ORNAMENTS = 32


def _text(raw, at, n):
    return raw[at:at + n].decode("latin-1").rstrip(" \x00")


def _printable(b):
    return bool(b) and all(0x20 <= x < 0x7F for x in b)


def parse(raw, filesize):
    """Read the header and every block. Never raises; `ok` says whether the
    module holds together, which is all the identification there is."""
    h = {"ok": False, "why": "", "delay": 0, "positions_at": 0, "ornaments_at": 0,
         "patterns_at": 0, "identifier": "", "size": 0, "samples": [],
         "positions": [], "ornaments": [], "ornaments_end": 0, "comment": "",
         "patterns": [], "pattern_table_end": 0, "header_size": HEADER,
         "ornaments_first": False, "positions_end": 0, "gap_at": 0}
    if len(raw) < HEADER + 1:
        h["why"] = "too short for a header"
        return h
    h["delay"] = raw[DELAY_AT]
    pos, orn, pat = struct.unpack_from("<HHH", raw, POSITIONS_PTR_AT)
    h["positions_at"], h["ornaments_at"], h["patterns_at"] = pos, orn, pat
    h["identifier"] = _text(raw, IDENT_AT, IDENT_LEN)
    h["size"] = struct.unpack_from("<H", raw, SIZE_AT)[0]
    first = min(pos, orn)
    if not (HEADER <= first and pos != orn and max(pos, orn) < pat < filesize):
        h["why"] = "the three pointers are not in order inside the file"
        return h
    h["ornaments_first"] = orn < pos
    if (first - HEADER) % SAMPLE_RECORD or (first - HEADER) // SAMPLE_RECORD > MAX_SAMPLES:
        h["why"] = "the sample area is not a whole number of 99-byte records"
        return h
    for i in range((first - HEADER) // SAMPLE_RECORD):
        at = HEADER + i * SAMPLE_RECORD
        if at + SAMPLE_RECORD > len(raw):
            break
        h["samples"].append({"at": at, "number": raw[at],
                             "repeat_at": raw[at + 97], "repeat_len": raw[at + 98]})
    if pos >= len(raw):
        h["why"] = "the positions block is past the read"
        return h
    pos_end = pat if h["ornaments_first"] else orn
    count = raw[pos] + 1
    if pos + 1 + 2 * count != pos_end:
        h["why"] = "the positions block does not end at the block after it"
        return h
    h["positions"] = [(raw[pos + 1 + 2 * i], raw[pos + 2 + 2 * i]) for i in range(count)]
    h["positions_end"] = pos_end
    orn_limit = pos if h["ornaments_first"] else pat
    n_orn = (orn_limit - orn) // ORNAMENT_RECORD
    if n_orn > MAX_ORNAMENTS:
        h["why"] = "more than 32 ornaments"
        return h
    for i in range(n_orn):
        at = orn + i * ORNAMENT_RECORD
        if at + ORNAMENT_RECORD > len(raw):
            break
        h["ornaments"].append({"at": at, "number": raw[at]})
    h["ornaments_end"] = orn + n_orn * ORNAMENT_RECORD
    # anything between the last fixed block and the pattern table
    tail_from = pos_end if h["ornaments_first"] else h["ornaments_end"]
    h["gap_at"] = tail_from
    gap = raw[tail_from:pat]
    if gap and _printable(gap):
        h["comment"] = gap.decode("latin-1").rstrip()
    q = pat
    while q < len(raw) and raw[q] != PATTERN_END and len(h["patterns"]) < MAX_PATTERNS:
        if q + PATTERN_ENTRY > len(raw):
            break
        num = raw[q]
        ptrs = struct.unpack_from("<3H", raw, q + 1)
        h["patterns"].append({"at": q, "number": num, "streams": ptrs})
        q += PATTERN_ENTRY
    if q >= len(raw) or raw[q] != PATTERN_END:
        h["why"] = "the pattern table has no 0xFF end"
        return h
    h["pattern_table_end"] = q + 1
    for p in h["patterns"]:
        for s in p["streams"]:
            if not h["pattern_table_end"] <= s < filesize:
                h["why"] = "a channel pointer lands outside the streams"
                return h
    h["ok"] = True
    return h


def stream_regions(h, filesize):
    """Channel streams as (offset, size, [(pattern number, channel)]),
    sorted, sized by the next pointer. Shared streams collapse."""
    named = {}
    for p in h["patterns"]:
        for ch, s in enumerate(p["streams"]):
            named.setdefault(s, []).append((p["number"], ch))
    starts = sorted(named)
    out = []
    for k, at in enumerate(starts):
        nxt = starts[k + 1] if k + 1 < len(starts) else filesize
        out.append((at, nxt - at, named[at]))
    return out
