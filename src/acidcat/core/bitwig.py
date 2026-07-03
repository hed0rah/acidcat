"""Bitwig BtWg container reader.

Bitwig .bwpreset / .bwclip / .bwmodulator files share a "BtWg" header followed
by a big-endian, length-prefixed tagged key/value structure. This reads the
metadata block (device, creator, category, tags, description, version). The
device/module tree and any embedded-asset zip are left opaque here; a walker
can note the zip and a caller can unzip it separately.

Grammar (reverse-engineered from real Bitwig Studio 6.x files): every token is
a u32-BE length followed by that many bytes. A meta entry is a key token whose
value is a type byte (0x08 = string) then a u32-BE length then the value bytes.
"""

import struct

MAGIC = b"BtWg"
_MAX_LEN = 1 << 20  # sanity cap on any declared length

# string-valued meta keys worth surfacing, in display order (key, label)
_META_FIELDS = [
    ("device_name", "device"),
    ("device_creator", "device_creator"),
    ("device_category", "category"),
    ("device_type", "device_type"),
    ("preset_category", "preset_category"),
    ("creator", "creator"),
    ("comment", "description"),
    ("tags", "tags"),
    ("application_version_name", "bitwig_version"),
]
_META_KEYS_BYTES = {k.encode(): k for k, _ in _META_FIELDS}


def is_bitwig(filepath):
    try:
        with open(filepath, "rb") as f:
            return f.read(4) == MAGIC
    except OSError:
        return False


def read_header(data):
    """The header is 'BtWg' plus ascii format/version digits. Return the
    version string (the 10 chars after the magic, e.g. '0003000200')."""
    return data[4:14].decode("latin-1", errors="replace")


def parse_meta(data):
    """Walk the tagged stream and pull the string-valued meta keys. Every
    length is bounds-checked against the buffer, so a hostile length is
    ignored rather than trusted. Returns {key: value}."""
    meta = {}
    # start after the 14-byte header; bound the scan (the meta block is always
    # near the start, so a hostile BtWg-magic file cannot force a full-buffer
    # byte-at-a-time scan).
    i, n = 14, min(len(data), 14 + 262144)
    while i + 4 <= n and len(meta) < len(_META_KEYS_BYTES):
        ln = struct.unpack_from(">I", data, i)[0]
        if 1 <= ln <= 256 and i + 4 + ln <= n:
            tok = data[i + 4:i + 4 + ln]
            key = _META_KEYS_BYTES.get(tok)
            if key and key not in meta:
                vp = i + 4 + ln  # value: 0x08 type, u32 len, bytes
                if vp < n and data[vp] == 0x08 and vp + 5 <= n:
                    vlen = struct.unpack_from(">I", data, vp + 1)[0]
                    if 0 <= vlen <= _MAX_LEN and vp + 5 + vlen <= n:
                        meta[key] = data[vp + 5:vp + 5 + vlen].decode(
                            "utf-8", errors="replace")
                        i = vp + 5 + vlen
                        continue
        i += 1
    return meta
