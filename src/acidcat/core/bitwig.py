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

import io
import re
import struct
import zipfile

MAGIC = b"BtWg"
_MAX_LEN = 1 << 20  # sanity cap on any declared length

# string-valued meta keys worth surfacing, in display order (key, label)
_META_FIELDS = [
    ("device_name", "device"),
    ("device_id", "device_id"),
    ("device_creator", "device_creator"),
    ("device_category", "category"),
    ("device_type", "device_type"),
    ("preset_category", "preset_category"),
    ("creator", "creator"),
    ("comment", "description"),
    ("tags", "tags"),
    ("type", "content_type"),
    ("branch", "branch"),
    ("revision_id", "revision"),
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


_REF_KEYS = [
    (b"referenced_device_ids", "referenced_devices"),
    (b"referenced_module_ids", "referenced_modules"),
    (b"referenced_modulator_ids", "referenced_modulators"),
    (b"referenced_packaged_file_ids", "referenced_files"),
]


_NUM_FIELDS = [("bpm", "bpm"), ("beat_length", "beat_length")]


def parse_numeric(data):
    """Top-level f64 fields (type 0x07 = big-endian double), e.g. a note clip's
    bpm and beat length. The key is matched length-prefixed so a short name like
    'bpm' cannot match a substring. Returns {label: float}."""
    out = {}
    for key, label in _NUM_FIELDS:
        kb = struct.pack(">I", len(key)) + key.encode()
        idx = data.find(kb)
        if idx < 0:
            continue
        vp = idx + len(kb)
        if vp + 9 <= len(data) and data[vp] == 0x07:
            out[label] = struct.unpack_from(">d", data, vp + 1)[0]
    return out


def parse_parameters(data, cap=4000):
    """Every named device parameter and its value. A parameter is stored as
    [u32 keylen][UPPER_SNAKE key][u32 marker][0x07][f64 big-endian]. Values are
    in Bitwig's internal units (seconds, semitones, or normalized 0..1 depending
    on the parameter), reported raw. Returns [(name, value)]."""
    end = data.find(b"PK\x03\x04")
    if end < 0:
        end = len(data)
    out = []
    i = 14
    while i + 4 < end and len(out) < cap:
        kl = struct.unpack_from(">I", data, i)[0]
        if 2 <= kl <= 40 and i + 4 + kl + 13 <= end:
            key = data[i + 4:i + 4 + kl]
            if all(65 <= b <= 90 or b == 95 or 48 <= b <= 57 for b in key) \
                    and data[i + 4 + kl + 4] == 0x07:
                val = struct.unpack_from(">d", data, i + 4 + kl + 5)[0]
                out.append((key.decode(), val))
                i = i + 4 + kl + 13
                continue
        i += 1
    return out


def parse_references(data):
    """Counts from the referenced_*_ids arrays (type 0x19 = u32 count + items):
    the preset's dependency graph (how many devices/modules/modulators it wires
    together). Returns {label: count}."""
    out = {}
    for key, label in _REF_KEYS:
        idx = data.find(key)
        if idx < 0:
            continue
        vp = idx + len(key)
        if vp < len(data) and data[vp] == 0x19 and vp + 5 <= len(data):
            count = struct.unpack_from(">I", data, vp + 1)[0]
            if 0 <= count <= 100000:
                out[label] = count
    return out


def parse_connections(data, cap=500):
    """Grid routing paths (e.g. 'CONTENTS/MODULES/4/CONTENTS/CUTOFF'), each
    naming a destination module and parameter. Deduped, order-preserving,
    bounded. This is the patch wiring."""
    end = data.find(b"PK\x03\x04")
    if end < 0:
        end = len(data)
    seen, out = set(), []
    i = 14
    while i + 4 < end and len(out) < cap:
        ln = struct.unpack_from(">I", data, i)[0]
        if 8 <= ln <= 200 and i + 4 + ln <= end:
            s = data[i + 4:i + 4 + ln]
            if b"MODULES/" in s and all(32 <= b < 127 for b in s):
                v = s.decode("latin-1")
                if v not in seen:
                    seen.add(v)
                    out.append(v)
                i += 4 + ln
                continue
        i += 1
    return out


def _collect_paths(data, cap=4000):
    """Every length-prefixed ASCII string that contains a '/' (a Grid path
    reference). Deduped, order-preserving, bounded."""
    end = data.find(b"PK\x03\x04")
    if end < 0:
        end = len(data)
    out, seen = [], set()
    i = 14
    while i + 4 < end and len(out) < cap:
        ln = struct.unpack_from(">I", data, i)[0]
        if 3 <= ln <= 200 and i + 4 + ln <= end:
            s = data[i + 4:i + 4 + ln]
            # Grid structure paths only (exclude the type MIME, asset paths, and
            # I/O-style module names that merely contain a slash).
            if (b"MODULES" in s or b"CHAIN" in s) and b"CONTENTS" in s \
                    and all(32 <= b < 127 for b in s):
                v = s.decode("latin-1")
                if v not in seen:
                    seen.add(v)
                    out.append(v)
                i += 4 + ln
                continue
        i += 1
    return out


def parse_tree(data):
    """The nested structure tree, built from the union of Grid path references.
    Each path (e.g. CONTENTS/MODULES/4/CONTENTS/CUTOFF) is split on '/' and ':'
    into segments; merging every path forms the module/parameter hierarchy the
    patch actually addresses. Returns a nested dict {segment: subtree}."""
    tree = {}
    for path in _collect_paths(data):
        node = tree
        for seg in re.split(r"[/:]", path):
            if seg:
                node = node.setdefault(seg, {})
    return tree


def flatten_tree(tree, depth=0, out=None, cap=600):
    """Depth-first (depth, segment, is_leaf) rows for display. Numeric segments
    (module indices) sort numerically, names alphabetically after them."""
    if out is None:
        out = []

    def _key(k):
        return (0, int(k)) if k.isdigit() else (1, k.lower())

    for k in sorted(tree, key=_key):
        if len(out) >= cap:
            break
        out.append((depth, k, not tree[k]))
        flatten_tree(tree[k], depth + 1, out, cap)
    return out


def parse_structure(data, max_tokens=50000):
    """Best-effort device/module tree: the object class names in the preset,
    in pre-order (Grid modules and device-chain containers like Filter+, LFO,
    Reverb, Chain, MODULATORS). A class name is a length-prefixed ASCII token
    immediately followed by a 'CONTENTS' token. Returns a list of class names.
    Bounded so a hostile file cannot force an unbounded scan."""
    end = data.find(b"PK\x03\x04")
    if end < 0:
        end = len(data)
    toks = []
    i = 14
    while i + 4 < end and len(toks) < max_tokens:
        ln = struct.unpack_from(">I", data, i)[0]
        if 2 <= ln <= 64 and i + 4 + ln <= end:
            s = data[i + 4:i + 4 + ln]
            if all(32 <= b < 127 for b in s):
                toks.append(s.decode("latin-1"))
                i += 4 + ln
                continue
        i += 1
    return [toks[j] for j in range(len(toks) - 1)
            if toks[j + 1] == "CONTENTS" and toks[j] != "CONTENTS"
            and "/" not in toks[j] and ":" not in toks[j]]


def list_assets(data, cap=32 * 1024 * 1024):
    """List the entries in the embedded DEFLATE zip: [(name, size, raw)], where
    raw is the decompressed bytes (read with a hard cap so a zip bomb cannot
    exhaust memory) or None if too large / unreadable. [] if there is no zip."""
    z = data.find(b"PK\x03\x04")
    if z < 0:
        return []
    out = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data[z:]))
        for info in zf.infolist():
            if info.is_dir():
                continue
            raw = None
            try:
                with zf.open(info) as fh:
                    raw = fh.read(cap + 1)
                if len(raw) > cap:
                    raw = None  # exceeds the cap: refuse
            except (zipfile.BadZipFile, OSError, RuntimeError, EOFError):
                raw = None
            out.append((info.filename, info.file_size, raw))
    except (zipfile.BadZipFile, OSError):
        return []
    return out
