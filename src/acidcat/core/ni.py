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
            # require mostly-ASCII printable text, so binary noise that happens
            # to decode as valid UTF-16LE (e.g. a stray CJK codepoint) is not
            # mistaken for a metadata string.
            printable = sum(ch.isascii() and ch.isprintable() for ch in s)
            if len(s) >= 2 and printable >= len(s) * 0.7:
                out.append((i, s))
                i += 4 + c * 2
                continue
        i += 1
    return out


def fastlz_decompress(src, max_out=32 * 1024 * 1024):
    """FastLZ level-1 decompression (pure Python). NI compresses the hsin
    subtree payload (item 115) with this. Returns the decompressed bytes, or
    None if the output would exceed max_out (a decompression-bomb guard)."""
    dst = bytearray()
    ip, n = 0, len(src)
    if n == 0:
        return b""
    ctrl = src[ip]
    ip += 1
    while True:
        if ctrl >= 32:  # back-reference
            length = ctrl >> 5
            ofs = (ctrl & 0x1F) << 8
            if length == 7:
                if ip >= n:
                    break
                length += src[ip]
                ip += 1
            if ip >= n:
                break
            ofs += src[ip]
            ip += 1
            length += 2
            ref = len(dst) - ofs - 1
            if ref < 0:
                break
            for _ in range(length):
                if ref >= len(dst):
                    break
                dst.append(dst[ref])
                ref += 1
        else:  # literal run of ctrl+1 bytes
            length = ctrl + 1
            dst.extend(src[ip:ip + length])
            ip += length
        if len(dst) > max_out:
            return None
        if ip < n:
            ctrl = src[ip]
            ip += 1
        else:
            break
    return bytes(dst)


def decompress_subtree(data, max_attempts=64):
    """Locate the FastLZ-compressed subtree (item 115) in an hsin preset and
    return its decompressed inner container, or None. The payload header is
    u32=1, u8=1, u32 uncompressed_size, u32 compressed_size, then FastLZ."""
    attempts = 0
    for m in range(0x30, len(data) - 13):
        if data[m:m + 5] != b"\x01\x00\x00\x00\x01":
            continue
        uncomp = struct.unpack_from("<I", data, m + 5)[0]
        comp = struct.unpack_from("<I", data, m + 9)[0]
        if not (16 < comp < len(data) and 16 < uncomp < 64 * 1024 * 1024
                and m + 13 + comp <= len(data)):
            continue
        attempts += 1
        if attempts > max_attempts:
            break
        out = fastlz_decompress(data[m + 13:m + 13 + comp], uncomp + 16)
        if out is not None and len(out) == uncomp:
            return out
    return None


def is_ni_ksd(data):
    return data[:4] == KSD_MAGIC


def is_ni_nksf(data):
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"NIKS"


# ── minimal MessagePack decoder (only what the NISI metadata map uses) ──


def _mp_str(b, pos, n):
    if pos + n > len(b):
        raise ValueError("mp str overruns")
    return b[pos:pos + n].decode("utf-8", errors="replace"), pos + n


def _mp_decode(b, pos=0, depth=0):
    if depth > 32 or pos >= len(b):
        return None, pos
    t = b[pos]
    pos += 1
    if t < 0x80:
        return t, pos                      # positive fixint
    if t >= 0xe0:
        return t - 256, pos                # negative fixint
    if 0x80 <= t <= 0x8f:
        return _mp_map(b, pos, t & 0x0F, depth)
    if 0x90 <= t <= 0x9f:
        return _mp_array(b, pos, t & 0x0F, depth)
    if 0xa0 <= t <= 0xbf:
        return _mp_str(b, pos, t & 0x1F)
    if t == 0xc0:
        return None, pos                   # nil
    if t == 0xc2:
        return False, pos
    if t == 0xc3:
        return True, pos
    if t == 0xd9:                          # str8
        return _mp_str(b, pos + 1, b[pos])
    if t == 0xda:                          # str16
        return _mp_str(b, pos + 2, struct.unpack_from(">H", b, pos)[0])
    if t == 0xdb:                          # str32
        return _mp_str(b, pos + 4, struct.unpack_from(">I", b, pos)[0])
    if t == 0xde:                          # map16
        return _mp_map(b, pos + 2, struct.unpack_from(">H", b, pos)[0], depth)
    if t == 0xdc:                          # array16
        return _mp_array(b, pos + 2, struct.unpack_from(">H", b, pos)[0], depth)
    raise ValueError(f"unsupported msgpack type 0x{t:02x}")


def _mp_map(b, pos, count, depth):
    out = {}
    for _ in range(count):
        k, pos = _mp_decode(b, pos, depth + 1)
        v, pos = _mp_decode(b, pos, depth + 1)
        out[k] = v
    return out, pos


def _mp_array(b, pos, count, depth):
    out = []
    for _ in range(count):
        v, pos = _mp_decode(b, pos, depth + 1)
        out.append(v)
    return out, pos


def _mp_encode(obj):
    """Minimal MessagePack encoder (inverse of _mp_decode): dict/str/list/int/
    nil/bool. Any valid encoding decodes to the same values, so re-encoding the
    NISI map is spec-safe."""
    out = bytearray()

    def enc(o):
        if o is None:
            out.append(0xC0)
        elif o is True:
            out.append(0xC3)
        elif o is False:
            out.append(0xC2)
        elif isinstance(o, int):
            if 0 <= o < 128:
                out.append(o)
            elif -32 <= o < 0:
                out.append(o & 0xFF)
            elif 0 <= o < 256:
                out.extend((0xCC, o))
            elif 0 <= o < 65536:
                out.extend(b"\xcd" + struct.pack(">H", o))
            else:
                out.extend(b"\xce" + struct.pack(">I", o & 0xFFFFFFFF))
        elif isinstance(o, str):
            b = o.encode("utf-8")
            if len(b) < 32:
                out.append(0xA0 | len(b))
            elif len(b) < 256:
                out.extend((0xD9, len(b)))
            else:
                out.extend(b"\xda" + struct.pack(">H", len(b)))
            out.extend(b)
        elif isinstance(o, list):
            if len(o) < 16:
                out.append(0x90 | len(o))
            else:
                out.extend(b"\xdc" + struct.pack(">H", len(o)))
            for x in o:
                enc(x)
        elif isinstance(o, dict):
            if len(o) < 16:
                out.append(0x80 | len(o))
            else:
                out.extend(b"\xde" + struct.pack(">H", len(o)))
            for k, v in o.items():
                enc(k)
                enc(v)
        else:
            raise ValueError(f"cannot encode {type(o).__name__}")
    enc(obj)
    return bytes(out)


_NKSF_EDIT = {
    "name": "name", "title": "name",
    "author": "author", "creator": "author",
    "vendor": "vendor",
    "comment": "comment", "description": "comment",
}


def edit_nksf(data, changes):
    """Edit the NISI MessagePack metadata in a .nksf (RIFF/NIKS): decode the
    full map, edit fields, re-encode, rewrite the RIFF with correct sizes.
    Returns (new_bytes, applied)."""
    if not is_ni_nksf(data):
        raise ValueError("not a .nksf preset")
    n = len(data)
    pos, chunks = 12, []
    while pos + 8 <= n:
        cid = data[pos:pos + 4]
        sz = struct.unpack_from("<I", data, pos + 4)[0]
        if pos + 8 + sz > n:
            raise ValueError(f"nksf chunk {cid!r} overruns file")
        chunks.append([cid, data[pos + 8:pos + 8 + sz]])
        pos += 8 + sz + (sz & 1)
    nisi = next((c for c in chunks if c[0] == b"NISI"), None)
    if nisi is None:
        raise ValueError("no NISI chunk to edit")
    ver = struct.unpack_from("<I", nisi[1], 0)[0]
    obj, _ = _mp_decode(nisi[1][4:])
    if not isinstance(obj, dict):
        raise ValueError("NISI payload is not a map")
    applied = []
    for field, value in changes.items():
        key = _NKSF_EDIT.get(field.lower())
        if key is None:
            raise ValueError(f"nksf has no editable field {field!r}")
        old = obj.get(key)
        obj[key] = "" if value is None else str(value)
        applied.append((field, old, obj[key]))
    nisi[1] = struct.pack("<I", ver) + _mp_encode(obj)
    out = bytearray(b"RIFF\x00\x00\x00\x00NIKS")
    for cid, payload in chunks:
        out += cid + struct.pack("<I", len(payload)) + payload
        if len(payload) & 1:
            out += b"\x00"
    struct.pack_into("<I", out, 4, len(out) - 8)
    return bytes(out), applied


def parse_nksf(data):
    """RIFF/NIKS (.nksf): decode the NISI MessagePack metadata (name, author,
    vendor, comment, device type, bankchain), or None if not a .nksf."""
    if not is_ni_nksf(data):
        return None
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        sz = struct.unpack_from("<I", data, pos + 4)[0]
        if cid == b"NISI" and pos + 8 + sz <= len(data):
            mp = data[pos + 12:pos + 8 + sz]  # after the u32 version
            try:
                obj, _ = _mp_decode(mp)
            except (ValueError, IndexError, struct.error):
                return None
            if not isinstance(obj, dict):
                return None
            meta = {}
            for src, dst in (("name", "name"), ("author", "author"),
                             ("vendor", "vendor"), ("comment", "comment"),
                             ("deviceType", "device_type")):
                v = obj.get(src)
                if v:
                    meta[dst] = v
            bc = obj.get("bankchain")
            if isinstance(bc, list):
                bc = [str(x) for x in bc if x]
                if bc:
                    meta["bank"] = " / ".join(bc)
            return meta or None
        pos += 8 + sz + (sz & 1)
    return None


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


# field -> (xml tag) for .ksd NI_DOC_HEADER editing
_KSD_EDIT = {
    "name": "doc_name", "title": "doc_name",
    "author": "Author", "creator": "Author",
    "vendor": "Vendor",
    "bank": "Bankname",
    "comment": "Comment", "description": "Comment",
}


def _xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def edit_ksd(data, changes):
    """Edit a .ksd (KORE/Absynth): decompress the NI_DOC_HEADER XML blob, patch
    the requested tags, re-compress, and update the cascading size fields
    (internal XML length, uncompSize, compSize). Returns (new_bytes, applied)."""
    if not is_ni_ksd(data):
        raise ValueError("not a .ksd preset")
    z = blob = comp_len = None
    for m in re.finditer(rb'\x78[\x01\x9c\xda]', data):
        d = zlib.decompressobj()
        try:
            dec = d.decompress(data[m.start():])
        except zlib.error:
            continue
        if b"NI_DOC_HEADER" in dec:
            z, blob = m.start(), dec
            comp_len = len(data[m.start():]) - len(d.unused_data)
            break
    if z is None:
        raise ValueError("no NI_DOC_HEADER blob found")
    xstart = blob.find(b"<?xml")
    if xstart < 0:
        raise ValueError("no XML in the doc header")
    header = blob[:xstart]
    xml = blob[xstart:].decode("utf-8", "replace")
    applied = []
    for field, value in changes.items():
        tag = _KSD_EDIT.get(field.lower())
        if tag is None:
            raise ValueError(f".ksd has no editable field {field!r}")
        pat = re.compile(rf"(<{tag}>)(.*?)(</{tag}>)", re.S)
        mo = pat.search(xml)
        if mo is None:
            raise ValueError(f"tag <{tag}> not present in this preset")
        old = mo.group(2)
        repl = _xml_escape("" if value is None else str(value))
        xml = pat.sub(lambda m2: m2.group(1) + repl + m2.group(3), xml, count=1)
        applied.append((field, old, value))
    xml_b = xml.encode("utf-8")
    new_header = bytearray(header)
    if header[:4] == b" LMX" and len(header) >= 12:  # reversed 'XML ' + ver + len
        struct.pack_into("<I", new_header, 8, len(xml_b))
    new_blob = bytes(new_header) + xml_b
    new_zlib = zlib.compress(new_blob, 9)
    out = bytearray(data)
    out[z:z + comp_len] = new_zlib
    struct.pack_into("<I", out, z - 8, len(new_zlib))   # compSize
    struct.pack_into("<I", out, z - 4, len(new_blob))   # uncompSize
    return bytes(out), applied


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
    app_off = version_off = None
    for off, s in strings:
        if s in _APP_NAMES:
            meta["product"], app_off = s, off
            break
    for off, s in strings:
        if _VERSION_RE.match(s):
            meta["version"], version_off = s, off
            break
    # the SoundInfoItem name is the FIRST alphabetic, non-version pascal string
    # after the version; author/vendor/description are the fields right after it
    # (positional, so they read correctly even when the name field is populated).
    name_end = None
    for off, s in strings:
        if version_off is not None and off <= version_off:
            continue
        if app_off is not None and off >= app_off:
            break
        if s == meta.get("version") or s in _APP_NAMES:
            continue
        if any(ch.isalpha() for ch in s):
            meta["name"] = s
            name_end = off + 4 + len(s) * 2
            break
    if name_end is not None:
        pos = name_end
        for label in ("author", "vendor", "description"):
            if (app_off is not None and pos >= app_off) or pos + 4 > len(data):
                break
            c = struct.unpack_from("<I", data, pos)[0]
            if c > 256 or pos + 4 + c * 2 > len(data):
                break
            val = data[pos + 4:pos + 4 + c * 2].decode(
                "utf-16-le", errors="replace").strip()
            if val:
                meta[label] = val
            pos += 4 + c * 2
    return meta
