"""AppleDouble (and AppleSingle) files: the `._name` sidecars macOS writes
beside a file on a volume that cannot hold its metadata, and that ride along
in every zip made on a Mac (`__MACOSX/`).

RFC 1740, big-endian throughout:

    0x00  u32  magic: 0x00051607 AppleDouble, 0x00051600 AppleSingle
    0x04  u32  version, 0x00020000
    0x08  16   filler ('Mac OS X        ' from macOS)
    0x18  u16  entry count
    0x1a       entries: u32 id, u32 offset, u32 length

Entry ids: 1 data fork, 2 resource fork, 3 real name, 4 comment, 5 icon,
6 colour icon, 8 file dates, 9 Finder info, 10 Macintosh file info,
11 ProDOS file info, 12 MS-DOS file info, 13 AFP short name, 14 AFP file
info, 15 AFP directory id.

macOS extends the Finder info entry: 32 bytes of FInfo/FXInfo (file type and
creator first), two bytes of padding, then an extended-attribute block:

    'ATTR', u32 debug tag, u32 total size, u32 data start, u32 data length,
    12 reserved bytes, u16 flags, u16 attribute count, then per attribute
    u32 offset, u32 length, u16 flags, u8 name length, the name with its NUL,
    padded to four bytes

Attribute offsets count from the start of the file.
"""

import re
import struct

DOUBLE = b"\x00\x05\x16\x07"
SINGLE = b"\x00\x05\x16\x00"
ENTRY_NAMES = {1: "data fork", 2: "resource fork", 3: "real name", 4: "comment",
               5: "icon", 6: "colour icon", 8: "file dates", 9: "Finder info",
               10: "Macintosh file info", 11: "ProDOS file info",
               12: "MS-DOS file info", 13: "AFP short name", 14: "AFP file info",
               15: "AFP directory id"}
FINDER_INFO_LEN = 32
_ATTR_HEAD = 36


def kind(head):
    if head[:4] == DOUBLE:
        return "AppleDouble"
    if head[:4] == SINGLE:
        return "AppleSingle"
    return None


def looks_like(head):
    """The magic and a version RFC 1740 or its predecessor defines."""
    return kind(head) is not None and head[4:8] in (b"\x00\x02\x00\x00", b"\x00\x01\x00\x00")


def entries(data, max_entries):
    """[(id, offset, length, entry_at)], and how many the header claims."""
    if len(data) < 26:
        return [], 0
    n = struct.unpack_from(">H", data, 24)[0]
    out = []
    for i in range(min(n, max_entries)):
        at = 26 + 12 * i
        if at + 12 > len(data):
            break
        eid, off, ln = struct.unpack_from(">III", data, at)
        out.append((eid, off, ln, at))
    return out, n


def attributes(data, finder_at, finder_len, max_attrs):
    """The extended attributes after the Finder info, as
    [(name, value_offset, value_length, entry_at)], plus the block's offset,
    or ([], None) when the entry holds only Finder info."""
    at = finder_at + FINDER_INFO_LEN + 2
    if (finder_len < FINDER_INFO_LEN + 2 + _ATTR_HEAD or at + _ATTR_HEAD > len(data)
            or data[at:at + 4] != b"ATTR"):
        return [], None
    count = struct.unpack_from(">H", data, at + 34)[0]
    out = []
    pos = at + _ATTR_HEAD
    for _ in range(min(count, max_attrs)):
        if pos + 11 > len(data):
            break
        off, ln, _flags, nlen = struct.unpack_from(">IIHB", data, pos)
        name = data[pos + 11:pos + 11 + nlen].split(b"\0", 1)[0].decode("utf-8", "replace")
        out.append((name, off, ln, pos))
        pos += (11 + nlen + 3) & ~3
    return out, at


def quarantine(value):
    """com.apple.quarantine: 'flags;hex unix time;agent;uuid'. Returns
    (agent, unix_time) or None."""
    parts = value.split(b";")
    if len(parts) < 3:
        return None
    try:
        ts = int(parts[1], 16)
    except ValueError:
        ts = None
    # the agent escapes spaces and other bytes as \xNN
    agent = re.sub(rb"\\x([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), parts[2])
    return agent.decode("utf-8", "replace"), ts
