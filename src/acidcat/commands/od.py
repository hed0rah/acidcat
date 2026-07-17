"""acidcat od -- objdump-x-style annotated, colored hex dump of a file's structure.

Lays out the parsed bytes as a hex dump -- offset, hex, meaning -- with each
chunk/block header and each decoded field on its own line, the field's byte-run
colored (cycling palette) and labeled with its name/value/note, so the structure
is legible directly in the raw byte stream. Bulk audio payload is elided.

Complements `inspect --hex` (a value-first table); this is a bytes-first layout.
"""

import sys

from acidcat.core.mapped import map_file
from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported

# cycling foreground colors so adjacent fields are visually distinct
_FIELD_COLORS = (36, 32, 33, 35, 34, 31, 96, 92, 93, 95)


def register(subparsers):
    p = subparsers.add_parser(
        "od", help="objdump-x-style annotated, colored hex dump of a file's structure")
    p.add_argument("target")
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    p.add_argument("--width", type=int, default=16, metavar="N",
                   help="max hex bytes shown per field before eliding (default 16)")
    p.set_defaults(func=run)


def _use_color(mode):
    return mode == "always" or (mode == "auto" and sys.stdout.isatty())


def _c(code, text, on):
    return f"\033[{code}m{text}\033[0m" if on else text


def _hexcells(b):
    return " ".join(f"{x:02x}" for x in b)


def _ascii(b):
    return "".join(chr(x) if 32 <= x < 127 else "." for x in b)


def run(args):
    path = args.target
    try:
        label, chunks, warns = walk_file(path)
    except Unsupported as e:
        print(f"acidcat od: {path}: {e}", file=sys.stderr)
        return 2
    on = _use_color(args.color)
    # mmap, not f.read(): od only slices small header/field/preview runs, and
    # a mapped file serves those without loading multi-GB payloads into RAM
    data, close = map_file(path)
    try:
        def dim(t):
            return _c("2", t, on)

        header = _c("1", f"{label}  {len(data):,} bytes  {len(chunks)} chunks", on)
        if warns:
            header += dim(f"   {len(warns)} warning(s)")
        print(header)

        for c in chunks:
            base = c.get("payload_base", c["offset"] + 8)
            summary = c.get("summary", "")
            title = _c("1;37", f"{str(c['id'])!r} @ 0x{c['offset']:08x}  {c['size']:,} bytes", on)
            print("\n" + title + (dim("  " + summary) if summary else ""))

            # the chunk/block header bytes (id + size), dimmed
            hdr = data[c["offset"]:base]
            c_off = f"0x{c['offset']:08x}"
            print(f"  {dim(c_off)}  {dim(_hexcells(hdr))}  {dim(_ascii(hdr))}")

            fields = c.get("fields", [])
            for i, fl in enumerate(fields):
                off = fl.get("off")
                name = _c("1", fl["name"], on)
                value = fl.get("value")
                note = dim("  " + fl["note"]) if fl.get("note") else ""
                if off is None:                   # derived / synthetic field
                    print(f"  {'':10}  {dim('(derived)')}  {name} = {value}{note}")
                    continue
                abs_off = base + off
                avail = max(0, min(abs_off + fl.get("len", 0), len(data)) - abs_off)
                # copy only the rendered prefix out of the map; a multi-MB
                # field must not be materialized to show its first bytes
                fb = data[abs_off:abs_off + min(avail, args.width)]
                cells = _c(_FIELD_COLORS[i % len(_FIELD_COLORS)], _hexcells(fb), on)
                more = dim(f" +{avail - args.width}") if avail > args.width else ""
                print(f"  0x{abs_off:08x}  {cells}{more}  {name} = {value}{note}")

            # opaque chunk (no decoded fields): show the first row, elide the rest
            if not fields and c["size"] > 0:
                preview = data[base:base + args.width]
                elided = dim(f"({c['size']:,} bytes payload)")
                print(f"  0x{base:08x}  {dim(_hexcells(preview))}  {dim(_ascii(preview))}  {elided}")
        return 0
    finally:
        close()
