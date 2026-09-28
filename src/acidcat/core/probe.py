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
    offset (0x.. / decimal), or an ADDR (node-v1.md section 13): a node
    (``RIFF/fmt_``, ``fmt``: its payload), a field (``RIFF/fmt_#sample_rate``)
    or a byte range (``@0x2c+4``, ``RIFF/fmt_[4:4]``). The 1.8 spelling
    ``chunk.field`` still works. The note is the id the address resolved to.
    length is None when the spec is a bare offset. Raises KeyError when the
    spec names nothing in the file, or names bytes in a decoded layer (probe
    reads the file's own bytes; ``od`` and ``carve`` read a layer)."""
    # a raw numeric offset
    try:
        return parse_int(spec), None, "offset"
    except ValueError:
        pass
    from acidcat.core.infra import addr as addrmod
    try:
        t = _addr_target(filepath, spec)
    except addrmod.AddrError as e:
        if "." in spec and "#" not in spec and "/" not in spec:
            return _resolve_chunk_field(filepath, spec)     # the 1.8 spelling
        raise KeyError(f"{e} (try: acidcat inspect {os.path.basename(filepath)})") from None
    if t.layer:
        raise KeyError(f"{spec} is in layer {t.layer}, a decoded image; probe "
                       f"reads the file's own bytes (od and carve read a layer)")
    if t.off is None:
        raise KeyError(f"{spec} has no byte range (it is unpositioned)")
    note = spec
    if t.node is not None:
        note = t.node["id"] + ("#" + t.field["key"] if t.field is not None else "")
    return t.off, t.len, note


def _addr_target(filepath, spec):
    """addr.resolve on the file's Document; AddrError when it names nothing,
    and for a file no walker reads."""
    from acidcat.core.document import open_document
    from acidcat.core.infra import addr as addrmod
    from acidcat.core.walk.base import Unsupported
    try:
        doc = open_document(filepath, forensics=False)
    except Unsupported:
        raise addrmod.AddrError("no walker reads this file, so only an offset "
                                "or a byte range names anything in it") from None
    return addrmod.resolve(doc.to_json(), spec)


def _resolve_chunk_field(filepath, spec):
    """The 1.8 ``chunk.field`` / ``chunk`` spelling, by the walker's ids."""
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
    """Every offset of a byte pattern. ``limit=None`` returns all of them.

    The docstring said "every offset" while stopping at 512, so a caller
    printing len() of the result reported the cap as the hit count. The default
    is unchanged; a caller that needs the true total asks for it.
    """
    offs = []
    i = data.find(pattern)
    while i != -1 and (limit is None or len(offs) < limit):
        offs.append(i)
        i = data.find(pattern, i + 1)
    return offs


def find_bytes_counted(data, pattern, limit=512):
    """(first `limit` offsets, total count) in one pass, bounded memory.

    The counting variant exists because the obvious way to report a true total
    -- ask for every offset and take len() -- materializes one int per match
    over an mmap that was mmapped precisely so a huge input would not cost its
    size in RAM. `probe strings` over a multi-GB image would build a list of
    every printable run in it. Count past the cap; store up to it.
    """
    offs = []
    total = 0
    i = data.find(pattern)
    while i != -1:
        total += 1
        if limit is None or len(offs) < limit:
            offs.append(i)
        i = data.find(pattern, i + 1)
    return offs, total


def strings_counted(data, minlen=4, limit=1000):
    """(first `limit` runs, total count), single pass, bounded memory."""
    out = []
    total = 0
    cur = bytearray()
    start = 0
    for i, b in enumerate(data):
        if 32 <= b < 127:
            if not cur:
                start = i
            cur.append(b)
        else:
            if len(cur) >= minlen:
                total += 1
                if limit is None or len(out) < limit:
                    out.append((start, cur.decode("latin-1")))
            cur = bytearray()
    if len(cur) >= minlen:
        total += 1
        if limit is None or len(out) < limit:
            out.append((start, cur.decode("latin-1")))
    return out, total


def diff_counted(a, b, limit=256):
    """(first `limit` changed ranges, total count, len_a, len_b), one pass."""
    n = min(len(a), len(b))
    ranges = []
    total = 0
    i = 0
    while i < n:
        if a[i] != b[i]:
            st = i
            while i < n and a[i] != b[i]:
                i += 1
            total += 1
            if limit is None or len(ranges) < limit:
                ranges.append((st, i))
        else:
            i += 1
    return ranges, total, len(a), len(b)


def scan_value(data, value, fmt, limit=512):
    """Cheat-Engine value scan: every offset where ``value`` appears as ``fmt``,
    in both byte orders. Returns a list of (offset, 'le'|'be').

    ``limit=None`` returns every hit rather than the first 512.
    """
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
    return hits if limit is None else hits[:limit]


def scan_value_counted(data, value, fmt, limit=512):
    """(first `limit` hits, total count), bounded memory. See
    find_bytes_counted for why the count is not len(everything)."""
    hits = []
    total = 0
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
        offs, n = find_bytes_counted(data, needle, limit)
        total += n
        hits.extend((off, order) for off in offs)
    hits.sort()
    return hits[:limit] if limit is not None else hits, total


# A run of ascending in-file u32s is a table; one in-file u32 is a coincidence.
# For a 44 KB file every value under 44,170 "could be an offset", which is most
# small integers -- so a lone candidate is noise and only the run is a finding.
_TABLE_MIN_ENTRIES = 4
_MARK_LIMIT = 256


def annotate(window, *, base_off=0, file_size=None, marks=True):
    """``{position: [tag, ...]}`` for the bytes of ``window``.

    Positions are relative to the window, matching what a hex renderer's colour
    map already wants. Tags are strings and never colours -- ``viz.byte_class``
    sets that precedent so core stays presentation-free and the terminal, the
    TUI and the HTML explorer each choose their own styling.

    Tags emitted:

    ``class:null|ff|ascii|ctrl|high``
        What kind of byte this is -- the split ``viz.byte_class`` already uses,
        so a dump and the Hilbert map agree. This is the thing the ascii column
        cannot say: 00, FF and 80 are three identical dots there. Note that
        printable means 0x20..0x7E, so DEL is ``high``, not ``ctrl``.

    ``mark:size``
        A value equal to the file size, or to the file size minus 8. The second
        is the RIFF/IFF convention (a size counting everything after the id and
        the size field), and finding it is usually the moment an unknown
        container stops being unknown.

    ``mark:table``
        The start of a run of at least four ascending u32s that all land inside
        the file. This is deliberately narrower than "this value could be an
        offset", which for any real file is true of most small integers and
        would light up the whole dump.

    ``marks=False`` gives classes only, for a caller that just wants the
    byte-kind tint.
    """
    from acidcat.core.forensics import viz

    tags = {}

    def add(pos, tag):
        if 0 <= pos < len(window):
            tags.setdefault(pos, []).append(tag)

    for i, b in enumerate(window):
        add(i, "class:" + viz.byte_class(b)[1])

    if not marks or not file_size:
        return tags

    # size fields: reuse the value scan, which already tries both byte orders
    for want in (file_size, file_size - 8):
        if want <= 0:
            continue
        for off, _order in scan_value(window, want, "u32", limit=_MARK_LIMIT):
            for k in range(4):
                add(off + k, "mark:size")

    # offset tables: a run, not a value
    n = len(window)
    aligned = (4 - (base_off % 4)) % 4          # first window-relative 4-aligned pos
    run_start = None
    prev = None
    count = 0
    for pos in range(aligned, n - 3, 4):
        v = struct.unpack_from("<I", window, pos)[0]
        ascending = prev is not None and prev < v
        if 0 < v < file_size and (prev is None or ascending):
            if run_start is None:
                run_start, count = pos, 1
            else:
                count += 1
            prev = v
            continue
        if run_start is not None and count >= _TABLE_MIN_ENTRIES:
            for k in range(count * 4):
                add(run_start + k, "mark:table")
        run_start, prev, count = (pos, v, 1) if 0 < v < file_size else (None, None, 0)
    if run_start is not None and count >= _TABLE_MIN_ENTRIES:
        for k in range(count * 4):
            add(run_start + k, "mark:table")

    return tags


def strings(data, minlen=4, limit=1000):
    """Printable ASCII runs, as (offset, text). ``limit=None`` returns all."""
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
                if limit is not None and len(out) >= limit:
                    return out
            cur = bytearray()
    if len(cur) >= minlen and (limit is None or len(out) < limit):
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
    ranges = [(start, end)] over the common prefix.

    ``limit=None`` walks the whole common prefix. With a limit the loop EXITS,
    so trailing differences are never examined and the count is the cap.
    """
    n = min(len(a), len(b))
    ranges = []
    i = 0
    while i < n and (limit is None or len(ranges) < limit):
        if a[i] != b[i]:
            s = i
            while i < n and a[i] != b[i]:
                i += 1
            ranges.append((s, i))
        else:
            i += 1
    return ranges, len(a), len(b)
