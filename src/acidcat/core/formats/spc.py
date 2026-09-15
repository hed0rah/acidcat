"""SPC: a Super Nintendo sound-chip snapshot, with a tag in front.

The SNES has a second computer inside it for sound -- the SPC700, with 64 KB
of its own RAM and a DSP that plays BRR-compressed samples out of that RAM. An
.spc file is that computer frozen mid-tune: the CPU registers, all 64 KB, and
the DSP's 128 registers. A player loads the image and lets it run, which is
why the format plays back exactly and why there is nothing "musical" to parse
in it -- the music is a running program.

What IS parseable is the tag in front, called ID666, and the DSP registers,
which say where in RAM the samples live. The layout, from the published spec:

    0x000   33   "SNES-SPC700 Sound File Data v0.30"
    0x021    3   1A 1A 1A in every real file (the spec says 26 26 then a
                 tag flag; see below)
    0x024    1   minor version
    0x025    2   PC        0x027 A   0x028 X   0x029 Y   0x02A PSW   0x02B SP
    0x02E   32   song title            0x04E   32   game title
    0x06E   16   who dumped it         0x07E   32   comments
    0x09E   11   dump date             0x0A9    3   seconds before fade
    0x0AC    5   fade, milliseconds    0x0B1   32   artist
    0x0D1    1   channel disables      0x0D2    1   emulator that dumped it
    0x100  65536 the SPC700's RAM
    0x10100 128  DSP registers         0x10180 64   unused
    0x101C0  64  the IPL ROM area      0x10200 ...  an xid6 extension, maybe

THE TAG HAS TWO SPELLINGS AND NOTHING SAYS WHICH. The date, seconds and fade
were originally written as text ("04/17/1999", "180", "10000") and later as
binary (a 4-byte date, a 3-byte count, a 4-byte count) in the same slots. A
reader tells them apart by looking: text has digits and slashes, binary does
not. It is a heuristic and the corpus says how well it holds.

THE SAMPLES ARE FINDABLE. DSP register 0x5D is DIR, the page of RAM holding
the sample directory: 256 entries of (start, loop) addresses, four bytes each.
Every entry that points inside RAM is a BRR sample the tune can play, and BRR
is a codec acidcat already decodes. So an SPC is not opaque: it is a bank of
samples with a program wrapped around them.

RSN is not a format. It is a RAR archive of SPC files, named so a player can
find it; the compression works because every tune from one game shares its
engine code and most of its samples.
"""

import struct

# The spec gives one magic, "SNES-SPC700 Sound File Data v0.30". Real
# files carry three: that one (4,956 of 4,999 measured), "v0.10" (34), and a
# bare "0.10" with no v and no EOF markers after it (9). The prefix is the
# identity; the version is read from what follows and reported as found.
MAGIC = b"SNES-SPC700 Sound File Data v0.30"
MAGIC_PREFIX = b"SNES-SPC700 Sound File Data"
MAGIC_LEN = 33
HEADER = 0x100
RAM = 0x10000
RAM_AT = 0x100
DSP_AT = 0x10100
DSP_SIZE = 0x80
UNUSED_AT = 0x10180
IPL_AT = 0x101C0
IPL_SIZE = 0x40
BASE_SIZE = 0x10200           # everything before an xid6 chunk
XID6_AT = BASE_SIZE

HAS_TAG = 0x26
NUL = bytes(1)
NO_TAG = 0x27

EMULATORS = {0: "unknown", 1: "ZSNES", 2: "Snes9x"}

DSP_DIR = 0x5D                # the sample directory page register
DIR_ENTRY = 4                 # start address, loop address
DIR_ENTRIES = 256
# A directory page can name 256 samples. Every real tune measured uses far
# fewer, and an entry is only counted when its start lies inside RAM and its
# BRR header block ends inside RAM too.
BRR_BLOCK = 9


def is_spc(head):
    return (len(head) >= MAGIC_LEN
            and head[:len(MAGIC_PREFIX)] == MAGIC_PREFIX)


def magic_version(raw):
    """The version text after the prefix: "v0.30", "v0.10" or "0.10"."""
    tail = raw[len(MAGIC_PREFIX):MAGIC_LEN]
    return tail.split(b"\x00", 1)[0].decode("latin-1").strip()


def _text(raw, at, n):
    """A fixed slot, NUL-truncated, latin-1 so it cannot fail."""
    blob = raw[at:at + n]
    nul = blob.find(b"\x00")
    if nul >= 0:
        blob = blob[:nul]
    return blob.decode("latin-1").strip()


def _looks_like_text_date(blob):
    """"MM/DD/YYYY" or close to it: digits, slashes, and nothing else."""
    s = blob.rstrip(b"\x00")
    return bool(s) and all(c in b"0123456789/-." for c in s)


def parse_header(raw):
    """The 256-byte header and its ID666 tag. Never raises."""
    h = {"ok": False, "why": "", "has_tag": False, "version": None,
         "magic_version": None,
         "pc": None, "a": None, "x": None, "y": None, "psw": None, "sp": None,
         "tag": {}, "tag_style": None, "emulator": None, "disables": None}
    if not is_spc(raw):
        h["why"] = "no SNES-SPC700 magic"
        return h
    if len(raw) < HEADER:
        h["why"] = "file ends inside the 256-byte header"
        return h
    h["magic_version"] = magic_version(raw)
    h["has_tag"] = raw[0x23] == HAS_TAG          # the spec's flag, recorded
    h["version"] = raw[0x24]
    h["pc"], h["a"], h["x"], h["y"], h["psw"], h["sp"] = struct.unpack_from(
        "<HBBBBB", raw, 0x25)
    # the slots are read regardless of the flag; see the module docstring
    t = {}
    t["title"] = _text(raw, 0x2E, 32)
    t["game"] = _text(raw, 0x4E, 32)
    t["dumper"] = _text(raw, 0x6E, 16)
    t["comment"] = _text(raw, 0x7E, 32)
    date = raw[0x9E:0x9E + 11]
    secs = raw[0xA9:0xAC]
    # Text is the layout unless the seconds slot holds bytes that are NOT
    # digits. An EMPTY slot is text with the length unwritten -- 56 of 324
    # real files, all with a text artist at 0xB1 and a text emulator digit --
    # and reading them as binary put the artist one byte early and dropped
    # its first letter. No binary-tagged file has been seen yet.
    if not any(secs) or _looks_like_text(secs) or _looks_like_text_date(date):
        h["tag_style"] = "text"
        if date.rstrip(NUL):
            t["date"] = date.rstrip(NUL).decode("latin-1")
        t["seconds"] = _int_text(raw, 0xA9, 3)
        t["fade_ms"] = _int_text(raw, 0xAC, 5)
        t["artist"] = _text(raw, 0xB1, 32)
        h["disables"] = raw[0xD1]
        # the emulator byte is written as a text digit in this spelling
        e = raw[0xD2]
        h["emulator"] = e - 0x30 if 0x30 <= e <= 0x39 else e
    else:
        # Binary spelling: the same slots, packed. Verified on 24 real files
        # (an Akihiko Mori set dumped with ZSNES): seconds is three bytes at
        # 0xA9, fade four at 0xAC, the artist starts at 0xB0 -- one byte
        # earlier than in the text layout, and reading it at 0xB1 drops the
        # first letter -- and the emulator is a real number at 0xD1. The
        # spec's table for this spelling carries a known transcription error
        # at the DATE; every binary file measured leaves the date zero, so
        # that one field is read per the spec and remains unverified.
        h["tag_style"] = "binary"
        y, m, d = struct.unpack_from("<HBB", raw, 0x9E)[0], raw[0xA0], raw[0xA1]
        if y:
            t["date"] = "%04d-%02d-%02d" % (y, m, d)
        t["seconds"] = int.from_bytes(raw[0xA9:0xAC], "little") or None
        t["fade_ms"] = struct.unpack_from("<I", raw, 0xAC)[0] or None
        t["artist"] = _text(raw, 0xB0, 32)
        h["disables"] = raw[0xD0]
        h["emulator"] = raw[0xD1]
    h["tag"] = {k: v for k, v in t.items() if v not in ("", None)}
    h["ok"] = True
    return h


def _looks_like_text(blob):
    """Digits, or digits then NULs: the text spelling of a number slot."""
    s = blob.rstrip(NUL)
    return bool(s) and all(0x30 <= c <= 0x39 for c in s)


def _int_text(raw, at, n):
    s = raw[at:at + n].rstrip(b"\x00").strip()
    try:
        return int(s) if s else None
    except ValueError:
        return None


def sample_directory(raw):
    """The BRR samples the DSP can reach: [(index, start, loop)].

    Read from RAM at the page DSP register DIR names. An entry counts only
    if its start lies inside RAM with room for one BRR block; anything else
    is an unused slot or garbage, and both look the same.
    """
    if len(raw) < DSP_AT + DSP_SIZE:
        return []
    page = raw[DSP_AT + DSP_DIR]
    base = RAM_AT + page * 0x100
    out = []
    for i in range(DIR_ENTRIES):
        at = base + i * DIR_ENTRY
        if at + DIR_ENTRY > RAM_AT + RAM:
            break
        start, loop = struct.unpack_from("<HH", raw, at)
        if start == 0 or start + BRR_BLOCK > RAM:
            continue
        out.append((i, start, loop))
    return out


def brr_length(raw, start):
    """How many bytes a BRR sample occupies from `start` in RAM: nine-byte
    blocks until one whose header has the END bit set. Bounded by RAM."""
    pos = RAM_AT + start
    end = RAM_AT + RAM
    n = 0
    while pos + BRR_BLOCK <= end:
        n += BRR_BLOCK
        if raw[pos] & 0x01:
            return n
        pos += BRR_BLOCK
    return n


def brr_loops(raw, start, length):
    """Whether the sample's last block asks to loop: bit 1 of its header."""
    if length < BRR_BLOCK:
        return False
    last = RAM_AT + start + length - BRR_BLOCK
    return bool(raw[last] & 0x02)


VOICES = 8
DSP_SRCN = 0x04               # per voice, at 0x04 + voice * 0x10


def voice_sources(raw):
    """Which directory entry each of the eight voices is set to play.

    This is the reliable list. The directory names 256 candidates and a sound
    engine leaves most of them stale -- pointing into program code, into each
    other, into whatever a previous tune left there. 219 of 332 real files
    have overlapping directory entries; only 14 have overlapping entries among
    the ones the voices are actually set to. So the voices' own SRCN registers
    say what is real, and the directory says how many slots there are.
    """
    if len(raw) < DSP_AT + DSP_SIZE:
        return {}
    out = {}
    for v in range(VOICES):
        out[v] = raw[DSP_AT + DSP_SRCN + v * 0x10]
    return out
