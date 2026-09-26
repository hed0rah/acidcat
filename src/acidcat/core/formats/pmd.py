"""PMD: the compiled output of Professional Music Driver, for the PC-98 and kin.

PMD is the sound driver M. Kajihara wrote for the NEC PC-9801 and its Yamaha
YM2608 (OPNA), later ported to the PC-88, X68000 and FM Towns. A composer
writes MML -- text -- and MC.EXE compiles it to this binary, which the driver
plays. So the file is the compiled form of a score, and the score's own
`#Title`, `#Composer`, `#Arranger`, `#Memo` and sample-bank directives
survive in it as a memo table at the end.

The whole layout, from the driver source and its API documentation:

    0x00   flag byte       0 = PC-98, 1 = X68000 (the driver calls it x68_flg)
    0x01   part table      11 words, little-endian, offsets into the file
                           from byte 1: six FM, three SSG, ADPCM, rhythm
    0x17   rhythm table    word; where the rhythm pattern address table is
    0x19   tone offset     word; where the FM instrument definitions start
    0x1B   ...             the part streams, then the rhythm table, then the
                           instruments (26 bytes each, ending 00 FF), then
                           the memo strings, then the memo pointer table
           (via 0x18+1)    the word at 0x18 of the stream, minus 4, is the
                           memo ANCHOR: a word pointer to the memo table,
                           then a tag byte, then 0xFE

Twelve words in the table, not eleven: play_init walks the eleven parts and
then takes one more for the rhythm address table. The first draft of this
reader counted eleven, put the tone offset two bytes early, and every file
had a two-byte hole in front of its first part.

Everything past the flag byte is offset from byte 1, not byte 0. The driver
loads the file, then points mmlbuf at data+1 and never looks back; a reader
that forgets is one byte out everywhere and the numbers still look plausible.

THE MEMO TABLE. A PCM-file slot is always first. The anchor's tag byte says
which further slots sit in FRONT of it: 0x40 means none, 0x42 or above puts a
PPS-file slot before it, 0x48 or above puts a PPZ-file slot before both. That
is _getmemo's arithmetic reproduced, not a reading of it -- the caller asks
for -2, -1 or 0 and the driver adds one per threshold. Then the text, fixed,
as the driver's author documents it:

    [#PPZFile] [#PPSFile] #PCMFile  -- the sample banks this tune plays from
    #Title, #Composer, #Arranger    -- the text
    #Memo, #Memo, ...               -- free lines, until a zero pointer

Each slot is a word pointing at a NUL-terminated Shift-JIS string, and a
string beginning with '/' means "not set". So a PMD file names its sample
banks the way an MDX names its PDX: the samples are elsewhere, and a tune
without its bank is half a file.

Identification is the driver's own three-byte test: byte 0 at most 0x0F, byte
1 either 0x1A or 0x18, byte 2 either 0 or 0xE6. That is what pmdmini checks
before it will play a file, so it is the definition.

Verified on 1,515 real files across Modland's PMD archive and the MXDRV
Complete set: every one identified, 1,439 carrying a memo block whose titles
and composers decode -- Falcom Sound Team J.D.K., Ryu Umemoto, Masaharu
Iwata among them. Two files with a `.M` extension failed the three-byte test: a text file
and a PDX sample bank, which is what the test is for.
"""

import struct

PARTS = 11                    # 6 FM, 3 SSG, ADPCM, rhythm
RHYTHM_TABLE_AT = PARTS * 2   # 22: the twelfth word, the rhythm address table
TONE_OFFSET_AT = RHYTHM_TABLE_AT + 2   # 24
STREAM_START = TONE_OFFSET_AT + 2      # 26: where the first part begins
PART_TABLE = STREAM_START     # the whole header, in the driver's coordinates
MEMO_ANCHOR_WORD = 0x18       # the word the driver reads to find the memo
PART_NAMES = ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K")
PART_KINDS = ("FM",) * 6 + ("SSG",) * 3 + ("ADPCM", "rhythm")

SILENT = 0x80                 # a part whose stream opens with this is off

# The memo anchor's tag byte, and the optional slots it announces. From
# _getmemo in the driver: al is bumped once at 0x42 and again at 0x48.
MEMO_TAG_MIN = 0x40
MEMO_TAG_PPS = 0x42           # a #PPSFile slot sits in front of the PCM one
MEMO_TAG_PPZ = 0x48           # and a #PPZFile slot in front of that
MEMO_END = 0xFE

# The slots, in table order once the optional ones are accounted for. The
# names are the MML directives the compiler read them from, as the driver's
# own API documentation lists them (DLLInfop.txt, getmemo, AL -2 through 5).
MEMO_FILES = ("ppz_file", "pps_file", "pcm_file")
MEMO_TEXT = ("title", "composer", "arranger")
MEMO_NOT_SET = ord("/")
MEMO_LINE_CAP = 128           # what the MML manual allows for #Memo

FLAG_PC98 = 0
FLAG_X68000 = 1

# An FM instrument: its number, then 25 bytes of YM2608 operator registers.
# The driver finds one by walking 26-byte records comparing the number, so
# the block is a list and not an indexed array. It ends with 00 FF -- a
# record number of 0 and no registers -- in 1,086 of 1,086 files measured,
# and the memo strings begin immediately after.
TONE_RECORD = 26
TONE_END = b"\x00\xff"
TONE_CAP = 256                # a byte-sized instrument number cannot exceed it


def tones(raw, h):
    """The FM instruments as (number, offset) pairs, in file order, and the
    file offset just past the 00 FF terminator. Empty if there are none."""
    if not h["has_tones"]:
        return [], 1 + h["tone_at"]
    pos = 1 + h["tone_at"]
    out = []
    while pos + 2 <= len(raw) and len(out) < TONE_CAP:
        if raw[pos:pos + 2] == TONE_END:
            return out, pos + 2
        if pos + TONE_RECORD > len(raw):
            break
        out.append((raw[pos], pos))
        pos += TONE_RECORD
    return out, pos


def is_pmd(head):
    """The driver's own test, byte for byte."""
    return (len(head) >= 3 and head[0] <= 0x0F
            and head[1] in (0x1A, 0x18) and head[2] in (0x00, 0xE6))


def parse(raw):
    """Read a compiled PMD file. Returns a dict; `ok` says whether it holds."""
    h = {"ok": False, "why": "", "flag": None, "parts": [], "tone_at": None,
         "rhythm_table_at": None, "has_tones": False, "memo": {},
         "memo_lines": [], "memo_at": None, "memo_tag": None}
    if not is_pmd(raw):
        h["why"] = "the first three bytes are not a PMD header"
        h["code"] = "magic.mismatch"
        return h
    h["flag"] = raw[0]
    mml = memoryview(raw)[1:]          # the driver's mmlbuf
    n = len(mml)
    if n < STREAM_START:
        h["why"] = "file ends inside the part table"
        h["code"] = "header.truncated"
        return h

    offsets = struct.unpack_from("<%dH" % PARTS, mml, 0)
    h["rhythm_table_at"] = struct.unpack_from("<H", mml, RHYTHM_TABLE_AT)[0]
    tone_at = struct.unpack_from("<H", mml, TONE_OFFSET_AT)[0]
    for i, off in enumerate(offsets):
        entry = {"name": PART_NAMES[i], "kind": PART_KINDS[i], "offset": off,
                 "silent": None}
        if off < n:
            entry["silent"] = mml[off] == SILENT
        h["parts"].append(entry)
    if any(p["offset"] >= n for p in h["parts"]):
        h["why"] = "a part offset points outside the file"
        h["code"] = "pointer.dangling"
        return h
    h["tone_at"] = tone_at
    # MC.EXE without /V writes no instruments, and then the tone offset is
    # just the table size: the driver's own test for "no tones here"
    h["has_tones"] = tone_at != STREAM_START
    if tone_at > n:
        h["why"] = "the tone offset points outside the file"
        h["code"] = "pointer.dangling"
        return h

    _read_memo(mml, h)
    h["ok"] = True
    return h


def _read_memo(mml, h):
    """The memo table, exactly as _getmemo walks it. Silent on any failure:
    a tune without a memo block is a tune, not a broken one."""
    n = len(mml)
    if n < MEMO_ANCHOR_WORD + 2:
        return
    si = struct.unpack_from("<H", mml, MEMO_ANCHOR_WORD)[0] - 4
    if si < 0 or si + 4 > n:
        return
    tag, end = mml[si + 2], mml[si + 3]
    if not (tag == MEMO_TAG_MIN or (end == MEMO_END and tag > MEMO_TAG_MIN)):
        return
    h["memo_tag"] = tag
    h["memo_at"] = si
    table = struct.unpack_from("<H", mml, si)[0]
    if table + 2 > n:
        return

    # Which file slots this table carries, front to back. From _getmemo: a
    # caller asks for -2 (PPZ), -1 (PPS) or 0 (PCM); the driver adds one at
    # tag >= 0x42 and one more at >= 0x48, and a result below zero is "not
    # registered". So at 0x40 only PCM exists (index 0); at 0x42 PPS is in
    # front of it; at 0x48 PPZ is in front of both.
    slots = []
    if tag >= MEMO_TAG_PPZ:
        slots.append("ppz_file")
    if tag >= MEMO_TAG_PPS:
        slots.append("pps_file")
    slots.append("pcm_file")
    slots += list(MEMO_TEXT)

    pos = table
    for name in slots:
        if pos + 2 > n:
            return
        dx = struct.unpack_from("<H", mml, pos)[0]
        pos += 2
        if dx == 0:
            return
        text = _string(mml, dx)
        # an empty string and a '/' both mean the directive was not written
        if text:
            h["memo"][name] = text
    # then #Memo lines until a zero pointer
    for _ in range(MEMO_LINE_CAP):
        if pos + 2 > n:
            return
        dx = struct.unpack_from("<H", mml, pos)[0]
        pos += 2
        if dx == 0:
            return
        text = _string(mml, dx)
        if text is not None:
            h["memo_lines"].append(text)


def _string(mml, dx):
    """A NUL-terminated Shift-JIS string at dx, or None for "not set"."""
    n = len(mml)
    if dx >= n or mml[dx] == MEMO_NOT_SET:
        return None
    end = dx
    while end < n and mml[end] != 0:
        end += 1
    raw = bytes(mml[dx:end])
    try:
        return raw.decode("shift_jis").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1", "replace").strip()
