"""acidcat TUI -- byte/field rendering helpers and metadata edit profiles.

Pure helpers shared by the TUI screens and the app: hex rendering (hex_text,
context_hex, _hex_rows), a fuzzy matcher (_fuzzy), bounded file reads (_read),
and the view/scan/undo cap constants. The metadata edit profiles the edit form
is built from are re-exported from core (acidcat.core.write.profiles). No
Textual app state.
"""

import re

from rich.text import Text

# the metadata edit profiles live in core (acidcat.core.write.profiles); these
# names stay importable from here and from acidcat.tui_app
from acidcat.core.write.profiles import edit_profile, text_field_for  # noqa: F401
from acidcat.tui_theme import DIM, FG, GUTTER, PALETTE, SOFT


_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # braille scan spinner
_BAR_W = 18            # width of the scan progress bar

_HEX_CAP = 1024        # most bytes to render in the hex pane for one node
_ROW_CAP = 400         # most per-element rows (events/frames) to list per chunk
# Most chunks to build tree widgets for. _ROW_CAP bounds rows WITHIN a chunk and
# nothing bounded the chunk count itself, so a malformed file froze the TUI on
# open: a WAV with a tail of nulls walks as one zero-size chunk per 8 bytes, and
# 262,146 of them took 46 seconds to mount with nothing painted -- so not even
# `q` was available. 8.5 million of them never finished. `inspect` renders the
# same file in 3.4 seconds because it prints text instead of building widgets.
_CHUNK_CAP = 2000
_HEXEDIT_CAP = 512     # refuse editing a byte region bigger than this (pick a field)
_VIZ_READ = 8 * 1024 * 1024   # bytes the histogram reads; the other views stream
_UNDO_CAP = 50         # most undo deltas to keep
_UNDO_BYTES_CAP = 64 * 1024 * 1024   # total delta bytes kept (latest always kept)
_DIFF_CAP = 200        # most changed regions to LIST; the count reported is the true one
# Byte-search hits kept for n/N cycling. This was a bare literal in the search
# loop and the only cap in the app that was neither named nor disclosed: the
# notify printed len(hits), so 100,000 matches reported as "4096 match(es)" and
# n/N wrapped at 4096 with the rest of the file unreachable.
_SEARCH_CAP = 4096
_LARGE_FILE = 64 * 1024 * 1024       # above this, browse in place (no working copy)
_SCAN_SEG = 16 * 1024 * 1024         # scan a blob in segments: live progress + cancel


def _read(path, off, length):
    try:
        with open(path, "rb") as f:
            f.seek(off)
            return f.read(length)
    except OSError:
        return b""


def _fuzzy(query, text):
    """fzf-style subsequence match: every char of `query` appears in `text`, in
    order, case-insensitively. The default TUI search over field names/values."""
    q, t = query.lower(), text.lower()
    i = 0
    for ch in t:
        if i < len(q) and ch == q[i]:
            i += 1
    return i == len(q)


# A row is gutter + hex cells + the mid gap + the ascii column. Powers of two
# only: at 16 or 8 per row a column still maps to the low nibble of the offset,
# which is most of why a hex grid is readable at all.
_ROW_WIDTHS = (16, 8, 4)


def row_width_for(columns):
    """Bytes per row that fit in ``columns``, largest first. 16 needs 76.

    The grid folds rather than scrolling horizontally, so a row wider than the
    pane does not merely look cramped -- it wraps, and column position stops
    meaning anything.
    """
    for n in _ROW_WIDTHS:
        if columns >= 10 + 3 * n + (1 if n > 8 else 0) + 1 + n:
            return n
    return _ROW_WIDTHS[-1]


def hex_text(path, off, length, accent, spans=None, width=16, start=0):
    """A colored hex dump (offset gutter + hex columns + ascii) of up to
    _HEX_CAP bytes, beginning `start` bytes into the region at `off`.

    Bytes render in `accent`; when `spans` (a list of (abs_offset, len) field
    ranges) is given, each field's bytes take a distinct palette color so a
    chunk's field structure shows in the hex. Non-printable ascii dims out.

    `start` is what makes the rest of a big region reachable. The cap always
    announced itself -- ".. N more bytes" -- but there was no way to go and look
    at them, which on a multi-megabyte region meant the hex view could only ever
    show the first kilobyte of it.
    """
    t = Text()
    if off is None or length in (None, 0):
        t.append("  (no byte range for this node)", style=DIM)
        return t
    start = max(0, min(int(start or 0), max(0, length - 1)))
    shown = min(length - start, _HEX_CAP)
    raw = _read(path, off + start, shown)
    _hex_rows(t, off + start, raw, accent,
              _spans_cmap(off + start, spans, shown) if spans else None, width)
    if length > shown:
        # Say which window this is, not just that one exists: "1,024 of 3 MB"
        # leaves the reader working out where they are in it.
        t.append(f"  bytes {start:,}..{start + shown - 1:,} of {length:,}"
                 f"   [PgDn/PgUp to page, g to jump]\n", style=DIM)
    return t


def byte_strip(nodes, length, width, selection=None):
    """The whole layer as one row: each cell is `length / width` bytes, drawn
    as the node that holds them.

    `nodes` are the layer's top-level Document nodes (with `extent`, and
    `payload` where the node has a header). A node's header bytes are drawn as
    header, not as its payload, and the cell where a node starts always shows
    that node's header, so a small chunk between big ones is never swallowed.
    Bytes no walker described (`kind: unwalked`) are a gap. Cells that hold
    selected bytes are lit. Exactly `width` cells, no newline.
    """
    t = Text(no_wrap=True, overflow="crop")
    if width <= 0:
        return t
    if not length:
        t.append("·" * width, style=GUTTER)
        return t
    spans = []                      # (off, end, kind, color)
    ci = 0
    for n in nodes:
        e = n.get("extent")
        if not e:
            continue
        if n.get("kind") == "unwalked":
            spans.append((e["off"], e["off"] + e["len"], "gap", GUTTER))
            continue
        color = PALETTE[ci % len(PALETTE)]
        ci += 1
        p = n.get("payload") or e
        head_end = min(max(p["off"], e["off"]), e["off"] + e["len"])
        if head_end > e["off"]:
            spans.append((e["off"], head_end, "head", color))
        spans.append((head_end, e["off"] + e["len"], "body", color))
    spans.sort()
    lo, hi = ((selection[0], selection[0] + max(selection[1], 1))
              if selection and selection[0] is not None else (None, None))
    for c in range(width):
        a = c * length // width
        b = max(a + 1, (c + 1) * length // width)
        here = [sp for sp in spans if sp[0] < b and sp[1] > a]
        # a header that starts in this cell wins it; else what covers the
        # cell's middle; else a gap
        starts = [sp for sp in here if sp[2] == "head" and a <= sp[0] < b]
        mid = (a + b) // 2
        cover = [sp for sp in here if sp[0] <= mid < sp[1]] or here
        sp = starts[0] if starts else (cover[0] if cover else None)
        kind, color = (sp[2], sp[3]) if sp else ("gap", GUTTER)
        glyph = {"head": "▌", "body": "█", "gap": "·"}[kind]
        lit = lo is not None and lo < b and a < hi
        style = color + (f" on {FG}" if lit else "")
        if lit and kind == "body":
            glyph = "▓"
        t.append(glyph, style=style)
    return t


def field_inspector(d, width=None):
    """The selected node, said in full: at most six lines, each cut to
    `width` with an ellipsis rather than wrapped, so the pane never changes
    height. The first bytes are budgeted to the width, whole bytes only, so
    a narrow pane shows fewer of them rather than half of one.

    `d` is plain facts the app gathered: name, accent, kind (field, chunk or
    root), off, len, raw (the first bytes), type and type_source, value,
    display, meaning (an enum's label), note, ptr ((target, in_file) or None),
    summary, payload ((off, len) or None), caps (strings), findings (count),
    hint (how it can be edited)."""
    lines = []

    def line():
        t = Text(no_wrap=True, overflow="ellipsis")
        lines.append(t)
        return t

    t = line()
    t.append(str(d.get("name", "")), style=f"bold {d.get('accent') or FG}")
    if d.get("type"):
        t.append(f"   {d['type']}", style=SOFT)
        if d.get("type_source") and d["type_source"] != "none":
            t.append(f" ({d['type_source']})", style=DIM)
    elif d.get("kind") in ("chunk", "root"):
        t.append(f"   {d['kind']}", style=DIM)

    t = line()
    if d.get("off") is None:
        t.append(d.get("where") or "no byte range: derived from other fields",
                 style=DIM)
    else:
        t.append("@ ", style=DIM)
        t.append(f"0x{d['off']:08x}", style=FG)
        t.append(f"   {d.get('len', 0):,} bytes", style=SOFT)
        tail = ""
        if d.get("payload") and d["payload"] != (d.get("off"), d.get("len")):
            po, pl = d["payload"]
            tail = f"   payload 0x{po:08x}+{pl:,}"
        raw = d.get("raw") or b""
        if raw:
            n = 16
            if width:
                # "   xx xx .. xx" is 3n+2 cells, " …" 2 more when cut short
                n = min(n, max(0, (width - t.cell_len - len(tail) - 4) // 3))
            shown = raw[:n]
            if shown:
                t.append("   " + shown.hex(" "), style=FG)
                if d.get("len", 0) > len(shown):
                    t.append(" …", style=DIM)
        if tail:
            t.append(tail, style=DIM)

    if d.get("kind") == "field":
        t = line()
        t.append("value   ", style=DIM)
        t.append(str(d.get("value")), style=f"bold {FG}")
        disp = d.get("display")
        if disp not in (None, "") and str(disp) != str(d.get("value")):
            t.append(f"   {disp}", style=SOFT)
        if d.get("meaning"):
            t = line()
            t.append("meaning ", style=DIM)
            t.append(str(d["meaning"]), style=SOFT)
    elif d.get("summary"):
        t = line()
        t.append(str(d["summary"]), style=SOFT)
    if d.get("note"):
        t = line()
        t.append("note    ", style=DIM)
        t.append(str(d["note"]), style=SOFT)
    if d.get("ptr") is not None:
        target, ok = d["ptr"]
        t = line()
        t.append("points  ", style=DIM)
        t.append(f"0x{target:08x}", style=FG if ok else PALETTE[-1])
        t.append("   enter follows" if ok else "   DANGLING: outside the file",
                 style=SOFT if ok else f"bold {PALETTE[-1]}")
    if d.get("caps"):
        t = line()
        t.append("offers  ", style=DIM)
        t.append("   ".join(d["caps"]), style=PALETTE[0])
    if d.get("findings"):
        t = line()
        n = d["findings"]
        t.append(f"{n} finding{'s' if n != 1 else ''} here", style=PALETTE[-2])
    if d.get("hint"):
        t = line()
        t.append(str(d["hint"]), style=DIM)
    out = Text(no_wrap=True, overflow="ellipsis")
    for i, ln in enumerate(lines[:6]):
        if i:
            out.append("\n")
        if width and ln.cell_len > width:
            ln.truncate(width, overflow="ellipsis")
        out.append_text(ln)
    return out


def data_inspector(off, raw, width=40):
    """The bytes at the cursor read every common way: u8 to u64 and i8 to i64
    and f32/f64, little-endian beside big-endian, with the ASCII and the bits
    of the first byte. `raw` is up to 8 bytes at `off`; a reading the bytes
    run out for is left blank rather than padded into a wrong number."""
    import struct
    t = Text(no_wrap=True, overflow="ellipsis")
    col = (width - 4) // 2

    def fit(v, room):
        # a number too long for its column says so: a silently shortened
        # number reads as a different one
        text = str(v)
        if len(text) > room and isinstance(v, int):
            text = f"0x{v & ((1 << 64) - 1):x}"
        if len(text) > room:
            text = text[:room - 1] + "…"
        return text.ljust(room)

    if off is None or not raw:
        t.append("  no bytes under the cursor", style=DIM)
        return t
    head = raw[:8]
    t.append(f"{off:08x}", style=FG)
    t.append("  ")
    t.append("".join(chr(b) if 32 <= b < 127 else "." for b in head), style=SOFT)
    t.append("  ")
    t.append(f"{head[0]:08b}", style=DIM)
    t.append("\n")
    t.append("    " + "little".ljust(col) + "big", style=DIM)
    rows = [("u8", "<B", ">B"), ("i8", "<b", ">b"), ("u16", "<H", ">H"),
            ("i16", "<h", ">h"), ("u32", "<I", ">I"), ("i32", "<i", ">i"),
            ("u64", "<Q", ">Q"), ("i64", "<q", ">q"), ("f32", "<f", ">f"),
            ("f64", "<d", ">d")]
    for name, le, be in rows:
        n = struct.calcsize(le)
        t.append("\n")
        t.append(name.ljust(4), style=GUTTER)
        if len(raw) < n:
            continue
        for fmt, room in ((le, col - 1), (be, col)):
            v = struct.unpack(fmt, bytes(raw[:n]))[0]
            if isinstance(v, float):
                v = f"{v:.6g}"
            t.append(fit(v, room) + (" " if fmt is le else ""), style=FG)
    return t


def _spans_cmap(base_off, spans, limit):
    """Map each shown byte position (relative to base_off) to a per-field color,
    cycling the palette across the fields."""
    cmap = {}
    for i, (ao, ln) in enumerate(spans):
        color = PALETTE[i % len(PALETTE)]
        start = ao - base_off
        for p in range(max(0, start), min(limit, start + ln)):
            cmap[p] = color
    return cmap


def _hex_rows(t, off, raw, byte_style, cmap=None, width=16):
    """Append hex-dump rows (gutter + hex + ascii) for `raw` to Text `t`.

    `cmap` maps a position (relative to `off`) to a style, and it applies to
    BOTH columns. The ascii column used to be styled independently, which meant
    a caller could not put one style on a byte -- and that gap is the only
    reason the hex editor had to hand-inline its own copy of this loop to get a
    cursor onto both halves of a row.
    """
    for row in range(0, len(raw), width):
        chunk = raw[row:row + width]
        t.append(f"{off + row:08x}  ", style=GUTTER)
        for i in range(width):
            if i < len(chunk):
                style = cmap.get(row + i, byte_style) if cmap else byte_style
                t.append(f"{chunk[i]:02x} ", style=style)
            else:
                t.append("   ")
            if width > 8 and i == (width // 2) - 1:
                t.append(" ")
        t.append(" ")
        for i, b in enumerate(chunk):
            printable = 32 <= b < 127
            # the cmap wins; otherwise printable/not is the only signal here
            style = (cmap or {}).get(row + i) or (FG if printable else DIM)
            t.append(chr(b) if printable else ".", style=style)
        t.append("\n")


def context_hex(start, raw, width, selection, spans, accent):
    """The bytes pane: `raw` (the window of the layer that starts at `start`)
    as hex rows, with the selected bytes lit inside their surroundings.

    `selection` is (offset, length) in the same layer, or None. The selected
    bytes are drawn on a lit background; within them each field of `spans`
    takes its own palette color, the rest the node's `accent`. Everything
    outside the selection is context: readable, and quieter than what you
    picked. The last row carries no newline, so the pane holds exactly as many
    rows as it was given and never grows a scrollbar that would narrow it.
    """
    t = Text()
    lo, hi = ((selection[0], selection[0] + max(selection[1], 1))
              if selection and selection[0] is not None else (None, None))
    tint = {}
    for i, (ao, ln) in enumerate(spans or ()):
        color = PALETTE[i % len(PALETTE)]
        for p in range(max(ao, start), min(ao + ln, start + len(raw))):
            tint[p] = color
    lit = f"on {GUTTER}"

    def style(pos, b):
        if lo is not None and lo <= pos < hi:
            return f"bold {tint.get(pos, accent)} {lit}"
        return DIM if b == 0 else SOFT

    rows = [raw[r:r + width] for r in range(0, len(raw), width)]
    for n, chunk in enumerate(rows):
        off = start + n * width
        mark = lo is not None and off < hi and lo < off + width
        t.append(f"{off:08x}", style=accent if mark else GUTTER)
        t.append("  ")
        for i in range(width):
            if i < len(chunk):
                st = style(off + i, chunk[i])
                t.append(f"{chunk[i]:02x}", style=st)
                # the gap after a lit byte stays lit when the next is too, so
                # a selection reads as one bar rather than a row of islands
                nxt = off + i + 1
                join = (lo is not None and lo <= off + i < hi and nxt < hi
                        and i + 1 < len(chunk))
                t.append(" ", style=lit if join else "")
            else:
                t.append("   ")
            if width > 8 and i == (width // 2) - 1:
                t.append(" ")
        t.append(" ")
        for i, b in enumerate(chunk):
            printable = 32 <= b < 127
            pos = off + i
            if lo is not None and lo <= pos < hi:
                st = style(pos, b)
            else:
                st = SOFT if printable else DIM
            t.append(chr(b) if printable else ".", style=st)
        if n < len(rows) - 1:
            t.append("\n")
    if not rows:
        t.append("  (this layer is empty)", style=DIM)
    return t


_SIZE_ECHO = re.compile(r",?\s*\b[\d,]+ bytes\b")


def trim_size_echo(summary, size):
    """Drop a byte count from a chunk summary when the row already shows it.

    The tree row prints the size itself, then the walker's summary prints it
    again -- "data 0x46 176,400b audio payload, 176,400 bytes, 1.000 s". Two
    statements of one fact, and on the widest chunks it is what pushed the row
    past the pane. Only an exact match is removed: a summary quoting a
    *different* number is saying something (a declared size, a payload inside a
    larger chunk) and must survive untouched.
    """
    text = str(summary or "")
    if not size:
        return text
    def drop(m):
        try:
            return "" if int(m.group(0).replace(",", "").replace("bytes", "").strip()) == size else m.group(0)
        except ValueError:
            return m.group(0)
    out = _SIZE_ECHO.sub(drop, text)
    return re.sub(r"\s{2,}", " ", out).strip().strip(",").strip()
