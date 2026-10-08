"""Kontakt 2-4 patch files (.nki, .nkm, .nkb).

Kontakt 5 and later write the NISound 'hsin' container (formats/ni.py). Kontakt
2 through 4.2 wrote this instead: a fixed header opening `12 90 a8 7f`, then a
compressed patch body, then (from 4.0) a trailer carrying a soundinfo XML.

    0x00  u32   magic 0x7fa89012
    0x04  u32   body length (0 in Kontakt 2 and 3: the body runs to EOF)
    0x08  u16   header version: 0x100, or 0x110 from Kontakt 4.2
    0x10  u8[4] application version, least significant part first
    0x14  4cc   application, byte-reversed ('2noK' is Kon2)
    0x18  u32   timestamp, Unix seconds
    0x20  u16   zones, 0x22 u16 groups, 0x24 u16 programs
    0x26  u32   total sample bytes
    0x3a  cstr  author (11 bytes), 0x45 cstr URL (to 0xa2)
    0xba  u32   uncompressed body length (header version 0x110 only)
    0xaa  body  (header version 0x100); 0xde for 0x110

Header version 0x100 compresses the body with zlib, and it inflates to an XML
document (`K2_Container`). 0x110 compresses it with FastLZ, and it decompresses
to a binary object tree that is not decoded here.

The field layout was measured on 2,279 real files, not taken from a published
spec. The counts at 0x20-0x24 matched the XML in all 1,716 files that carry it,
the sample-byte total matched `numBytesSamplesTotal` wherever the XML records
one, the body length at 0x04 matched the zlib stream in every Kontakt 4.0-4.1
file, and the uncompressed length at 0xba matched the FastLZ output in all 393
Kontakt 4.2+ files.

A monolith carries its samples in the same file: the body slot then holds a
sample container (`54 ac 70 5e`, see ni_container) instead of a compressed
patch.
"""

import datetime
import re
import struct
import zlib

MAGIC = b"\x12\x90\xa8\x7f"
CONTAINER_MAGIC = b"\x54\xac\x70\x5e"
TRAILER_MAGIC = b"\xae\xe1\x0e\xb0"

# header version -> where the body starts
BODY_AT = {0x100: 0xAA, 0x110: 0xDE}
HEADER_LEN = 0xDE

_TRAILER_HEAD = 12              # magic, four bytes, u32 XML length
_HASH_PREFIX = b"$2a$"          # a bcrypt-format string some trailers carry
_HASH_LEN = 60


def is_kontakt(head):
    return head[:4] == MAGIC


def _cstr(data, start, end):
    raw = data[start:end]
    return raw.split(b"\0", 1)[0].decode("latin-1")


def parse_header(data):
    """The fixed header as {name: (offset, length, value)}, or None when the
    header version is one this module does not lay out."""
    if len(data) < 0xAA or not is_kontakt(data):
        return None
    hv = struct.unpack_from("<H", data, 8)[0]
    if hv not in BODY_AT:
        return None
    if hv == 0x110 and len(data) < HEADER_LEN:
        return None
    u16 = lambda o: struct.unpack_from("<H", data, o)[0]    # noqa: E731
    u32 = lambda o: struct.unpack_from("<I", data, o)[0]    # noqa: E731
    h = {
        "header_version": (8, 2, hv),
        "body_length": (4, 4, u32(4)),
        "app_version": (0x10, 4, ".".join(str(b) for b in reversed(data[0x10:0x14]))),
        "application": (0x14, 4, data[0x14:0x18][::-1].decode("latin-1")),
        "timestamp": (0x18, 4, u32(0x18)),
        "zones": (0x20, 2, u16(0x20)),
        "groups": (0x22, 2, u16(0x22)),
        "programs": (0x24, 2, u16(0x24)),
        "sample_bytes": (0x26, 4, u32(0x26)),
        "author": (0x3A, 11, _cstr(data, 0x3A, 0x45)),
        "url": (0x45, 0xA2 - 0x45, _cstr(data, 0x45, 0xA2)),
    }
    if hv == 0x110:
        h["uncompressed_length"] = (0xBA, 4, u32(0xBA))
    return h


def body_start(hv):
    return BODY_AT[hv]


def timestamp_text(ts):
    if not ts:
        return "0 (not set)"
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC")


def body_kind(data, start):
    b = data[start:start + 4]
    if b == CONTAINER_MAGIC:
        return "container"
    if b[:1] == b"\x78":
        return "zlib"
    return "fastlz"


def inflate(body, cap):
    """(xml_bytes, consumed, truncated). consumed is the length of the zlib
    stream, so what follows it is the trailer. truncated means the output hit
    `cap` and the XML is a prefix."""
    d = zlib.decompressobj()
    try:
        out = d.decompress(body, cap)
    except zlib.error:
        return None, 0, False
    truncated = bool(d.unconsumed_tail)
    consumed = len(body) - len(d.unconsumed_tail) - len(d.unused_data)
    return out, consumed, truncated


def parse_trailer(data, off):
    """The trailer at `off`: {offset, length, xml_offset, xml_length, hash,
    soundinfo}, or None if there is no trailer there."""
    if data[off:off + 4] != TRAILER_MAGIC or off + _TRAILER_HEAD > len(data):
        return None
    xml_len = struct.unpack_from("<I", data, off + 8)[0]
    pos = off + _TRAILER_HEAD
    digest = None
    if data[pos:pos + 4] == _HASH_PREFIX:
        digest = data[pos:pos + _HASH_LEN].decode("latin-1")
        pos += _HASH_LEN
    xml = data[pos:pos + xml_len]
    return {"offset": off, "length": pos + len(xml) - off, "xml_offset": pos,
            "xml_length": xml_len, "xml_short": len(xml) < xml_len,
            "hash": digest, "soundinfo": parse_soundinfo(xml)}


_PROP = re.compile(rb"<(\w+)>([^<]*)</\1>")


def _unescape(s):
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            .replace("&apos;", "'").replace("&amp;", "&"))


def parse_soundinfo(xml):
    """{properties..., 'attributes': [values]} from a soundinfo document. Read
    with regex, not an XML parser, so entity expansion cannot be abused."""
    out = {}
    m = re.search(rb"<properties>(.*?)</properties>", xml, re.S)
    if m:
        for k, v in _PROP.findall(m.group(1)):
            v = _unescape(v.decode("utf-8", "replace")).strip()
            if v:
                out[k.decode("latin-1")] = v
    attrs = [_unescape(v.decode("utf-8", "replace")).strip()
             for v in re.findall(rb"<attribute>\s*<value>([^<]*)</value>", xml)]
    if attrs:
        out["attributes"] = [a for a in attrs if a]
    return out


# ── the K2 XML (header version 0x100) ──

_PROGRAM = re.compile(rb'<K2_Program index="\d+" name="([^"]*)"')
_CONTAINER = re.compile(rb'<K2_Container [^>]*type="([^"]*)"')
_FILE = re.compile(rb'<V name="file(?:_ex2?)?" value="([^"]*)"')


def parse_xml(xml):
    """What the patch XML says: container type, program names, counts, and
    the sample paths in zone order (decoded, duplicates kept)."""
    m = _CONTAINER.search(xml)
    return {
        "type": m.group(1).decode("latin-1") if m else None,
        "programs": [_unescape(p.decode("utf-8", "replace")) for p in _PROGRAM.findall(xml)],
        "zones": xml.count(b"<K2_Zone "),
        "groups": xml.count(b"<K2_Group "),
        "program_count": xml.count(b"<K2_Program "),
        "files": [decode_path(_unescape(v.decode("utf-8", "replace")))
                  for v in _FILE.findall(xml)],
    }


def decode_path(s):
    """Kontakt's encoded path to a readable one.

    '@' then tokens: v### volume, d### directory (### is the name's length),
    b one directory up, and F then an 11-character field and the file name to
    the end. '@v001Cd005Usersd003fooF00000016000kick.wav' is C:/Users/foo/kick.wav.
    Anything else is returned as stored."""
    if not s.startswith("@"):
        return s
    parts, vol, i = [], None, 1
    while i < len(s):
        t = s[i]
        if t in "vd" and s[i + 1:i + 4].isdigit():
            n = int(s[i + 1:i + 4])
            name = s[i + 4:i + 4 + n]
            if len(name) != n:
                return s
            if t == "v":
                vol = name
            else:
                parts.append(name)
            i += 4 + n
        elif t == "b":
            parts.append("..")
            i += 1
        elif t == "F" and len(s) > i + 12:
            parts.append(s[i + 12:])
            break
        else:
            return s
    path = "/".join(parts)
    return f"{vol}:/{path}" if vol else path
