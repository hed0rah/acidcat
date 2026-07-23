"""
acidcat carve -- surgically extract a byte range or a typed field, to a file or
stdout. The "pull out exactly this" primitive.

Read-only on the source: carve never modifies the input, it only reads it out.
carve is a knife -- writing a value back is a different tool.

RAW ranges (bytes out; pipe-friendly, refuses to spew binary at a TTY):

    acidcat carve FILE --offset 0x100 --length 256
    acidcat carve FILE --offset 0x100 --end 0x200 -o out.bin
    acidcat carve FILE --trailing -o appended.bin          # past the container end
    acidcat carve FILE --chunk data -o audio.raw           # a RIFF/AIFF chunk payload

TYPED fields (decoded value out) -- surgical RE:

    acidcat carve FILE --at 0x1c --type u32be              # -> 44100
    acidcat carve FILE --at chunk:Indx+8 --type u32be --count 288   # a seek table
    acidcat carve FILE --at find:BFDi+8 --type u32be       # relative to a pattern
    acidcat carve FILE --at 0x1c --type u32 --endian both  # guess the endianness
    acidcat carve FILE --struct '@0x08 bits:u32be _:u32be samples:u32be rate:u32be ch:u32be'
    acidcat carve FILE --field sample_rate                 # a walker field, by name

--at ANCHORS an offset so you don't hand-count: an absolute address, end[-N],
find:STR|0xHEX[+N], or chunk:ID[+N] (any walked format). --format picks the
output shape: raw (default for ranges) / value (default when typed) / hex / c /
py / b64.
"""

import base64
import os
import sys

from acidcat.core import bytefields as bf
from acidcat.core.anomalies import _declared_end, _rf64_end
from acidcat.core.riff import iter_chunks

_ENDIAN = {"be": ">", "le": "<", "both": "both"}


def register(subparsers):
    p = subparsers.add_parser(
        "carve", help="Extract a byte range or a typed field (chunk / offset / "
                      "anchored / struct) to a file or stdout.")
    p.add_argument("target", help="File to carve from (never modified).")
    p.add_argument("--offset", help="Start offset (0x.. hex or decimal).")
    p.add_argument("--at", metavar="EXPR",
                   help="Anchored start: 0xNN | end[-N] | find:STR|0xHEX[+N] | "
                        "chunk:ID[+N].")
    p.add_argument("--length", help="Number of bytes from the start (0x.. or decimal).")
    p.add_argument("--end", help="End offset (exclusive), instead of --length.")
    p.add_argument("--trailing", action="store_true",
                   help="Everything past the declared container end (RIFF/AIFF/RF64).")
    p.add_argument("--chunk", metavar="ID",
                   help="Payload of a named RIFF/AIFF chunk (e.g. data, COMM).")
    p.add_argument("--raw", action="store_true",
                   help="With --chunk, include the 8-byte chunk header.")
    p.add_argument("--type", metavar="T",
                   help="Decode the range as a typed value: u8..i64, f32/f64, "
                        "Ns (fixed string), cstr; optional be/le suffix.")
    p.add_argument("--count", type=int, default=1,
                   help="With --type, decode an array of this many values.")
    p.add_argument("--endian", choices=("be", "le", "both"), default="be",
                   help="Byte order for bare numeric types (default be; both "
                        "prints each interpretation -- the endian guess).")
    p.add_argument("--struct", metavar="SPEC",
                   help="Decode a labeled record: '@OFF name:type name:type ...' "
                        "(@OFF accepts any --at expression).")
    p.add_argument("--field", metavar="NAME",
                   help="Print a walker-decoded field by name (as shown by inspect).")
    p.add_argument("--format", choices=("raw", "value", "hex", "c", "py", "b64"),
                   help="Output shape (default: raw for ranges, value when typed).")
    p.add_argument("--batch", metavar="SRC",
                   help="Extract many regions: read `locate` records (JSON or TSV) "
                        "from SRC ('-' = stdin) and carve each from TARGET into -o DIR.")
    p.add_argument("-o", "--output", help="Write here (default: stdout; a DIR for --batch).")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress the summary line on stderr.")
    p.set_defaults(func=run)


# natural file extension for a carved region, by detected container format
_EXT = {"wav": "wav", "rf64": "wav", "aiff": "aiff", "aifc": "aiff", "8svx": "8svx",
        "flac": "flac", "ogg": "ogg", "sf2": "sf2", "mp3": "mp3"}


def _parse_records(text):
    """Parse `locate` output -- a JSON array, or TSV lines (offset, length, kind,
    format, ...) -- into [{offset, length, kind, format}]."""
    text = text.strip()
    if not text:
        return []
    if text[0] in "[{":
        import json
        d = json.loads(text)
        recs = d if isinstance(d, list) else d.get("regions", [])
        return [{"offset": r["offset"], "length": r.get("length", r["end"] - r["offset"]),
                 "kind": r.get("kind", "region"), "format": r.get("format")} for r in recs]
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            off, length = int(parts[0], 0), int(parts[1])
        except ValueError:
            continue
        fmt = parts[3] if len(parts) >= 4 else None
        out.append({"offset": off, "length": length,
                    "kind": parts[2] if len(parts) >= 3 else "region",
                    "format": None if fmt in (None, "raw-pcm", "") else fmt})
    return out


def _run_batch(args, filepath, size):
    if not args.output:
        print("acidcat carve --batch: needs -o DIR to write regions", file=sys.stderr)
        return 2
    try:
        text = sys.stdin.read() if args.batch == "-" else open(args.batch).read()
        recs = _parse_records(text)
    except (OSError, ValueError) as e:
        print(f"acidcat carve --batch: {e}", file=sys.stderr)
        return 2
    os.makedirs(args.output, exist_ok=True)
    done = skipped = 0
    with open(filepath, "rb") as f:
        for i, r in enumerate(recs):
            off, length = r["offset"], r["length"]
            if off < 0 or length <= 0 or off + length > size:
                skipped += 1
                continue
            f.seek(off)
            blob = f.read(length)
            ext = _EXT.get(r.get("format")) or "raw"
            name = f"{i:04d}_0x{off:08x}_{r.get('kind', 'region')}.{ext}"
            with open(os.path.join(args.output, name), "wb") as g:
                g.write(blob)
            done += 1
    if not args.quiet:
        print(f"carved {done} region(s) -> {args.output}"
              + (f" ({skipped} out-of-range skipped)" if skipped else ""),
              file=sys.stderr)
    return 0


def _int(text, what):
    try:
        return int(text, 0)
    except (ValueError, TypeError):
        raise ValueError(f"{what}: not an offset/length: {text!r}")


def _resolve_start(args, filepath, size):
    """Resolve the start offset from --at or --offset (or None if a range target
    like --trailing/--chunk is used instead)."""
    if args.at is not None:
        return bf.resolve_offset(args.at, filepath, size)
    if args.offset is not None:
        return _int(args.offset, "--offset")
    return None


def _resolve_range(args, filepath, size, typed_len=None):
    """Return (start, length). Exactly one start source: --at/--offset,
    --trailing, or --chunk. typed_len supplies the default length in typed mode."""
    chosen = [args.at is not None or args.offset is not None,
              bool(args.trailing), bool(args.chunk)]
    if sum(chosen) != 1:
        raise ValueError("pick exactly one of --offset/--at, --trailing, --chunk")

    if args.at is not None or args.offset is not None:
        start = _resolve_start(args, filepath, size)
        if args.end is not None:
            length = _int(args.end, "--end") - start
        elif args.length is not None:
            length = _int(args.length, "--length")
        elif typed_len is not None:
            length = typed_len
        else:
            length = size - start
        if length < 0:
            raise ValueError("range length is negative (--end before start?)")
        return start, length

    if args.trailing:
        with open(filepath, "rb") as f:
            head = f.read(16)
        end = _declared_end(head)
        if end is None and head[:4] in (b"RF64", b"BW64"):
            end = _rf64_end(filepath)
        if end is None:
            raise ValueError("no declared container size for --trailing (RIFF/AIFF/"
                             "RF64 only); use --offset for this format")
        if end >= size:
            raise ValueError(f"no trailing data: container end (0x{end:x}) at/past "
                             f"EOF (0x{size:x})")
        return end, size - end

    wanted = args.chunk.upper().ljust(4)[:4]
    try:
        for cid, offset, csize in iter_chunks(filepath):
            if cid.upper().ljust(4)[:4] == wanted:
                return (offset, 8 + csize) if args.raw else (offset + 8, csize)
    except Exception as e:
        raise ValueError(f"could not walk chunks (RIFF/AIFF only?): "
                         f"{e.__class__.__name__}: {e}")
    raise ValueError(f"no chunk {args.chunk!r} found (RIFF/AIFF only; use --offset)")


# ---- typed / struct / field modes ------------------------------------------

def _emit(text, output):
    if output:
        with open(output, "w", encoding="utf-8") as g:
            g.write(text + "\n")
    else:
        print(text)


def _fmt_bytes(blob, how):
    if how == "hex":
        return " ".join(f"{b:02x}" for b in blob)
    if how == "c":
        return "{ " + ", ".join(f"0x{b:02x}" for b in blob) + " }"
    if how == "py":
        return repr(bytes(blob))
    if how == "b64":
        return base64.b64encode(blob).decode("ascii")
    return None


def _run_typed(args, filepath, size):
    parsed = bf.parse_type(args.type, _ENDIAN.get(args.endian, ">"))
    kind = parsed[0]
    # fixed-size types give a known length; cstr is read to EOF and sized on decode
    unit = bf.type_size(parsed) if kind != "cstr" else None
    typed_len = unit * max(args.count, 1) if unit else None
    start, length = _resolve_range(args, filepath, size, typed_len=typed_len)
    length = min(length, size - start)
    with open(filepath, "rb") as f:
        f.seek(start)
        blob = f.read(length)

    fmt = args.format or "value"
    if fmt != "value":                                   # raw/hex/c/py/b64 of the bytes
        out = _fmt_bytes(blob, fmt)
        if out is None:
            sys.stdout.buffer.write(blob)
            return 0
        _emit(out, args.output)
        return 0

    # decode value(s)
    lines = []
    pos = 0
    for _ in range(max(args.count, 1)):
        chunk = blob[pos:]
        if args.endian == "both" and kind == "num":
            both = bf.decode_both_endian(chunk, args.type)
            lines.append(f"be={both['be']}  le={both['le']}")
        else:
            lines.append(str(bf.decode(chunk, parsed)))
        pos += bf.type_size(parsed, chunk)
    _emit("\n".join(lines), args.output)
    if not args.quiet:
        print(f"decoded {args.count}x {args.type} from 0x{start:08x}", file=sys.stderr)
    return 0


def _run_struct(args, filepath, size):
    spec = args.struct.split()
    base = 0
    if spec and spec[0].startswith("@"):
        base = bf.resolve_offset(spec.pop(0)[1:], filepath, size)
    with open(filepath, "rb") as f:
        f.seek(base)
        blob = f.read(min(size - base, 1 << 20))
    rows, pos = [], 0
    for token in spec:
        if ":" not in token:
            raise ValueError(f"struct field {token!r} must be name:type")
        name, tspec = token.split(":", 1)
        parsed = bf.parse_type(tspec, _ENDIAN.get(args.endian, ">"))
        seg = blob[pos:]
        val = bf.decode(seg, parsed)
        if name != "_":
            rows.append((name, tspec, base + pos, val))
        pos += bf.type_size(parsed, seg)
    if args.format == "value":
        _emit("\n".join(str(v) for _, _, _, v in rows), args.output)
    else:
        width = max((len(n) for n, _, _, _ in rows), default=4)
        _emit("\n".join(f"{n:<{width}}  @0x{o:08x}  {t:<6}  {v}"
                        for n, t, o, v in rows), args.output)
    return 0


def _run_field(args, filepath):
    from acidcat.core.walk import walk_file, Unsupported
    try:
        _label, chunks, _warns = walk_file(filepath)
    except Unsupported as e:
        print(f"acidcat carve: {e}", file=sys.stderr)
        return 1
    matches = [(cid, name, val) for cid, name, val in bf.flatten_fields(chunks)
               if name == args.field]
    if not matches:
        avail = sorted({name for _, name, _ in bf.flatten_fields(chunks)})
        print(f"acidcat carve: no field {args.field!r}; available: "
              f"{', '.join(avail) if avail else '(none)'}", file=sys.stderr)
        return 2
    _emit("\n".join(str(v) for _, _, v in matches), args.output)
    return 0


def run(args):
    filepath = args.target
    if not os.path.isfile(filepath):
        print(f"acidcat carve: {filepath}: No such file", file=sys.stderr)
        return 1
    size = os.path.getsize(filepath)

    if args.batch is not None:
        return _run_batch(args, filepath, size)

    try:
        if args.field is not None:
            return _run_field(args, filepath)
        if args.struct is not None:
            return _run_struct(args, filepath, size)
        if args.type is not None:
            return _run_typed(args, filepath, size)
        start, length = _resolve_range(args, filepath, size)
    except (ValueError, bf.FieldError) as e:
        print(f"acidcat carve: {e}", file=sys.stderr)
        return 2

    if start < 0 or start > size:
        print(f"acidcat carve: start 0x{start:x} outside the file (0..0x{size:x})",
              file=sys.stderr)
        return 2
    avail = size - start
    if length > avail:
        print(f"acidcat carve: range runs {length - avail:,} bytes past EOF; "
              f"carving the {avail:,} available", file=sys.stderr)
        length = avail

    with open(filepath, "rb") as f:
        f.seek(start)
        blob = f.read(length)

    # non-raw text formats for a plain range (hex/c/py/b64)
    if args.format and args.format not in ("raw",):
        out = _fmt_bytes(blob, args.format)
        if out is not None:
            _emit(out, args.output)
            if not args.quiet:
                print(f"carved {len(blob):,} bytes from 0x{start:08x} ({args.format})",
                      file=sys.stderr)
            return 0

    if not args.output and sys.stdout.isatty():
        print("acidcat carve: refusing to write binary to the terminal; "
              "redirect or pass -o FILE", file=sys.stderr)
        return 2

    if args.output:
        with open(args.output, "wb") as g:
            g.write(blob)
        if not args.quiet:
            print(f"carved {len(blob):,} bytes from 0x{start:08x} -> {args.output}",
                  file=sys.stderr)
    else:
        sys.stdout.buffer.write(blob)
        if not args.quiet:
            print(f"carved {len(blob):,} bytes from 0x{start:08x}", file=sys.stderr)
    return 0
