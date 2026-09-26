"""MDX: the Sharp X68000 music format, as played by MXDRV.

An MDX is a Music Macro Language score for the X68000's YM2151 (OPM) FM chip,
plus optional ADPCM samples that live in a SEPARATE file with a .PDX
extension. So a tune is often two files, and the MDX names the one it wants.

There is no magic number. A file opens with its title in Shift-JIS, and
identification is the arithmetic: a title terminator, a NUL-terminated PDX
name, then an offset table whose own first entry says how many channels
follow. That table has to resolve to 9 or 16 or the file is not an MDX.

LAYOUT

    title           Shift-JIS, terminated by 0D 0A 1A
    pdx name        NUL-terminated; a bare NUL when the tune uses no samples
    voice offset    u16 big-endian
    mml offsets     u16 big-endian x 9 or x 16

Every offset is relative to the position of the VOICE OFFSET WORD, not to the
start of the file -- which is the one thing a reader has to get right, because
the title and PDX name are both variable length so that base moves per file.

The channel count is recovered from the table rather than declared. The table
ends where the first thing after it begins, and the gap between that point and
the base, divided by two, is the channel count. Measured over 27,166 real
tunes: 18,385 use 9 channels and 8,331 use 16, and nothing else resolves.

The thing after the table is USUALLY the first MML stream, so the first MML
offset is usually the table end. It is a convention rather than a rule:
eighteen modules measured (the METAL SIGHT set among them) lay the voice block
down first, and there the first MML offset points hundreds of bytes further
on. Taking the earlier of the two is what makes both layouts resolve.

A further 440 modules were packed with an X68000 compressor after they were
written. Their titles and PDX names are in the clear and everything from the
table on is compressed, so they are identified and their headers read, but
their music is not walked. See packer_stamp below.

Channels are lettered rather than numbered: A through H are the eight FM
voices, P is the ADPCM channel, and Q through W are the extra voices a Mercury
Unit expansion board provides. Nine is the base machine; sixteen is a machine
with the expansion.

Voices are 27 bytes each and are OPM register values, not an abstraction over
them -- so a voice is literally what gets written to the chip.

The byte order is big-endian throughout, which for once is not a trap: the
X68000 is a 68000.

Layout from the MXDRV format description (www16.atwiki.jp/mxdrv), cross-checked
against the mdxtools notes, and every field below measured against 27,166 tunes
from the X68000 MDX Master Library.
"""

import struct

from acidcat.core.infra.source import open_input, input_size

TITLE_END = b"\x0d\x0a\x1a"

# A title longer than this is not a title. The longest in 27,166 real tunes is
# well inside it; the cap exists so a file with no terminator at all is
# rejected by arithmetic rather than by reading to EOF.
MAX_TITLE = 1024
# Likewise for the sample-bank name, which is a Human68k filename.
MAX_PDX_NAME = 256

# The only two table sizes that occur. Nine is a stock X68000: eight FM voices
# plus one ADPCM channel. Sixteen adds the Mercury Unit's extra voices.
CHANNELS_BASE = 9
CHANNELS_MERCURY = 16

VOICE_SIZE = 27

_LETTERS = "ABCDEFGHPQRSTUVW"


def channel_name(index, count):
    """The letter MXDRV uses for a channel, or a number if it has none.

    A through H are the FM voices and P is ADPCM, so a nine-channel file is
    A-H plus P. Q onward are Mercury Unit channels.
    """
    if count == CHANNELS_BASE and index == 8:
        return "P"
    if index < len(_LETTERS):
        return _LETTERS[index]
    return str(index)


def decode_title(raw):
    """Decode a title. Shift-JIS, because the X68000 is a Japanese machine.

    Falls back rather than raising: a title that will not decode is still a
    title, and refusing to name the tune is worse than naming it imperfectly.
    """
    try:
        return raw.decode("shift_jis").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1").strip()


# X68000 packers, stamped into the file where the offset table would be.
# An MDX whose body is packed keeps its title and its PDX reference in the
# clear and replaces everything after with compressed data, so the header
# reads perfectly and the offset table resolves to nonsense -- 12,312
# channels, in the file that turned this up.
#
# Measured on 27,166 modules: 440 are packed, and in 438 of them the stamp
# sits at exactly base+4, immediately after the two words the offset table
# would have started with.
PACKERS = (b"LZX", b"ZOO", b"LHA", b"LZS")
PACKER_WINDOW = 64


def packer_stamp(raw, base):
    """The packer that wrote this file, or "" if the body is not packed.

    Looked for near the offset table rather than anywhere in the file: "LZX"
    is three common bytes and finding them in compressed data proves nothing.
    """
    if base < 0:
        return ""
    window = raw[base:base + PACKER_WINDOW]
    for name in PACKERS:
        at = window.find(name)
        if at < 0:
            continue
        version = bytes(c for c in window[at + len(name):at + len(name) + 8]
                        if 0x20 <= c < 0x7F).strip()
        return (name + b" " + version).decode("latin-1").strip()
    return ""


# The shape shared by the SACOM modules that are not MXDRV files: see the
# note where parse_header reports them.
POINTER_TABLE_SLOTS = 16
POINTER_TABLE_USED = 9


def looks_like_pointer_table_module(raw):
    """A 64-byte table of sixteen 32-bit big-endian slots, the first nine
    rising and inside the file, the rest zero, the first equal to 64."""
    if len(raw) < POINTER_TABLE_SLOTS * 4 + 4:
        return False
    words = struct.unpack_from(">%dI" % POINTER_TABLE_SLOTS, raw, 0)
    if words[0] != POINTER_TABLE_SLOTS * 4:
        return False
    used = words[:POINTER_TABLE_USED]
    if any(words[POINTER_TABLE_USED:]):
        return False
    return all(a < b for a, b in zip(used, used[1:]))


def parse_header(raw):
    """Decode an MDX header. Never raises; `ok` says whether it holds together.

    Returns a dict with the title, the PDX name, the offset base, the channel
    count and the resolved absolute offsets of the voice block and each
    channel's MML stream.
    """
    h = {
        "ok": False, "why": "", "title": "", "title_end": -1,
        "pdx_name": "", "has_pdx": False, "base": -1,
        "channels": 0, "voice_offset": 0, "voice_abs": -1,
        "mml_offsets": [], "mml_abs": [], "packer": "",
        "pointer_table": False,
    }
    end = raw.find(TITLE_END, 0, MAX_TITLE)
    if end < 0:
        # Before saying "no title", say what the bytes ARE if they are
        # something consistent. Eighty of 54,738 modules in one archive --
        # all from one publisher, SACOM -- open with a 64-byte table of
        # sixteen big-endian 32-bit slots with exactly nine filled, every
        # stream beginning with a command byte and every file ending FF FA.
        # Nine is the X68000's channel count, so it is a module for this
        # machine written by a driver that is not MXDRV. Which driver is not
        # known and not guessed; the shape is named so the answer is "a
        # different driver's file" rather than "damaged".
        if looks_like_pointer_table_module(raw):
            h["why"] = ("a nine-channel X68000 module with a 64-byte pointer "
                        "table and no MXDRV title; a different driver's file")
            h["pointer_table"] = True
            return h
        h["why"] = "no 0D 0A 1A title terminator in the first %d bytes" % MAX_TITLE
        return h
    h["title"] = decode_title(raw[:end])
    h["title_end"] = end

    nul = raw.find(b"\x00", end + 3, end + 3 + MAX_PDX_NAME)
    if nul < 0:
        h["why"] = "no NUL terminating the PDX file name"
        return h
    name = raw[end + 3:nul]
    h["pdx_name"] = decode_title(name) if name else ""
    h["has_pdx"] = bool(name)

    base = nul + 1
    h["base"] = base
    if base + 4 > len(raw):
        h["why"] = "file ends before the offset table"
        return h

    h["voice_offset"], first = struct.unpack_from(">HH", raw, base)
    # The channel count is not stored. It has to be derived from where the
    # table ENDS, and the table ends wherever the first thing after it begins
    # -- which is not always the first MML stream.
    #
    # Most tunes lay the MML streams down immediately after the table and put
    # the voices last, so `first` is the table's end. But the layout is a
    # convention, not a rule: 18 of 27,166 modules measured (the METAL SIGHT
    # set among them) write the VOICE block first, and there `first` points
    # hundreds of bytes further on. Reading it as the table end gave those
    # files 399 channels and rejected them as not-MDX.
    #
    # Whichever of the two comes first is the end of the table. A voice offset
    # of 0 is excluded because it means "no voice block" rather than "the
    # voice block is at the top of the table".
    if first < 2 or first % 2:
        h["why"] = "first MML offset %d cannot start an offset table" % first
        return h
    table_end = first
    if 2 <= h["voice_offset"] < first and h["voice_offset"] % 2 == 0:
        table_end = h["voice_offset"]
    count = (table_end - 2) // 2
    if count not in (CHANNELS_BASE, CHANNELS_MERCURY):
        # A PACKED module lands here: its title and PDX name are in the clear
        # and everything after is compressed, so the offset table is whatever
        # the compressed stream happens to start with. That is not an
        # unreadable file, it is a readable header in front of a body we
        # cannot walk, and the two deserve different answers.
        h["packer"] = packer_stamp(raw, base)
        if h["packer"]:
            h["why"] = ("the MML and voice data are packed with %s; the "
                        "offset table belongs to the unpacked form"
                        % h["packer"])
            return h
        h["why"] = ("offset table resolves to %d channels, and only 9 or 16 "
                    "occur" % count)
        return h
    h["channels"] = count

    need = base + 2 + count * 2
    if need > len(raw):
        h["why"] = "file ends inside the offset table"
        return h
    h["mml_offsets"] = list(struct.unpack_from(">%dH" % count, raw, base + 2))
    h["voice_abs"] = base + h["voice_offset"]
    h["mml_abs"] = [base + o for o in h["mml_offsets"]]
    h["ok"] = True
    return h


def looks_like_mdx(raw, filesize=None):
    """Structural identification, since the format has no magic.

    Everything before the offset table is variable-length text, so the only
    thing that can identify an MDX is whether the arithmetic lands: a title
    terminator, a NUL, and a table that resolves to a legal channel count.
    Offsets that run past the end mark a cut file, not a different format;
    `filesize` is kept for callers and no longer consulted.
    """
    h = parse_header(raw)
    if not h["ok"]:
        # a packed module is an MDX whose body cannot be walked, not a file
        # that is something else
        return bool(h.get("packer"))
    # Offsets past the end of the file do not make it something else: a cut
    # rip keeps a whole header (terminator, bank name, a 9- or 16-channel
    # table) and the walker reports each offset that dangles. 16 real modules
    # are cut like that, and no other file in the corpus has such a header.
    return True


def voice_count(raw, h):
    """How many 27-byte voices sit between the voice block and the first MML
    stream. Derived, because nothing declares it."""
    if not h["ok"] or h["voice_abs"] < 0:
        return 0
    first = min([a for a in h["mml_abs"] if a > h["voice_abs"]] or [len(raw)])
    return max(0, (min(first, len(raw)) - h["voice_abs"]) // VOICE_SIZE)


def parse_voice(raw, off):
    """One 27-byte voice as OPM register fields.

    The four-element lists are the operators in MXDRV's order: M1, C1, M2, C2
    as the chip numbers them. Values are raw register contents.
    """
    if off < 0 or off + VOICE_SIZE > len(raw):
        return None
    b = raw[off:off + VOICE_SIZE]
    v = {"number": b[0], "feedback": (b[1] >> 3) & 7, "connect": b[1] & 7,
         "slot_mask": b[2] & 0x0F}
    names = ("dt1_mul", "tl", "ks_ar", "ame_d1r", "dt2_d2r", "d1l_rr")
    for i, name in enumerate(names):
        v[name] = list(b[3 + i * 4:7 + i * 4])
    return v


# Enough for the longest title plus the sample-bank name plus the table. The
# sniffer's shared head is 20 bytes, which cannot reach an MDX offset table at
# all, so this check reads for itself the way the gzipped-Ableton one does.
SNIFF_READ = 4096


def looks_like_mdx_file(path):
    """`looks_like_mdx` for a path, reading enough to see the offset table."""
    try:
        with open_input(path) as fh:
            head = fh.read(SNIFF_READ)
        return looks_like_mdx(head, input_size(path))
    except OSError:
        return False
