"""PDX: the ADPCM sample bank a Sharp X68000 MDX tune plays its P channel from.

An MDX carries no samples. Its ADPCM channel names a bank by file name -- the
NUL-terminated string right after the title -- and every drum and voice hit in
the tune lives in that separate .PDX file. core/formats/mdx.py reads the name;
this reads what it points at.

The format is a pointer table and nothing else. No magic, no version, no
count, no names:

    0x0000  slot[0]    offset u32 BE, length u32 BE
    0x0008  slot[1]    ...
    ...
    0x0300  sample data

Ninety-six slots of eight bytes is one BANK, 768 bytes, and an unused slot is
eight zero bytes. The slot INDEX is the identity: MML says a sample number and
that is the row it reads, so a bank with one sample at slot 33 has 95 empty
rows in front of it, and removing them would silently retune the tune.

Banks stack. A file with more than 96 samples repeats the table -- 192 slots,
288, up to 1,632 in the files measured -- and the sample data starts after the
last one. Nothing declares how many banks there are, so it is recovered the
same way MDX recovers its channel count: the first sample has to begin exactly
where the table ends, so the smallest offset in the table IS the table's size.
Measured over 3,418 real banks, every table size that resolves is a multiple
of 768.

Some writers emit a SHORT table: only the slots they fill, so the first
sample begins before 768 (26 slots is common). It is accepted only when the
short table accounts for the file exactly, samples end to end to the last
byte; 76 real banks do, and none of 333,922 other files does.

Slots may point at the SAME bytes. 947 duplicate slot pairs across the corpus,
and every one of them is an exact duplicate -- same offset, same length --
rather than a window into another sample. A bank aliases a sample to several
numbers; it does not slice one.

The samples themselves are OKI MSM6258V ADPCM: 4 bits per sample, one nibble
per step, which is the chip the X68000 has. A length is therefore twice the
number of audio samples it holds, and there is no rate in the file -- the
player sets it. codecs/adpcm.decode_oki turns one into PCM, low nibble
first, with the datasheet's rounding; the choice is argued there.

Identification is arithmetic, like MDX's. Verified over 3,418 real banks
against 132,305 files of everything else: 3,256 accepted, 0 false positives.
"""

import struct

from acidcat.core.infra.source import open_input, input_size
from acidcat.core.formats.mdx import packer_stamp

SLOT = 8
SLOTS_PER_BANK = 96
BANK = SLOTS_PER_BANK * SLOT          # 768
# Thirty-two banks is 3,072 samples. The largest real table measured is 17
# banks (13,056 bytes) -- the first corpus topped out at 8, and a second twice
# its size found 9, 10, 12, 13 and 17. The bound stops a crafted first-offset
# from making us read a table larger than the file; it is not a claim about
# the format, which declares no ceiling.
MAX_BANKS = 32
# A single bank rounded up to a power of two by its writer.
PADDED_TABLE = 1024
# A slot length is BYTES. MSM6258 ADPCM packs one sample per nibble, so the
# audio is twice as many samples as the length says.
SAMPLES_PER_BYTE = 2


def parse_table(raw, filesize):
    """Read the slot table. Returns a dict; `ok` says whether it holds up.

    `slots` is every row, empty ones included, because the row number is the
    sample number and a compacted list would not be addressable.
    """
    h = {"ok": False, "why": "", "table_size": 0, "banks": 0,
         "slots": [], "used": 0, "packer": "", "data_start": 0,
         "padded": False, "short": False}
    if filesize < BANK + SLOT:
        h["why"] = "file is smaller than one %d-byte bank table" % BANK
        return h

    n = SLOTS_PER_BANK
    for _ in range(MAX_BANKS):
        want = n * SLOT
        if want > len(raw) or want > filesize:
            h["why"] = "file ends inside the slot table"
            return h
        words = struct.unpack_from(">%dI" % (n * 2), raw, 0)
        rows = [(words[i * 2], words[i * 2 + 1]) for i in range(n)]
        live = [(o, s) for o, s in rows if o or s]
        if not live:
            h["why"] = "every slot is empty"
            return h
        if any(s == 0 or o + s > filesize for o, s in live):
            # This is where a PACKED bank lands. The compressors of the era
            # wrote over the table, so the offsets are compressor output and
            # point anywhere at all -- while the file is still a PDX.
            h["packer"] = packer_stamp(raw, 0)
            if h["packer"]:
                h["why"] = ("the bank is packed with %s; the slot table "
                            "belongs to the unpacked form" % h["packer"])
                return h
            short = _short_table(raw, filesize)
            if short:
                h.update(short)
                return h
            h["why"] = "a slot points outside the file"
            return h

        low = min(o for o, _s in live)
        if low == want:
            h.update(ok=True, table_size=want, banks=n // SLOTS_PER_BANK,
                     slots=rows, used=len(live), data_start=want)
            return h
        # a bigger table: the data starts after the LAST bank, so a first
        # offset that is a whole number of banks further on says how many
        if low > want and low % BANK == 0 and low <= MAX_BANKS * BANK:
            n = low // SLOT
            continue
        # One bank, then the data at 1024 rather than 768: a writer that
        # rounds the table up to a power of two. Seventeen real banks do it,
        # every one with the 256 bytes between all zero, and no bank with
        # more than one table does. Accepted as padding, and only when the
        # padding IS zero -- anything else there is data nobody accounted for.
        if (want == BANK and low == PADDED_TABLE
                and not raw[BANK:PADDED_TABLE].strip(b"\x00")):
            h.update(ok=True, table_size=want, banks=1, slots=rows,
                     used=len(live), data_start=low, padded=True)
            return h
        h["why"] = ("the first sample begins at %d, which is not where a "
                    "table of whole banks ends" % low)
        return h
    h["why"] = "slot table claims more than %d banks" % MAX_BANKS
    return h


def _short_table(raw, filesize):
    """A table shorter than one bank, or None.

    Some writers emit only as many slots as they fill: the first sample then
    begins where those slots end, before 768, and reading a whole bank reads
    sample data as slots. Accepted only when the short table accounts for the
    file exactly: every live slot inside it, the samples laid end to end, and
    the last one ending at the end of the file (or one byte before it, which
    a handful of real banks do). That is what separates a bank from any other
    run of bytes, since the format has no magic."""
    first = None
    for i in range(0, min(len(raw), BANK) - SLOT + 1, SLOT):
        o, n = struct.unpack_from(">II", raw, i)
        if o or n:
            first = o
            break
    if first is None or first % SLOT or not 2 * SLOT <= first < BANK:
        return None
    k = first // SLOT
    rows = [struct.unpack_from(">II", raw, i * SLOT) for i in range(k)]
    live = sorted(set((o, n) for o, n in rows if o or n))
    if len(live) < 2 or live[0][0] != first:
        return None
    if any(n == 0 or o < first or o + n > filesize for o, n in live):
        return None
    if any(a[0] + a[1] != b[0] for a, b in zip(live, live[1:])):
        return None
    end = live[-1][0] + live[-1][1]
    if filesize - end not in (0, 1):
        return None
    return {"ok": True, "why": "", "table_size": first, "banks": 1, "slots": rows,
            "used": sum(1 for o, n in rows if o or n), "data_start": first,
            "short": True}


def looks_like_pdx(raw, filesize):
    """Structural identification. The format has no magic, so this is it."""
    return parse_table(raw, filesize)["ok"]


def looks_like_pdx_file(path):
    try:
        with open_input(path) as fh:
            head = fh.read(MAX_BANKS * BANK)
        return looks_like_pdx(head, input_size(path))
    except OSError:
        return False
