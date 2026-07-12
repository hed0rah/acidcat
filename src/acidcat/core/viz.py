"""Zero-dependency terminal visualization primitives for byte dissection.

These give a picture of a file's *shape*: where the structure is, where the
high-entropy (encrypted / compressed) regions are, and how a byte-class map
(binvis-style) lays the file out. Pure functions returning plain strings / grids;
the caller adds color. No third-party imports.

  braille_line(values, w, h)   -> h strings (a smooth line/area plot)
  byte_histogram(data, w, h)   -> braille bars of the 256-value distribution
  windowed_entropy(data, n)    -> n Shannon-entropy samples (bits/byte, 0..8)
  hilbert_grid(data, order)    -> (side x side) grid of mean bytes along a
                                  Hilbert curve, so adjacent offsets stay adjacent
  byte_class(b)                -> a (glyph, class_name) for a byte's binvis class
                                  (class_name is a tui_theme.BYTE_CLASS key; color
                                  lives in the theme, not here)
"""

import math

# dot bit within a 2x4 braille cell, indexed [row 0..3][col 0..1]
_DOTS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


def _dot_rows(dots, width, height):
    rows = []
    for cy in range(height):
        line = []
        for cx in range(width):
            m = 0
            for dy in range(4):
                for dx in range(2):
                    if (cx * 2 + dx, cy * 4 + dy) in dots:
                        m |= _DOTS[dy][dx]
            line.append(chr(0x2800 + m))
        rows.append("".join(line))
    return rows


def braille_line(values, width=72, height=8, vmin=None, vmax=None, fill=False):
    """Braille line (or filled area) plot, `height` strings top-first."""
    if not values or width < 1 or height < 1:
        return [" " * max(1, width) for _ in range(max(1, height))]
    dot_w, dot_h = width * 2, height * 4
    vmin = min(values) if vmin is None else vmin
    vmax = max(values) if vmax is None else vmax
    span = (vmax - vmin) or 1.0
    n = len(values)
    dots = set()
    prev = None
    for x in range(dot_w):
        idx = int(round(x * (n - 1) / (dot_w - 1))) if dot_w > 1 and n > 1 else 0
        v = values[min(n - 1, idx)]
        from_bottom = int(round((v - vmin) / span * (dot_h - 1)))
        from_bottom = max(0, min(dot_h - 1, from_bottom))
        top = (dot_h - 1) - from_bottom
        dots.add((x, top))
        if prev is not None:
            lo, hi = sorted((prev, top))
            for yy in range(lo, hi + 1):
                dots.add((x, yy))
        if fill:
            for yy in range(top, dot_h):
                dots.add((x, yy))
        prev = top
    return _dot_rows(dots, width, height)


def byte_counts(data):
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    return counts


def byte_histogram(data, width=128, height=6):
    """Braille bar chart of the byte distribution. Flat top = encrypted/
    compressed; peaks = structure."""
    counts = byte_counts(data)
    return braille_line(counts, width=width, height=height, vmin=0, fill=True)


def _shannon(counts, total):
    h = 0.0
    for c in counts:
        if c:
            p = c / total
            h -= p * math.log2(p)
    return h


def windowed_entropy(data, windows=72):
    """Shannon entropy (bits/byte, 0..8) over ``windows`` equal slices. A flat
    line near 8 is an encrypted or compressed span; structure varies."""
    n = len(data)
    if n == 0:
        return [0.0] * windows
    out = []
    for i in range(windows):
        lo = i * n // windows
        hi = max(lo + 1, (i + 1) * n // windows)
        seg = data[lo:hi]
        counts = byte_counts(seg)
        out.append(_shannon(counts, len(seg)))
    return out


def _d2xy(side, d):
    """Hilbert curve: distance d -> (x, y) on a side x side grid (side = 2^k)."""
    x = y = 0
    t = d
    s = 1
    while s < side:
        rx = 1 & (t // 2)
        ry = 1 & (t ^ rx)
        if ry == 0:
            if rx == 1:
                x = s - 1 - x
                y = s - 1 - y
            x, y = y, x
        x += s * rx
        y += s * ry
        t //= 4
        s *= 2
    return x, y


def hilbert_grid(data, order=5):
    """Lay bytes along a Hilbert space-filling curve into a 2^order square grid;
    each cell is the mean byte of its slice (or None). Adjacent file offsets stay
    spatially adjacent, so headers, PCM, and appended/cavity regions show up as
    distinct blocks."""
    side = 1 << order
    cells = side * side
    grid = [[None] * side for _ in range(side)]
    n = len(data)
    if n == 0:
        return grid, side
    for i in range(cells):
        lo = i * n // cells
        hi = max(lo + 1, (i + 1) * n // cells)
        chunk = data[lo:hi]
        if not chunk or lo >= n:
            continue
        x, y = _d2xy(side, i)
        grid[y][x] = sum(chunk) // len(chunk)
    return grid, side


# byte class -> (glyph for no-color terminals, hex color for color terminals)
def byte_class(b):
    """(glyph, class) for a byte's binvis class. class is a tui_theme.BYTE_CLASS
    key; color lives in the theme so core stays presentation-free."""
    if b is None:
        return " ", "empty"
    if b == 0x00:
        return ".", "null"
    if b == 0xFF:
        return "#", "ff"
    if 0x20 <= b <= 0x7E:
        return "o", "ascii"
    if b < 0x20:
        return "-", "ctrl"
    return "+", "high"
