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

MAGIC_OFFSET = 0x0C
MAGIC = b"hsin"

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
