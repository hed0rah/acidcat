"""Low-level byte-dissection primitives: the RE-tool surface for reading a file
as bytes rather than as decoded structure.

Where ``inspect`` shows the decoded structure and ``carve`` extracts a region,
``probe`` is the scalpel for looking at raw bytes the way a reverse engineer does
in radare2 / pwndbg / a hex editor: read an offset as a typed value, scan for a
value, find a byte pattern, pull printable strings, hexdump, and diff two files.

The one thing that makes this acidcat and not ``xxd`` + ``grep``: an address can
be a raw offset (``0x2c`` / decimal) OR a structural name (``data``, a chunk id,
or ``fmt .sample_rate``, a chunk field), resolved through the walker. So you can
say "read the fmt chunk's sample_rate as a u32" without counting bytes.
"""

import os
import struct

from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported

# type token -> (struct code, byte size)
FMT_STRUCT = {
    "u8": ("B", 1), "i8": ("b", 1), "u16": ("H", 2), "i16": ("h", 2),
    "u24": (None, 3), "u32": ("I", 4), "i32": ("i", 4),
    "u64": ("Q", 8), "i64": ("q", 8), "f32": ("f", 4), "f64": ("d", 8),
}
# formats acidcat walks whose integers are big-endian
_BE_LABELS = ("AIFF", "AIFC", "MP4", "MIDI")


def parse_int(s):
    """A hex (0x..), decimal, or negative integer from a string."""
    s = s.strip()
    neg = s.startswith("-")
    body = s[1:] if neg else s
    v = int(body, 16) if body.lower().startswith("0x") else int(body)
    return -v if neg else v


def default_byteorder(label):
    """'big' for the containers acidcat walks that store big-endian integers,
    else 'little'."""
    return "big" if any(t in (label or "") for t in _BE_LABELS) else "little"


def _walk(filepath):
    try:
        return walk_file(filepath)
    except (Unsupported, OSError, ValueError):
        return None, [], []


def resolve(filepath, spec):
    """Resolve an address ``spec`` to (offset, length, note). ``spec`` is a raw
    offset (0x.. / decimal), a chunk id, or ``chunk.field``. length is the chunk
    payload size or field length when known, else None. Raises KeyError if a
    named target is not found."""
    # a raw numeric offset
    try:
        return parse_int(spec), None, "offset"
    except ValueError:
        pass
    label, chunks, _warns = _walk(filepath)
    cid, _, fname = spec.partition(".")
    cid = cid.strip()
    match = None
    for c in chunks:
        if str(c.get("id", "")).strip() == cid:
            match = c
            break
    if match is None:
        raise KeyError(f"no chunk {cid!r} (try: acidcat inspect {os.path.basename(filepath)})")
    if not fname:
        return match["offset"], match.get("size"), f"chunk {cid}"
    pb = match.get("payload_base", (match.get("offset") or 0) + 8)
    for f in match.get("fields", []):
        if f.get("name") == fname and f.get("off") is not None:
            return pb + f["off"], f.get("len") or 0, f"{cid}.{fname}"
    raise KeyError(f"no field {cid}.{fname} (try: acidcat inspect {os.path.basename(filepath)})")


# ── the primitives (pure, operate on bytes) ────────────────────────

def read_typed(data, offset, fmt, count, byteorder):
    """Read ``count`` values of ``fmt`` at ``offset``. Returns a list of values.
    Supports the u24 escape (no struct code)."""
    e = ">" if byteorder == "big" else "<"
    if fmt == "u24":
        out = []
        for i in range(count):
            b = data[offset + i * 3:offset + i * 3 + 3]
            if len(b) < 3:
                break
            out.append(int.from_bytes(b, byteorder))
        return out
    code, size = FMT_STRUCT[fmt]
    out = []
    for i in range(count):
        o = offset + i * size
        if o + size > len(data):
            break
        out.append(struct.unpack_from(e + code, data, o)[0])
    return out


def find_bytes(data, pattern, limit=512):
    """Every offset of a byte pattern."""
    offs = []
    i = data.find(pattern)
    while i != -1 and len(offs) < limit:
        offs.append(i)
        i = data.find(pattern, i + 1)
    return offs


def scan_value(data, value, fmt, limit=512):
    """Cheat-Engine value scan: every offset where ``value`` appears as ``fmt``,
    in both byte orders. Returns a list of (offset, 'le'|'be')."""
    hits = []
    for order, e in (("le", "<"), ("be", ">")):
        if fmt == "u24":
            try:
                needle = int(value).to_bytes(3, "little" if order == "le" else "big")
            except (OverflowError, ValueError):
                continue
        else:
            code, _ = FMT_STRUCT[fmt]
            try:
                needle = struct.pack(e + code, value)
            except struct.error:
                continue
        for off in find_bytes(data, needle, limit):
            hits.append((off, order))
    hits.sort()
    return hits[:limit]


def strings(data, minlen=4, limit=1000):
    """Printable ASCII runs, as (offset, text)."""
    out = []
    cur = bytearray()
    start = 0
    for i, b in enumerate(data):
        if 32 <= b < 127:
            if not cur:
                start = i
            cur.append(b)
        else:
            if len(cur) >= minlen:
                out.append((start, cur.decode("latin-1")))
                if len(out) >= limit:
                    return out
            cur = bytearray()
    if len(cur) >= minlen and len(out) < limit:
        out.append((start, cur.decode("latin-1")))
    return out


def hexdump(data, offset, length):
    """An annotated hexdump of ``data[offset:offset+length]``."""
    lines = []
    end = min(offset + length, len(data))
    for r in range(offset, end, 16):
        row = data[r:r + 16]
        hexs = " ".join(f"{b:02x}" for b in row)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        lines.append(f"{r:08x}  {hexs:<47}  {asc}")
    return "\n".join(lines)


def diff(a, b, limit=256):
    """Changed byte ranges between two byte strings: (ranges, len_a, len_b),
    ranges = [(start, end)] over the common prefix."""
    n = min(len(a), len(b))
    ranges = []
    i = 0
    while i < n and len(ranges) < limit:
        if a[i] != b[i]:
            s = i
            while i < n and a[i] != b[i]:
                i += 1
            ranges.append((s, i))
        else:
            i += 1
    return ranges, len(a), len(b)
