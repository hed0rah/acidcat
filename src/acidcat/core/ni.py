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
    # locate the sentinel with a C-level find (not a Python byte-by-byte scan,
    # which a crafted sentinel-free file could stretch to seconds).
    m = data.find(b"\x01\x00\x00\x00\x01", 0x30)
    while 0 <= m <= len(data) - 13:
        uncomp = struct.unpack_from("<I", data, m + 5)[0]
        comp = struct.unpack_from("<I", data, m + 9)[0]
        if (16 < comp < len(data) and 16 < uncomp < 64 * 1024 * 1024
                and m + 13 + comp <= len(data)):
            attempts += 1
            if attempts > max_attempts:
                break
            out = fastlz_decompress(data[m + 13:m + 13 + comp], uncomp + 16)
            if out is not None and len(out) == uncomp:
                return out
        m = data.find(b"\x01\x00\x00\x00\x01", m + 1)
    return None


def is_ni_ksd(data):
    return data[:4] == KSD_MAGIC


# ── hsin writing (frame-size cascade; verified against Massive + Absynth) ──

_HSIN_DOMAINS = (b"DSIN", b"4KIN", b"NISD")
# field -> index of the UTF-16LE pascal string in the SoundInfoItem(108) payload
_HSIN_EDIT = {"name": 0, "title": 0, "author": 1, "creator": 1,
              "vendor": 2, "comment": 3, "description": 3}


def _hsin_walk(data, off, fields, depth=0):
    """Walk the hsin frame at off, recording every size field as
    (field_offset, width, span_start, span_end). Returns
    [(item_id, frame_off, data_start, data_end, payload_start), ...]."""
    if depth > 128:
        raise ValueError("hsin nesting too deep")
    frames = []
    fs = struct.unpack_from("<Q", data, off)[0]
    if data[off + 12:off + 16] != b"hsin" or off + fs > len(data):
        raise ValueError(f"bad hsin frame at {off:#x}")
    ds = struct.unpack_from("<Q", data, off + 0x28)[0]
    data_start, data_end, frame_end = off + 0x30, off + 0x30 + ds, off + fs
    if data_end > frame_end:
        raise ValueError("data section overruns frame")
    fields.append((off, 8, off, frame_end))               # frame_size (inclusive)
    fields.append((off + 0x28, 8, data_start, data_end))  # data_size (exclusive)
    item_id, payload_start, pos = None, data_start, data_start
    while pos + 12 <= data_end and data[pos:pos + 4] in _HSIN_DOMAINS:
        iid = struct.unpack_from("<I", data, pos + 4)[0]
        if item_id is None:
            item_id = iid
        if iid == 1:
            payload_start = max(payload_start, pos + 24)
            break
        inner = struct.unpack_from("<Q", data, pos + 12)[0]
        inner_start, inner_end = pos + 20, pos + 20 + inner
        if inner_end > data_end:
            raise ValueError("stack inner_size overruns data")
        fields.append((pos + 12, 8, inner_start, inner_end))
        payload_start = max(payload_start, inner_end)
        pos = inner_start
    frames.append((item_id, off, data_start, data_end, payload_start))
    pos = data_end
    while pos < frame_end:
        if pos + 20 > frame_end or data[pos + 4:pos + 8] not in _HSIN_DOMAINS:
            raise ValueError("bad child prefix")
        child_off = pos + 12
        cfs = struct.unpack_from("<Q", data, child_off)[0]
        frames.extend(_hsin_walk(data, child_off, fields, depth + 1))
        pos = child_off + cfs
    if pos != frame_end:
        raise ValueError("children do not fill frame")
    return frames


def _edit_hsin_string(data, index, new_value):
    """Replace the index-th SoundInfoItem string (0 name, 1 author, 2 vendor,
    3 description) and bump every enclosing size field. Returns (new_bytes, old)."""
    fields = []
    frames = _hsin_walk(data, 0, fields)
    info = [f for f in frames if f[0] == 108]
    if len(info) != 1:
        raise ValueError(f"expected one SoundInfoItem(108), found {len(info)}")
    _, _, _, d_end, payload = info[0]
    if payload + 8 > d_end or struct.unpack_from("<I", data, payload)[0] != 1:
        raise ValueError("unexpected SoundInfoItem payload")
    off = payload + 8
    for i in range(index + 1):
        if off + 4 > d_end:
            raise ValueError("info string index out of range")
        count = struct.unpack_from("<I", data, off)[0]
        if count > 0x10000 or off + 4 + count * 2 > d_end:
            raise ValueError("info string overruns SoundInfoItem")
        if i == index:
            break
        off += 4 + count * 2
    str_end = off + 4 + count * 2
    old = data[off + 4:str_end].decode("utf-16-le", "replace")
    enc = new_value.encode("utf-16-le")
    new_field = struct.pack("<I", len(enc) // 2) + enc
    delta = len(new_field) - (4 + count * 2)
    out = bytearray(data)
    out[off:str_end] = new_field
    for foff, width, s0, s1 in fields:
        if s0 <= off and str_end <= s1:
            v = struct.unpack_from("<Q", data, foff)[0]
            struct.pack_into("<Q", out, foff, v + delta)
    return bytes(out), old


def edit_hsin(data, changes):
    """Edit hsin (Massive/Absynth) preset metadata: name, author, vendor,
    description. Cascades the enclosing frame-size fields. Returns
    (new_bytes, applied). EXPERIMENTAL: confirm reload in the app."""
    if not is_ni_hsin(data):
        raise ValueError("not an hsin preset")
    out, applied = bytes(data), []
    for field, value in changes.items():
        idx = _HSIN_EDIT.get(field.lower())
        if idx is None:
            raise ValueError(f"hsin preset has no editable field {field!r}")
        out, old = _edit_hsin_string(out, idx, "" if value is None else str(value))
        applied.append((field, old, value))
    return out, applied


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
    if t == 0xdf:                          # map32
        return _mp_map(b, pos + 4, struct.unpack_from(">I", b, pos)[0], depth)
    if t == 0xdc:                          # array16
        return _mp_array(b, pos + 2, struct.unpack_from(">H", b, pos)[0], depth)
    if t == 0xdd:                          # array32
        return _mp_array(b, pos + 4, struct.unpack_from(">I", b, pos)[0], depth)
    if t == 0xcc:                          # uint8
        return b[pos], pos + 1
    if t == 0xcd:                          # uint16
        return struct.unpack_from(">H", b, pos)[0], pos + 2
    if t == 0xce:                          # uint32
        return struct.unpack_from(">I", b, pos)[0], pos + 4
    if t == 0xcf:                          # uint64
        return struct.unpack_from(">Q", b, pos)[0], pos + 8
    if t == 0xd0:                          # int8
        return struct.unpack_from(">b", b, pos)[0], pos + 1
    if t == 0xd1:                          # int16
        return struct.unpack_from(">h", b, pos)[0], pos + 2
    if t == 0xd2:                          # int32
        return struct.unpack_from(">i", b, pos)[0], pos + 4
    if t == 0xd3:                          # int64
        return struct.unpack_from(">q", b, pos)[0], pos + 8
    if t == 0xca:                          # float32
        return struct.unpack_from(">f", b, pos)[0], pos + 4
    if t == 0xcb:                          # float64
        return struct.unpack_from(">d", b, pos)[0], pos + 8
    if t in (0xc4, 0xc5, 0xc6):            # bin8 / bin16 / bin32
        w = {0xc4: 1, 0xc5: 2, 0xc6: 4}[t]
        n = int.from_bytes(b[pos:pos + w], "big")
        return bytes(b[pos + w:pos + w + n]), pos + w + n
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
                out.extend(b"\xcc" + struct.pack(">B", o))
            elif -128 <= o < 128:
                out.extend(b"\xd0" + struct.pack(">b", o))
            elif 0 <= o < 65536:
                out.extend(b"\xcd" + struct.pack(">H", o))
            elif -32768 <= o < 32768:
                out.extend(b"\xd1" + struct.pack(">h", o))
            elif 0 <= o < 2 ** 32:
                out.extend(b"\xce" + struct.pack(">I", o))
            elif -2 ** 31 <= o < 2 ** 31:
                out.extend(b"\xd2" + struct.pack(">i", o))
            elif 0 <= o < 2 ** 64:
                out.extend(b"\xcf" + struct.pack(">Q", o))
            else:
                out.extend(b"\xd3" + struct.pack(">q", o))
        elif isinstance(o, float):
            out.extend(b"\xcb" + struct.pack(">d", o))
        elif isinstance(o, str):
            b = o.encode("utf-8")
            if len(b) < 32:
                out.append(0xA0 | len(b))
            elif len(b) < 256:
                out.extend((0xD9, len(b)))
            elif len(b) < 65536:
                out.extend(b"\xda" + struct.pack(">H", len(b)))
            else:
                out.extend(b"\xdb" + struct.pack(">I", len(b)))
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
    attempts = 0
    for m in re.finditer(rb'\x78[\x01\x9c\xda]', data):
        attempts += 1
        if attempts > 32:
            break
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
    attempts = 0
    for m in re.finditer(rb'\x78[\x01\x9c\xda]', data):  # zlib stream markers
        attempts += 1
        if attempts > 32:  # bound inflate cost on a marker-flooded file
            break
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
