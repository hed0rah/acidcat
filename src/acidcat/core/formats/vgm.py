"""VGM: a log of every write to a sound chip, with the clock it ran at.

A VGM file is not a score and not a sample bank. It is what a game sent to
its sound chips, register by register, with the waits between writes, so a
player with the same chips (emulated) produces the same sound. That makes
the format three things: a header of chip clocks, which says WHICH chips a
tune uses (a non-zero clock is the only flag there is); a command stream,
which is the music; and a GD3 tag at the end, which is who wrote it.

    0x00   "Vgm "         magic
    0x04   u32 LE         end of file, relative to 0x04
    0x08   u32 LE         version, BCD: 0x00000151 is 1.51
    0x0C   u32 LE         SN76489 clock, Hz; zero means no such chip
    0x10   u32 LE         YM2413 clock
    0x14   u32 LE         GD3 tag offset, relative to 0x14; zero for none
    0x18   u32 LE         total samples at 44,100 Hz
    0x1C   u32 LE         loop point, relative to 0x1C; zero for no loop
    0x20   u32 LE         samples in the loop
    0x24   u32 LE         playback rate, Hz (1.01); 0 means unspecified
    0x28   ...            more chip clocks, added version by version; see
                          CHIP_CLOCKS. Fields past the file's version are
                          read as zero.
    0x34   u32 LE         command stream offset, relative to 0x34 (1.50);
                          zero, or a version before 1.50, means 0x40

The command stream is a byte code: a chip-write opcode and its operands,
or a wait. Every opcode has a fixed length except 0x67 (a data block: PCM
for a chip's sample RAM, carveable) and 0x66 (end). Reserved opcode ranges
have lengths the spec fixes so a reader can skip commands it does not
know. The walk below decodes lengths only; what a register write MEANS is
the chip's business.

GD3 is UTF-16LE: "Gd3 ", u32 version, u32 length, then eleven strings each
NUL-terminated: title, title (Japanese), game, game (JP), system, system
(JP), author, author (JP), date, who converted it, notes.

Spec: vgmrips.net's vgmspec, versions 1.00 to 1.72. A .vgz is a .vgm
inside gzip, and nothing else.
"""

import gzip
import struct

MAGIC = b"Vgm "
GD3_MAGIC = b"Gd3 "
HEADER_MIN = 0x40
SAMPLE_RATE = 44100
END_OF_DATA = 0x66
DATA_BLOCK = 0x67

# (offset, size, name, version it appeared in). A clock is u32; the byte-
# sized ones are flags kept beside their chip. Order is file order.
CHIP_CLOCKS = [
    (0x0C, "SN76489", 0x100), (0x10, "YM2413", 0x100),
    (0x2C, "YM2612", 0x110), (0x30, "YM2151", 0x110),
    (0x38, "SegaPCM", 0x151), (0x40, "RF5C68", 0x151),
    (0x44, "YM2203", 0x151), (0x48, "YM2608", 0x151), (0x4C, "YM2610", 0x151),
    (0x50, "YM3812", 0x151), (0x54, "YM3526", 0x151), (0x58, "Y8950", 0x151),
    (0x5C, "YMF262", 0x151), (0x60, "YMF278B", 0x151), (0x64, "YMF271", 0x151),
    (0x68, "YMZ280B", 0x151), (0x6C, "RF5C164", 0x151), (0x70, "PWM", 0x151),
    (0x74, "AY8910", 0x151),
    (0x80, "GameBoy DMG", 0x161), (0x84, "NES APU", 0x161),
    (0x88, "MultiPCM", 0x161), (0x8C, "uPD7759", 0x161),
    (0x90, "OKIM6258", 0x161), (0x98, "OKIM6295", 0x161),
    (0x9C, "K051649", 0x161), (0xA0, "K054539", 0x161),
    (0xA4, "HuC6280", 0x161), (0xA8, "C140", 0x161), (0xAC, "K053260", 0x161),
    (0xB0, "Pokey", 0x161), (0xB4, "QSound", 0x161),
    (0xB8, "SCSP", 0x171),
    (0xC0, "WonderSwan", 0x171), (0xC4, "VSU", 0x171), (0xC8, "SAA1099", 0x171),
    (0xCC, "ES5503", 0x171), (0xD0, "ES5506", 0x171),
    (0xD8, "X1-010", 0x171), (0xDC, "C352", 0x171), (0xE0, "GA20", 0x171),
    (0xE4, "Mikey", 0x172),
]
# a clock's top bit means "two of this chip" from 1.51 on
DUAL_CHIP_BIT = 0x40000000

# Command lengths after the opcode byte. None means variable (handled in
# the walker); absent means the reserved-range rule applies.
_FIXED = {
    0x4F: 1, 0x50: 1,                          # Game Gear stereo, PSG
    0x61: 2, 0x62: 0, 0x63: 0,                 # waits
    0x64: 3,                                   # override a wait's length (1.70)
    0x66: 0,                                   # end
    0x90: 4, 0x91: 4, 0x92: 5, 0x93: 10, 0x94: 1, 0x95: 4,   # DAC streams
    0xE0: 4,                                   # PCM seek
}
for _op in range(0x51, 0x60):                  # YM2413 .. YMF262 ports etc.
    _FIXED[_op] = 2
for _op in range(0x70, 0x80):                  # wait n+1
    _FIXED[_op] = 0
for _op in range(0x80, 0x90):                  # YM2612 DAC write + wait
    _FIXED[_op] = 0
for _op in range(0xA0, 0xC0):                  # AY8910 and the 2-operand block
    _FIXED[_op] = 2
for _op in range(0xC0, 0xE0):                  # 3-operand block
    _FIXED[_op] = 3
for _op in range(0xE1, 0x100):                 # 4-operand block
    _FIXED[_op] = 4
for _op in range(0x30, 0x40):                  # reserved, one operand
    _FIXED[_op] = 1
for _op in range(0x40, 0x4F):                  # reserved, two operands (1.60)
    _FIXED[_op] = 2

WAIT_735 = 0x62
WAIT_882 = 0x63

# Which opcode writes which chip; the second-chip variants (0xA0.. for
# AY, 0x30 for a second PSG, port bits) are folded onto the first name.
OPCODE_CHIP = {
    0x30: "SN76489", 0x4F: "SN76489", 0x50: "SN76489",
    0x51: "YM2413", 0x52: "YM2612", 0x53: "YM2612", 0x54: "YM2151",
    0x55: "YM2203", 0x56: "YM2608", 0x57: "YM2608", 0x58: "YM2610",
    0x59: "YM2610", 0x5A: "YM3812", 0x5B: "YM3526", 0x5C: "Y8950",
    0x5D: "YMZ280B", 0x5E: "YMF262", 0x5F: "YMF262",
    0xA0: "AY8910", 0xB0: "RF5C68", 0xB1: "RF5C164", 0xB2: "PWM",
    0xB3: "GameBoy DMG", 0xB4: "NES APU", 0xB5: "MultiPCM", 0xB6: "uPD7759",
    0xB7: "OKIM6258", 0xB8: "OKIM6295", 0xB9: "HuC6280", 0xBA: "K053260",
    0xBB: "Pokey", 0xBC: "WonderSwan", 0xBD: "SAA1099", 0xBE: "ES5506",
    0xBF: "GA20", 0xC0: "SegaPCM", 0xC1: "RF5C68", 0xC2: "RF5C164",
    0xC3: "MultiPCM", 0xC4: "QSound", 0xC5: "SCSP", 0xC6: "WonderSwan",
    0xC7: "VSU", 0xC8: "X1-010", 0xD0: "YMF278B", 0xD1: "YMF271",
    0xD2: "K051649", 0xD3: "K054539", 0xD4: "C140", 0xD5: "ES5503",
    0xD6: "ES5506", 0xE1: "C352",
}


def is_vgm(head):
    return head[:4] == MAGIC


def is_vgz(head):
    """gzip; the walker inflates and checks the magic. A .vgz that holds
    anything else is a gzip file, not a VGM."""
    return head[:2] == b"\x1f\x8b"


def inflate(raw, cap):
    """The VGM inside a .vgz, or None if it is not gzip / not a VGM. Bounded
    by `cap` so a forged stream cannot make us allocate."""
    try:
        d = gzip.decompress(raw) if len(raw) < cap else None
    except (OSError, EOFError):
        return None
    if d is None or len(d) > cap or not is_vgm(d[:4]):
        return None
    return d


def _u32(raw, off):
    return struct.unpack_from("<I", raw, off)[0] if off + 4 <= len(raw) else 0


def version_text(v):
    """BCD: 0x00000151 -> '1.51'."""
    return "%x.%02x" % (v >> 8, v & 0xFF)


def parse_header(raw):
    """The header. Never raises; `ok` says whether it holds."""
    h = {"ok": False, "why": "", "version": 0, "version_text": "",
         "eof": 0, "gd3_at": None, "total_samples": 0, "loop_at": None,
         "loop_samples": 0, "rate": 0, "data_at": HEADER_MIN,
         "header_size": HEADER_MIN, "chips": []}
    if len(raw) < HEADER_MIN or not is_vgm(raw):
        h["why"] = "no Vgm magic"
        return h
    v = _u32(raw, 0x08)
    h["version"], h["version_text"] = v, version_text(v)
    h["eof"] = 0x04 + _u32(raw, 0x04)
    gd3 = _u32(raw, 0x14)
    h["gd3_at"] = 0x14 + gd3 if gd3 else None
    h["total_samples"] = _u32(raw, 0x18)
    loop = _u32(raw, 0x1C)
    h["loop_at"] = 0x1C + loop if loop else None
    h["loop_samples"] = _u32(raw, 0x20)
    h["rate"] = _u32(raw, 0x24) if v >= 0x101 else 0
    if v >= 0x150:
        do = _u32(raw, 0x34)
        h["data_at"] = 0x34 + do if do else HEADER_MIN
    h["header_size"] = h["data_at"]
    for off, name, since in CHIP_CLOCKS:
        # a field the version predates is not a field, whatever the bytes
        # say, and a field past the header's own end is the stream
        if v < since or off + 4 > h["data_at"]:
            continue
        clock = _u32(raw, off)
        if clock:
            dual = bool(clock & DUAL_CHIP_BIT) and v >= 0x151
            h["chips"].append({"name": name, "offset": off,
                               "clock": clock & ~DUAL_CHIP_BIT, "dual": dual})
    h["ok"] = True
    return h


def walk_commands(raw, start, end, cap):
    """Decode command lengths from `start` until 0x66, `end`, or `cap`
    commands. Returns a dict: commands, waits (in samples), writes per
    chip, data blocks [(offset, type, size)], where the stream ended, and
    a reason if it ended badly."""
    pos = start
    n = 0
    waits = 0
    wait_735, wait_882 = 735, 882             # 0x64 can redefine either
    writes = {}
    blocks = []
    why = ""
    ended = False
    while pos < end and n < cap:
        op = raw[pos]
        n += 1
        if op == END_OF_DATA:
            pos += 1
            ended = True
            break
        if op == DATA_BLOCK:
            if pos + 7 > end:
                why = "a data block header runs past the end"
                break
            btype = raw[pos + 2]
            size = _u32(raw, pos + 3) & 0x7FFFFFFF
            blocks.append((pos + 7, btype, size))
            pos += 7 + size
            continue
        if op == 0x68:                          # PCM RAM write: 11 operands
            pos += 12
            continue
        ln = _FIXED.get(op)
        if ln is None:
            why = "opcode 0x%02X at 0x%X is not one the spec defines" % (op, pos)
            break
        if op == 0x61:
            waits += raw[pos + 1] | (raw[pos + 2] << 8) if pos + 3 <= end else 0
        elif op == WAIT_735:
            waits += wait_735
        elif op == WAIT_882:
            waits += wait_882
        elif op == 0x64 and pos + 4 <= end:
            which, count = raw[pos + 1], raw[pos + 2] | (raw[pos + 3] << 8)
            if which == WAIT_735:
                wait_735 = count
            elif which == WAIT_882:
                wait_882 = count
        elif 0x70 <= op <= 0x7F:
            waits += (op & 0x0F) + 1
        elif 0x80 <= op <= 0x8F:
            waits += op & 0x0F
            writes["YM2612"] = writes.get("YM2612", 0) + 1
        else:
            chip = OPCODE_CHIP.get(op)
            if chip:
                writes[chip] = writes.get(chip, 0) + 1
        pos += 1 + ln
    if pos > end:
        why = why or "the last command runs past the end of the stream"
        pos = end
    return {"commands": n, "waits": waits, "writes": writes, "blocks": blocks,
            "end": pos, "ended": ended, "why": why, "capped": n >= cap}


GD3_FIELDS = ["title", "title_jp", "game", "game_jp", "system", "system_jp",
              "author", "author_jp", "date", "converter", "notes"]


def parse_gd3(raw, at):
    """The tag. Returns a dict, `ok` False if the magic is not there."""
    g = {"ok": False, "version": 0, "length": 0, "fields": {}, "size": 0}
    if raw[at:at + 4] != GD3_MAGIC or at + 12 > len(raw):
        return g
    g["version"] = _u32(raw, at + 4)
    g["length"] = _u32(raw, at + 8)
    body = raw[at + 12:at + 12 + g["length"]]
    g["size"] = 12 + len(body)
    text = body.decode("utf-16-le", errors="replace")
    parts = text.split("\x00")
    for name, value in zip(GD3_FIELDS, parts):
        if value:
            g["fields"][name] = value
    g["ok"] = True
    return g
