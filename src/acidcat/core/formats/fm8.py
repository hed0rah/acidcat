"""Native Instruments FM8 sound state: the `E8MF` block.

An FM8 preset (.nfm8) is an NI hsin container (formats/ni.py). Its SoundInfoItem
holds the name, and the FastLZ-compressed SubtreeItem inflates to a second hsin
tree whose item 109 carries u32 1, a u64 byte count, then this block. A KORE
sound (.ksd) holding FM8 keeps the same block zlib-compressed in its `prst`
chunk. The block is a fixed serialisation with no tags: everything is found by
position, little-endian throughout.

    0     4    magic `E8MF` ('FM8E' reversed, as NI writes its 4CCs)
    4     4    u32 version (206 and 207 laid out here; FM8's own big-endian
               .ksd writes a byte-swapped `FM8E` block, version 205, not read)
    8     4    u32 9148 in every file seen
    12         four u32-counted ASCII names ("default" from FM8's DX7 import,
               the sound's name four times in a .ksd)

Offsets below are from the end of the four names (S):

    S+0x000    214 float32: transpose at +0x24 (semitones), two LFO records
               at +0x48 and +0x68 (+0 an on flag, +4 waveform, +8 rate,
               +12 delay, +16 key sync), the rest constant in every DX7 import
    S+0x359    three 9-byte arrays, one byte per envelope, not decoded: in
               every DX7 import its point count less 2, less 3 and less 1,
               not so in sounds saved from FM8 itself
    S+0x374    9 bytes: per-slot key sync (0/1)
    S+0x37D    9 bytes: per-slot velocity sensitivity (0-100)
    S+0x386    9 bytes: per-slot envelope rate scaling (0-100)
    S+0x479    four 90-byte copies of: 9 bytes on/off, 9 bytes not decoded,
               9 float32 frequency ratios, 9 float32 frequency offsets in Hz
    S+0x5E1    51 lists, each a u8 n then n+1 float32: 3 groups of 9 (the
               envelopes' point times, levels and slopes), then 3 groups of 8
               (the amplitude key-scaling curves' keys, values and slopes)
    then       the modulation matrix: 11 x 11 cells of 4 bytes, the amount
               from source r to destination c at cell 11 * r + c, its value
               (0-100) stored four times; then bytes not decoded

The nine slots are the six operators A to F, then two slots a DX7 import never
uses, then (for the envelope lists only) the pitch envelope. Matrix columns 0-5
are the operators, column 8 the output; the diagonal is an operator's
feedback. Rows and columns 6, 7, 9 and 10 are not decoded.

HOW THIS WAS MEASURED. 58,736 .nfm8 files, every one FM8 1.2.0.1010's import of
a DX7 voice (version 207), all read to the byte with this layout: the matrix
always ends 475 bytes before the block does. The init patch FM8 installs and 9
sounds saved from FM8 as .ksd (version 206) read the same way, 391 bytes after
the matrix. 1,915 of the imports were paired by bank file and voice name with
the DX7 SysEx they came from (78 banks of a public DX7 archive), and each named
field is the exact image of a DX7 parameter over all 1,915:

    ratio        the DX7 coarse/fine ratio (0.5 for coarse 0), times a small
                 detune factor; 0 for a fixed-frequency operator
    offset       the fixed frequency in Hz (10^(coarse mod 4) x 10^(fine/100)),
                 or the detune as a few tenths of a Hz
    on/off       1 exactly when the DX7 output level is above 0
    key sync     the DX7 oscillator key sync, copied to every operator
    velocity     round(DX7 key velocity sensitivity x 100 / 7)
    rate scaling round(DX7 rate scaling x 100 / 8)
    transpose    DX7 transpose - 24
    LFO          rate and delay are functions of the DX7 LFO speed and delay;
                 sync is the DX7 LFO sync; the waveform ids are what the import
                 writes for the DX7 waves (below)
    matrix       nonzero only where the DX7 algorithm connects two operators
                 (forward, and backward for the loops of algorithms 4 and 6),
                 feeds an operator back on itself, or sends a carrier out;
                 exactly so in 1,839, the rest leave an expected cell at 0
    keys         the middle key-scaling key is the DX7 break point + 17

Slot 0 holds DX7 operator 6 and slot 5 operator 1: the import reverses the
order, as the DX7's own voice data does. The point times, slopes and key-scaling
values follow the DX7 rates, levels and depths but not one to one, so they are
shown as stored. The 4 copies of the per-operator block and of every matrix
cell are equal in all 58,736 imports. In 8 of the 9 .ksd sounds the first copy
differs from the other three, which agree with each other; which copy FM8
plays is not established, and the first is the one read here.
"""

import math
import struct

MAGIC = b"E8MF"
VERSIONS = (206, 207)
NAMES = 4
NAME_MAX = 256
SLOTS = 9
OPERATORS = "ABCDEF"
PITCH_ENV = 8

FLOATS = 214
TRANSPOSE = 0x24
LFOS = (0x48, 0x68)
POINT_INDEX = (0x359, 0x362, 0x36B)
KEY_SYNC = 0x374
VELOCITY = 0x37D
RATE_SCALING = 0x386
BLOCKS = 0x479
BLOCK = 90
COPIES = 4
LISTS = 0x5E1
ENV_LISTS = 9
KEYSCALE_LISTS = 8
MATRIX = 11
MATRIX_OUT = 8
CELL = 4

# the waveform id FM8's DX7 import writes for each DX7 LFO wave; both saws
# become 4 (a separate flag, not decoded, tells them apart)
LFO_WAVES = {0: "sine", 2: "triangle", 3: "square", 4: "saw", 32: "sample and hold"}


class Short(ValueError):
    """The block ends before the layout does."""


def _need(b, at, n):
    if at < 0 or at + n > len(b):
        raise Short("the block is %d bytes; the layout reads %d at 0x%X"
                    % (len(b), n, at))


def _f32(b, at):
    _need(b, at, 4)
    return struct.unpack_from("<f", b, at)[0]


def _u32(b, at):
    _need(b, at, 4)
    return struct.unpack_from("<I", b, at)[0]


def header(b):
    """(version, word, names, S) of a block, or raise. S is where the
    fixed layout starts, after the four names."""
    if b[:4] != MAGIC:
        raise ValueError("no E8MF magic")
    version, word = _u32(b, 4), _u32(b, 8)
    pos, names = 12, []
    for _ in range(NAMES):
        n = _u32(b, pos)
        if n > NAME_MAX:
            raise ValueError("a name at 0x%X claims %d bytes" % (pos, n))
        _need(b, pos + 4, n)
        names.append(b[pos + 4:pos + 4 + n].decode("latin-1"))
        pos += 4 + n
    return version, word, names, pos


def _lists(b, pos, count):
    out = []
    for _ in range(count):
        _need(b, pos, 1)
        n = b[pos] + 1
        _need(b, pos + 1, 4 * n)
        out.append(struct.unpack_from("<%df" % n, b, pos + 1))
        pos += 1 + 4 * n
    return out, pos


def parse(b):
    """The decoded block as a dict. Raises ValueError (Short when the bytes
    stop early) when the bytes are not a block this layout reads; a version
    outside VERSIONS returns only the header, with `laid_out` False."""
    version, word, names, s = header(b)
    out = {"version": version, "word": word, "names": names, "base": s,
           "laid_out": version in VERSIONS}
    if not out["laid_out"]:
        return out
    _need(b, s, LISTS)
    out["transpose"] = _f32(b, s + TRANSPOSE)
    out["lfo"] = [{"on": _f32(b, s + at), "wave": _f32(b, s + at + 4),
                   "rate": _f32(b, s + at + 8), "delay": _f32(b, s + at + 12),
                   "sync": _f32(b, s + at + 16)} for at in LFOS]
    out["point_index"] = [list(b[s + at:s + at + SLOTS]) for at in POINT_INDEX]
    for key, at in (("key_sync", KEY_SYNC), ("velocity", VELOCITY),
                    ("rate_scaling", RATE_SCALING)):
        out[key] = list(b[s + at:s + at + SLOTS])
    blocks = [b[s + BLOCKS + i * BLOCK:s + BLOCKS + (i + 1) * BLOCK]
              for i in range(COPIES)]
    first = s + BLOCKS
    out["on"] = list(b[first:first + SLOTS])
    out["ratio"] = list(struct.unpack_from("<9f", b, first + 2 * SLOTS))
    out["offset"] = list(struct.unpack_from("<9f", b, first + 2 * SLOTS + 4 * SLOTS))
    out["blocks_agree"] = all(x == blocks[0] for x in blocks)
    pos = s + LISTS
    times, pos = _lists(b, pos, ENV_LISTS)
    levels, pos = _lists(b, pos, ENV_LISTS)
    slopes, pos = _lists(b, pos, ENV_LISTS)
    keys, pos = _lists(b, pos, KEYSCALE_LISTS)
    values, pos = _lists(b, pos, KEYSCALE_LISTS)
    kslopes, pos = _lists(b, pos, KEYSCALE_LISTS)
    out["env"] = list(zip(times, levels, slopes))
    out["keyscale"] = list(zip(keys, values, kslopes))
    out["matrix_at"] = pos
    _need(b, pos, MATRIX * MATRIX * CELL)
    cells = [b[pos + CELL * i:pos + CELL * (i + 1)] for i in range(MATRIX * MATRIX)]
    out["matrix"] = [[cells[r * MATRIX + c][0] for c in range(MATRIX)]
                     for r in range(MATRIX)]
    out["cells_agree"] = all(len(set(c)) == 1 for c in cells)
    out["end"] = pos + MATRIX * MATRIX * CELL
    return out


def lfo_wave(v):
    """A waveform id as text."""
    if math.isfinite(v) and v == int(v) and int(v) in LFO_WAVES:
        return LFO_WAVES[int(v)]
    return "id %g" % v


def locate(inner, hsin_walk):
    """(offset, length) of the block in an inflated FM8 subtree, read through
    item 109's own data (u32 1, 1, 1, then the u64 length), or None.
    `hsin_walk` is formats/ni._hsin_walk, passed in so this module stays
    leaf-level; the payload it gives is where the item's own data starts."""
    try:
        frames = hsin_walk(inner, 0, [])
    except (ValueError, struct.error):
        return None
    for iid, _off, _ds, end, payload in frames:
        if iid != 109 or payload + 20 > end:
            continue
        n = struct.unpack_from("<Q", inner, payload + 12)[0]
        at = payload + 20
        if inner[at:at + 4] == MAGIC and at + n <= end:
            return at, n
    return None
