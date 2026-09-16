"""PT3: a ZX Spectrum AY-3-8910 module, the ProTracker 3 / Vortex Tracker kind.

The Spectrum's sound chip has three square-wave channels, an envelope and
a noise generator, and a PT3 is a score for it: sixteen ornaments (pitch
tables), thirty-two samples (per-tick tables of volume, tone and noise
settings; there is no PCM anywhere), patterns of three channel streams,
and a position list. Everything is reached by 16-bit pointers that are
absolute offsets into the file, which is what a Spectrum player did with
the module loaded at a known address.

    0x00   30   signature: "ProTracker 3.x compilation of " or
                "Vortex Tracker II 1.0 module: "
    0x1E   32   name
    0x3E    4   " by "
    0x42   32   author
    0x63    1   tone table id (0-3: which of four tuning tables)
    0x64    1   delay (ticks per row)
    0x65    1   number of positions
    0x66    1   loop position
    0x67    2   pointer to the pattern table
    0x69   64   32 sample pointers (0 = no sample)
    0xA9   32   16 ornament pointers
    0xC9   ...  position list, one byte per position, each a pattern
                number times 3, ended by 0xFF

The pattern table has three u16 pointers per pattern, one per channel.
A sample is (loop, length) then length 4-byte rows; an ornament (loop,
length) then length bytes. The channel streams are a byte code with a
0x00 end marker; their extent is taken from the next pointed region
rather than decoded, so a stream is a region that begins where its
pointer says and ends where the next thing begins.

Spec: the Vortex Tracker II source and ayfly's pt3 loader. A "TS" file
is two modules back to back for Turbo Sound, with a footer naming both.

PT2, the tracker before it, is the same idea with no signature and the
header the other way round:

    0x00    1   delay
    0x01    1   number of positions
    0x02    1   loop position
    0x03   64   32 sample pointers
    0x43   32   16 ornament pointers
    0x63    2   pointer to the pattern table
    0x65   30   name
    0x83   ...  position list, plain pattern numbers, ended by 0xFF

and a sample row is 3 bytes. With nothing to sniff, a PT2 is identified
by its arithmetic: the counts agree, every pointer lands inside the
file, and the first pointed region begins exactly where the position
list ends. Every real file measured does that.
"""

import struct

SIGNATURES = (b"ProTracker 3.", b"Vortex Tracker II")
SIG_LEN = 30
NAME_AT, NAME_LEN = 0x1E, 32
BY_AT = 0x3E
AUTHOR_AT, AUTHOR_LEN = 0x42, 32
TONE_TABLE_AT = 0x63
DELAY_AT = 0x64
POSITIONS_AT = 0x65
LOOP_AT = 0x66
PATTERNS_PTR_AT = 0x67
SAMPLES_AT, SAMPLES = 0x69, 32
ORNAMENTS_AT, ORNAMENTS = 0xA9, 16
POSITION_LIST_AT = 0xC9
POSITION_END = 0xFF
CHANNELS = 3
FIXED_HEADER = POSITION_LIST_AT
MAX_POSITIONS = 256
TS_FOOTER = b"02TS"
TS_FOOTER_LEN = 16

TONE_TABLES = {0: "PT 3.3 and below", 1: "Sound Tracker", 2: "ASM / PSC",
               3: "Real Sound"}
SAMPLE_ROW = 4

PT2_DELAY_AT = 0x00
PT2_POSITIONS_AT = 0x01
PT2_LOOP_AT = 0x02
PT2_SAMPLES_AT = 0x03
PT2_ORNAMENTS_AT = 0x43
PT2_PATTERNS_PTR_AT = 0x63
PT2_NAME_AT, PT2_NAME_LEN = 0x65, 30
PT2_POSITION_LIST_AT = 0x83
PT2_SAMPLE_ROW = 3


def _text(raw, at, n):
    return raw[at:at + n].decode("latin-1").rstrip(" \x00")


def is_pt3(head):
    return any(head.startswith(s) for s in SIGNATURES)


def _empty():
    return {"ok": False, "why": "", "kind": "pt3", "signature": "", "name": "",
            "author": "", "tone_table": 0, "delay": 0, "positions": 0, "loop": 0,
            "patterns_at": 0, "samples": [], "ornaments": [], "position_list": [],
            "header_size": 0, "pattern_count": 0, "pattern_table_size": 0,
            "channel_streams": [], "ts_footer": False, "bad_pointers": [],
            "sample_row": SAMPLE_ROW}


def parse(raw, filesize):
    """Read the header and every pointer. Never raises; `ok` says whether
    the module holds together."""
    h = _empty()
    if len(raw) < FIXED_HEADER + 1 or not is_pt3(raw):
        h["why"] = "no ProTracker 3 signature"
        return h
    h["signature"] = _text(raw, 0, SIG_LEN)
    h["name"] = _text(raw, NAME_AT, NAME_LEN)
    h["author"] = _text(raw, AUTHOR_AT, AUTHOR_LEN)
    h["tone_table"] = raw[TONE_TABLE_AT]
    h["delay"] = raw[DELAY_AT]
    h["positions"] = raw[POSITIONS_AT]
    h["loop"] = raw[LOOP_AT]
    h["patterns_at"] = struct.unpack_from("<H", raw, PATTERNS_PTR_AT)[0]
    h["samples"] = list(struct.unpack_from("<%dH" % SAMPLES, raw, SAMPLES_AT))
    h["ornaments"] = list(struct.unpack_from("<%dH" % ORNAMENTS, raw, ORNAMENTS_AT))
    end = raw.find(bytes([POSITION_END]), POSITION_LIST_AT,
                   POSITION_LIST_AT + MAX_POSITIONS + 1)
    if end < 0:
        h["why"] = "the position list has no 0xFF terminator"
        return h
    h["position_list"] = [b // CHANNELS for b in raw[POSITION_LIST_AT:end]]
    h["header_size"] = end + 1
    if len(h["position_list"]) != h["positions"]:
        h["why"] = ("the header says %d positions and the list holds %d"
                    % (h["positions"], len(h["position_list"])))
        return h
    if any(b % CHANNELS for b in raw[POSITION_LIST_AT:end]):
        h["why"] = "a position is not a multiple of three"
        return h
    return _finish(h, raw, filesize)


def parse_pt2(raw, filesize):
    """PT2: no signature, so the arithmetic is the identification. `ok`
    only when every count agrees, every pointer is inside the file, and
    the first pointed region starts exactly where the header ends."""
    h = _empty()
    h["kind"] = "pt2"
    h["sample_row"] = PT2_SAMPLE_ROW
    if len(raw) < PT2_POSITION_LIST_AT + 2:
        h["why"] = "too short for a PT2 header"
        return h
    h["signature"] = "Pro Tracker 2"
    h["delay"] = raw[PT2_DELAY_AT]
    h["positions"] = raw[PT2_POSITIONS_AT]
    h["loop"] = raw[PT2_LOOP_AT]
    h["samples"] = list(struct.unpack_from("<%dH" % SAMPLES, raw, PT2_SAMPLES_AT))
    h["ornaments"] = list(struct.unpack_from("<%dH" % ORNAMENTS, raw, PT2_ORNAMENTS_AT))
    h["patterns_at"] = struct.unpack_from("<H", raw, PT2_PATTERNS_PTR_AT)[0]
    h["name"] = _text(raw, PT2_NAME_AT, PT2_NAME_LEN)
    end = raw.find(bytes([POSITION_END]), PT2_POSITION_LIST_AT,
                   PT2_POSITION_LIST_AT + MAX_POSITIONS + 1)
    if end < 0 or end - PT2_POSITION_LIST_AT != h["positions"] or not h["positions"]:
        h["why"] = "the position count and the list do not agree"
        return h
    if not 1 <= h["delay"] or h["loop"] >= h["positions"]:
        h["why"] = "delay or loop out of range"
        return h
    h["position_list"] = list(raw[PT2_POSITION_LIST_AT:end])
    h["header_size"] = end + 1
    h = _finish(h, raw, filesize)
    if not h["ok"]:
        return h
    if h["bad_pointers"]:
        h["ok"] = False
        h["why"] = "a pointer lands outside the file"
        return h
    first = min([h["patterns_at"]] + [p for p in h["samples"] + h["ornaments"] if p]
                + [q for t in h["channel_streams"] for q in t])
    if first != h["header_size"]:
        h["ok"] = False
        h["why"] = "the first pointed region is not at the header's end"
    return h


def _finish(h, raw, filesize):
    npat = (max(h["position_list"]) + 1) if h["position_list"] else 0
    h["pattern_count"] = npat
    h["pattern_table_size"] = npat * CHANNELS * 2
    pt = h["patterns_at"]
    if pt < h["header_size"] or pt + h["pattern_table_size"] > filesize:
        h["why"] = "the pattern table at %d does not fit the file" % pt
        return h
    for i in range(npat):
        h["channel_streams"].append(
            struct.unpack_from("<3H", raw, pt + i * CHANNELS * 2))
    # a pointer outside the file is a truncated module, not a different
    # format: it is recorded, the region it named is simply absent, and
    # the walker says so
    for kind, ptrs in (("sample", h["samples"]), ("ornament", h["ornaments"])):
        for i, p in enumerate(ptrs):
            if p and not h["header_size"] <= p < filesize:
                h["bad_pointers"].append((kind, i, p))
    for i, trio in enumerate(h["channel_streams"]):
        for ch, p in enumerate(trio):
            if not h["header_size"] <= p < filesize:
                h["bad_pointers"].append(("channel", i * CHANNELS + ch, p))
    if raw[-TS_FOOTER_LEN:-TS_FOOTER_LEN + 4] == TS_FOOTER:
        h["ts_footer"] = True
    h["ok"] = True
    return h


def sample_size(raw, at, row=SAMPLE_ROW):
    """(loop, length, bytes) of the sample record at `at`; a row is 4 bytes
    in PT3 and 3 in PT2."""
    if at + 2 > len(raw):
        return None
    loop, length = raw[at], raw[at + 1]
    return loop, length, 2 + length * row


def ornament_size(raw, at):
    """(loop, length, bytes) of the ornament record at `at`; a row is 1 byte."""
    if at + 2 > len(raw):
        return None
    loop, length = raw[at], raw[at + 1]
    return loop, length, 2 + length


def regions(h, filesize):
    """Every pointed-at region as (offset, kind, index), sorted, so a
    walker can size each by the next one. Duplicate pointers (two
    patterns sharing a channel stream, an empty sample shared by many)
    collapse to one region with every name."""
    named = {}
    named.setdefault(h["patterns_at"], []).append(("pattern_table", 0))
    inside = lambda p: h["header_size"] <= p < filesize
    for i, p in enumerate(h["samples"]):
        if p and inside(p):
            named.setdefault(p, []).append(("sample", i))
    for i, p in enumerate(h["ornaments"]):
        if p and inside(p):
            named.setdefault(p, []).append(("ornament", i))
    for i, trio in enumerate(h["channel_streams"]):
        for ch, p in enumerate(trio):
            if inside(p):
                named.setdefault(p, []).append(("channel", i * CHANNELS + ch))
    out = []
    starts = sorted(named)
    for k, at in enumerate(starts):
        nxt = starts[k + 1] if k + 1 < len(starts) else filesize
        out.append((at, nxt - at, named[at]))
    return out
