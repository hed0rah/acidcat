"""Native Instruments preset reader (NISound 'hsin' container).

Massive (.nmsv), Absynth (.nabs), FM8, Reaktor, and modern Kontakt (.nki/.nkm)
share the NISound container: an EBML-like tree of size-prefixed frames headed
by the 'hsin' magic at offset 0x0C. The metadata (the SoundInfoItem: preset
name, product, version) is stored as plain UTF-16LE and needs no decompression;
only the preset payload itself is FastLZ-compressed and is left opaque here.

Implemented from byte-level facts only (verified against real Massive and
Absynth presets), not from any third-party source.
"""

import re
import struct
import zlib

MAGIC_OFFSET = 0x0C
MAGIC = b"hsin"
KSD_MAGIC = b"-in-"

# app names as they appear (UTF-16LE) in the SoundInfoItem
_APP_NAMES = {"Massive", "Massive X", "Absynth", "FM8", "Reaktor", "Kontakt",
              "Guitar Rig", "Battery"}
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")


def is_ni_hsin(data):
    return len(data) >= 0x30 and data[MAGIC_OFFSET:MAGIC_OFFSET + 4] == MAGIC


def _u16le_pascals(data, limit=1 << 20):
    """Collect (offset, text) for u32-count-prefixed UTF-16LE strings in the
    metadata region. Every length is bounds-checked."""
    out = []
    i, n = 0x30, min(len(data), limit)
    while i + 4 <= n:
        c = struct.unpack_from("<I", data, i)[0]
        if 1 <= c <= 256 and i + 4 + c * 2 <= len(data):
            raw = data[i + 4:i + 4 + c * 2]
            try:
                s = raw.decode("utf-16-le")
            except UnicodeDecodeError:
                i += 1
                continue
            if s and all(0x20 <= ord(ch) < 0xFFFF for ch in s):
                out.append((i, s))
                i += 4 + c * 2
                continue
        i += 1
    return out


def is_ni_ksd(data):
    return data[:4] == KSD_MAGIC


def _safe_inflate(chunk, maxlen=8 * 1024 * 1024):
    """zlib-inflate with an output cap so a decompression bomb cannot exhaust
    memory. Returns the bytes, or None on error or if the stream exceeds the
    cap."""
    try:
        d = zlib.decompressobj()
        out = d.decompress(chunk, maxlen)
    except (zlib.error, MemoryError):
        return None
    if d.unconsumed_tail:  # more than the cap remained: refuse
        return None
    return out


# .ksd (old Absynth / KORE): the doc header is a zlib-compressed XML blob.
_KSD_FIELDS = [
    ("name", r"<doc_name>(.*?)</doc_name>"),
    ("author", r"<Author>(.*?)</Author>"),
    ("vendor", r"<Vendor>(.*?)</Vendor>"),
    ("bank", r"<Bankname>(.*?)</Bankname>"),
    ("comment", r"<Comment>(.*?)</Comment>"),
    ("plugin", r"<Plugin>(.*?)</Plugin>"),
    ("device_type", r'<DeviceType A="(.*?)"'),
    ("tempo", r'<MusicalAttr Tempo="(.*?)"'),
    ("genre", r"<Genre>(.*?)</Genre>"),
    ("key", r"<Key>(.*?)</Key>"),
]


def parse_ksd(data):
    """Extract the metadata from a .ksd (name, author, vendor, bank, comment,
    plugin, device type, tempo/genre/key), or None if not a .ksd. The XML is
    read with regex (no XML parser, so no entity-expansion DoS)."""
    if not is_ni_ksd(data):
        return None
    xml = None
    for m in re.finditer(rb'\x78[\x01\x9c\xda]', data):  # zlib stream markers
        dec = _safe_inflate(data[m.start():])
        if dec and b"NI_DOC_HEADER" in dec:
            s = dec.find(b"<?xml")
            if s >= 0:
                xml = dec[s:].decode("utf-8", errors="replace")
                break
    if xml is None:
        return None
    meta = {}
    for label, pat in _KSD_FIELDS:
        mm = re.search(pat, xml, re.S)
        if mm:
            v = mm.group(1).strip()
            if v and v.lower() != "not set" and not (label == "tempo" and v == "0"):
                meta[label] = v
    return meta or None


def parse_hsin(data):
    """Extract {product, version, name} from the hsin metadata, or None if the
    bytes are not an hsin container."""
    if not is_ni_hsin(data):
        return None
    meta = {}
    strings = _u16le_pascals(data)
    app_off = None
    for off, s in strings:
        if s in _APP_NAMES:
            meta["product"], app_off = s, off
            break
    for off, s in strings:
        if _VERSION_RE.match(s):
            meta["version"] = s
            break
    # the SoundInfoItem name sits before the app-name string; take the last
    # alphabetic, non-version pascal string in that region.
    name = None
    for off, s in strings:
        if app_off is not None and off >= app_off:
            break
        if s == meta.get("version") or s in _APP_NAMES:
            continue
        if any(ch.isalpha() for ch in s):
            name = s
    if name:
        meta["name"] = name
    return meta
