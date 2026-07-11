"""acidcat probe -- low-level byte dissection (the RE-tool surface).

Read a file as raw bytes the way a reverse engineer does, with the addresses
resolved through acidcat's format walker so you can name structure instead of
counting bytes.

    acidcat probe FILE read AT [--type u32] [--count N] [--be|--le]
    acidcat probe FILE scan VALUE [--type u32]
    acidcat probe FILE find HEX
    acidcat probe FILE strings [--min N]
    acidcat probe FILE hexdump AT [--len N]
    acidcat probe FILE diff OTHER

AT is a raw offset (0x2c / 44) OR a structural name: a chunk id (data), or a
chunk field (fmt.sample_rate). VALUE for scan is an integer (or a float for
f32/f64); it is searched in both byte orders. HEX for find is a hex string
(64617461) or, with a leading s:, literal text (s:data).
"""

import os
import sys

from acidcat.core import probe as pr
from acidcat.core import viz


def register(subparsers):
    p = subparsers.add_parser(
        "probe",
        help="Byte-level dissection: typed read, value scan, find, strings, hexdump, diff.")
    p.add_argument("file", help="File to dissect.")
    sub = p.add_subparsers(dest="verb", metavar="VERB")

    r = sub.add_parser("read", help="Read AT as typed values (pwndbg x).")
    r.add_argument("at", help="Offset (0x.. / decimal) or name (chunk / chunk.field).")
    r.add_argument("--type", "-t", default="u32", choices=sorted(pr.FMT_STRUCT),
                   help="Value type (default u32).")
    r.add_argument("--count", "-n", type=int, default=1, help="How many values.")
    r.add_argument("--be", action="store_true", help="Force big-endian.")
    r.add_argument("--le", action="store_true", help="Force little-endian.")

    s = sub.add_parser("scan", help="Find every offset holding VALUE (Cheat Engine).")
    s.add_argument("value", help="The value to find (int, or float for f32/f64).")
    s.add_argument("--type", "-t", default="u32", choices=sorted(pr.FMT_STRUCT),
                   help="How to encode VALUE (default u32).")

    f = sub.add_parser("find", help="Find every offset of a byte pattern.")
    f.add_argument("pattern", help="Hex bytes (64617461) or s:text for literal ASCII.")

    st = sub.add_parser("strings", help="Printable ASCII runs with offsets.")
    st.add_argument("--min", "-m", type=int, default=4, help="Minimum run length.")

    h = sub.add_parser("hexdump", help="Annotated hexdump at AT.")
    h.add_argument("at", help="Offset or structural name.")
    h.add_argument("--len", "-l", dest="length", type=int, default=256,
                   help="Bytes to dump (default 256, or the chunk size for a name).")

    d = sub.add_parser("diff", help="Changed byte ranges vs another file.")
    d.add_argument("other", help="The file to compare against.")

    en = sub.add_parser("entropy",
                        help="Shannon entropy curve + byte histogram (spot encrypted/compressed spans).")
    en.add_argument("--width", "-w", type=int, default=72, help="Plot width in cells.")

    mp = sub.add_parser("map",
                        help="Hilbert byte-class map (binvis): the file's shape at a glance.")
    mp.add_argument("--order", "-o", type=int, default=5,
                    help="Grid is 2^order per side (default 5 = 32x32).")
    mp.add_argument("--no-color", action="store_true", help="Glyphs instead of color blocks.")

    p.set_defaults(func=run)


def _rgb(hexc):
    return int(hexc[1:3], 16), int(hexc[3:5], 16), int(hexc[5:7], 16)


def _use_color(no_color):
    return (not no_color) and sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _byteorder(args, label):
    if getattr(args, "be", False):
        return "big"
    if getattr(args, "le", False):
        return "little"
    return pr.default_byteorder(label)


def _read_file(path):
    with open(path, "rb") as fh:
        return fh.read()


def run(args):
    verb = getattr(args, "verb", None)
    if not verb:
        print("acidcat probe: pick a verb (read/scan/find/strings/hexdump/diff)",
              file=sys.stderr)
        return 2
    path = args.file
    try:
        data = _read_file(path)
    except OSError as e:
        print(f"acidcat probe: {path}: {e}", file=sys.stderr)
        return 1
    label, _chunks, _warns = pr._walk(path)

    if verb == "read":
        try:
            off, _ln, note = pr.resolve(path, args.at)
        except (KeyError, ValueError) as e:
            print(f"acidcat probe: {e}", file=sys.stderr)
            return 1
        order = _byteorder(args, label)
        vals = pr.read_typed(data, off, args.type, args.count, order)
        if not vals:
            print(f"acidcat probe: nothing to read at 0x{off:x}", file=sys.stderr)
            return 1
        head = f"0x{off:08x}  {args.type} {order}-endian  ({note})"
        print(head)
        for i, v in enumerate(vals):
            print(f"  [{i}] {v}")
        return 0

    if verb == "scan":
        try:
            value = float(args.value) if args.type in ("f32", "f64") else pr.parse_int(args.value)
        except ValueError:
            print(f"acidcat probe: bad value {args.value!r}", file=sys.stderr)
            return 1
        hits = pr.scan_value(data, value, args.type)
        print(f"{len(hits)} hit(s) for {args.value} as {args.type}")
        for off, order in hits:
            print(f"  0x{off:08x}  ({order})")
        return 0 if hits else 1

    if verb == "find":
        pat = args.pattern
        if pat.startswith("s:"):
            needle = pat[2:].encode("latin-1")
        else:
            try:
                needle = bytes.fromhex(pat)
            except ValueError:
                print(f"acidcat probe: bad hex {pat!r} (use s: for text)", file=sys.stderr)
                return 1
        offs = pr.find_bytes(data, needle)
        print(f"{len(offs)} hit(s) for {pat}")
        for off in offs:
            print(f"  0x{off:08x}")
        return 0 if offs else 1

    if verb == "strings":
        found = pr.strings(data, args.min)
        for off, text in found:
            print(f"0x{off:08x}  {text}")
        return 0

    if verb == "hexdump":
        try:
            off, ln, _note = pr.resolve(path, args.at)
        except (KeyError, ValueError) as e:
            print(f"acidcat probe: {e}", file=sys.stderr)
            return 1
        length = args.length if args.length != 256 else (ln or 256)
        print(pr.hexdump(data, off, length))
        return 0

    if verb == "diff":
        try:
            other = _read_file(args.other)
        except OSError as e:
            print(f"acidcat probe: {args.other}: {e}", file=sys.stderr)
            return 1
        ranges, la, lb = pr.diff(data, other)
        if not ranges and la == lb:
            print("identical")
            return 0
        print(f"{os.path.basename(path)} ({la:,}) vs {os.path.basename(args.other)} "
              f"({lb:,}): {len(ranges)} changed range(s)")
        for s, e in ranges:
            print(f"  0x{s:08x}..0x{e:08x}  ({e - s} bytes)")
        if la != lb:
            print(f"  lengths differ by {abs(la - lb):,} bytes")
        return 0

    if verb == "entropy":
        ent = viz.windowed_entropy(data, max(8, args.width))
        print(f"entropy  {os.path.basename(path)}  {len(data):,} bytes  (0 = uniform .. 8 = random)")
        for line in viz.braille_line(ent, width=args.width, height=8, vmin=0, vmax=8):
            print("  " + line)
        hi = sum(1 for e in ent if e >= 7.2)
        summary = (f"  min {min(ent):.2f}  max {max(ent):.2f}  "
                   f"mean {sum(ent) / len(ent):.2f} bits/byte")
        if hi:
            summary += f"   [{hi} window(s) >= 7.2: encrypted or compressed]"
        print(summary)
        print("  byte distribution:")
        for line in viz.byte_histogram(data, width=128, height=5):
            print("  " + line)
        return 0

    if verb == "map":
        grid, side = viz.hilbert_grid(data, args.order)
        color = _use_color(args.no_color)
        print(f"byte map  {os.path.basename(path)}  {len(data):,} bytes  "
              f"({side}x{side} Hilbert; adjacent cells are adjacent bytes)")
        for row in grid:
            cells = []
            for b in row:
                glyph, hexc = viz.byte_class(b)
                if color:
                    r, g, bl = _rgb(hexc)
                    cells.append(f"\x1b[38;2;{r};{g};{bl}m█\x1b[0m")
                else:
                    cells.append(glyph)
            print("  " + "".join(cells))
        print("  legend:  . null   o ascii   - control   + high   # 0xFF")
        return 0

    return 2
