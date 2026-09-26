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
find:STR|0xHEX[+N], or chunk:ID[+N] (any walked format). --encoding picks how the
carved bytes are serialized: raw (default for ranges) / value (default when
typed) / hex / c / py / b64.
"""

import base64
import os
import argparse
import sys

from acidcat.util import outpath

from acidcat.core.infra import bytefields as bf
from acidcat.core.forensics.anomalies import _declared_end, _rf64_end
from acidcat.core.formats.riff import iter_chunks
# the one audio-container table (shared with locate): format id -> file extension
from acidcat.core.infra.sniff import AUDIO_CONTAINER_EXT as _EXT

_ENDIAN = {"be": ">", "le": "<", "both": "both"}

# Sample rate is not recoverable from raw bytes. When --wrap has to write a
# header anyway, this is the assumption it states rather than hides.
_ASSUMED_RATE = 44100


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
    p.add_argument("--encoding", choices=("raw", "value", "hex", "c", "py", "b64"),
                   help="How to serialize carved bytes: raw|value|hex|c|py|b64 "
                        "(default: raw for ranges, value when typed).")
    p.add_argument("--format", dest="encoding",
                   choices=("raw", "value", "hex", "c", "py", "b64"),
                   help=argparse.SUPPRESS)          # deprecated alias for --encoding
    p.add_argument("--batch", metavar="SRC",
                   help="Extract many regions: read `locate` records (JSON or TSV) "
                        "from SRC ('-' = stdin) and carve each from TARGET into -o DIR.")
    p.add_argument("--wrap", action="store_true",
                   help="With --batch: give headerless regions a WAV header "
                        "using the geometry from `locate --analyze`, so they "
                        "come out playable instead of as .raw. Containers "
                        "already have their own header and are left alone.")
    p.add_argument("--rate", type=int, metavar="HZ",
                   help="With --wrap: sample rate for wrapped regions. Rate is "
                        "playback metadata and is not recoverable from the "
                        "bytes, so this overrides the guess.")
    p.add_argument("--layer", type=int, metavar="N",
                   help="Write layer N: a decoded image the walk found (a packed "
                        "YM's unpacked tune is layer 1), checked as the walk "
                        "checked it. 0 is the file itself.")
    p.add_argument("-o", "--output", help="Write here (default: stdout; a DIR for --batch).")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress the summary line on stderr.")
    p.set_defaults(func=run)


def _as_int(v, what):
    """An int from a JSON number or a numeric string, or a ValueError naming it.

    Accepts "0x10" because every other address acidcat takes does, and a person
    writing these records by hand has no reason to think this one is different.
    """
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise ValueError(f"{what} is {type(v).__name__}, not a number")
    if isinstance(v, int):
        return v
    try:
        return int(v.strip(), 0)
    except ValueError:
        raise ValueError(f"{what} is {v!r}, which is not a number") from None


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
        # geometry rides along when `locate --analyze` produced it; --wrap needs
        # it, and dropping it here is why the two verbs could not compose.
        # length is resolved the long way because dict.get evaluates its default
        # eagerly -- `r.get("length", r["end"] - r["offset"])` raises KeyError on
        # a record that has length and no end, which is a perfectly reasonable
        # thing for someone scripting their own regions to write.
        out = []
        for i, r in enumerate(recs):
            if not isinstance(r, dict):
                raise ValueError(f"record {i} is {type(r).__name__}, not an object")
            # These records are meant to be hand-writable -- the missing-length
            # branch below exists for exactly that. So the fields have to be
            # validated rather than indexed: a record with no 'offset', or a
            # "0x10" someone typed because every other acidcat surface accepts
            # hex, used to raise KeyError/TypeError straight out of the parser
            # as an uncaught traceback.
            if "offset" not in r:
                raise ValueError(f"record {i} has no 'offset'")
            offset = _as_int(r["offset"], f"record {i} 'offset'")
            length = r.get("length")
            if length is None:
                if "end" not in r:
                    raise ValueError(
                        f"region at offset {offset} has neither "
                        f"'length' nor 'end'")
                length = _as_int(r["end"], f"record {i} 'end'") - offset
            else:
                length = _as_int(length, f"record {i} 'length'")
            out.append({"offset": offset, "length": length,
                        "kind": r.get("kind", "region"), "format": r.get("format"),
                        "geometry": r.get("geometry")})
        return out
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
        # `locate --analyze --output-format tsv` appends width/channels/endian.
        # Reading them here is what makes --wrap work from the TSV path too;
        # without it the two output formats were not interchangeable.
        geometry = None
        if len(parts) >= 8:
            try:
                geometry = {"width": int(parts[5]), "channels": int(parts[6]),
                            "endian": parts[7] or None}
            except ValueError:
                geometry = None
        out.append({"offset": off, "length": length,
                    "kind": parts[2] if len(parts) >= 3 else "region",
                    "format": None if fmt in (None, "raw-pcm", "") else fmt,
                    "geometry": geometry})
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
    done = skipped = headered = no_geometry = 0
    with open(filepath, "rb") as f:
        for i, r in enumerate(recs):
            off, length = r["offset"], r["length"]
            if off < 0 or length <= 0 or off + length > size:
                skipped += 1
                continue
            f.seek(off)
            blob = f.read(length)
            ext = _EXT.get(r.get("format")) or "raw"
            # getattr: callers construct args objects programmatically, and a
            # new flag must not break a script that predates it
            want_wrap = getattr(args, "wrap", False)
            wrapped = None
            if want_wrap and ext == "raw":
                wrapped = _wrap_blob(blob, r.get("geometry"),
                                     getattr(args, "rate", None))
                if wrapped is not None:
                    blob, ext = wrapped, "wav"
            name = f"{i:04d}_0x{off:08x}_{r.get('kind', 'region')}.{ext}"
            with open(os.path.join(args.output, name), "wb") as g:
                g.write(blob)
            done += 1
            if want_wrap and ext == "raw":
                # _wrap_blob returns None when the record carries no usable
                # geometry, which is the normal case for `locate --json`
                # without --analyze. Saying nothing meant --wrap looked
                # honoured while every region landed as a headerless .raw.
                no_geometry += 1
            elif want_wrap and wrapped is not None:
                headered += 1
    if not args.quiet:
        extra = []
        if skipped:
            extra.append(f"{skipped} out-of-range skipped")
        if getattr(args, "wrap", False) and headered:
            chosen = getattr(args, "rate", None)
            how = (f"at {chosen} Hz" if chosen
                   else f"at an assumed {_ASSUMED_RATE} Hz -- pass --rate if "
                        f"you know better")
            extra.append(f"{headered} headerless region(s) wrapped as WAV {how}")
        if no_geometry:
            extra.append(f"{no_geometry} left raw: no sample geometry in the "
                         f"record (re-run locate with --analyze)")
        print(f"carved {done} region(s) -> {args.output}"
              + (f" ({', '.join(extra)})" if extra else ""), file=sys.stderr)
    # A shell pipeline reports the LAST command's status, so `locate | carve`
    # exited 0 on a blob with no audio in it even after locate learned to
    # return 1 -- carve is the one whose code the script actually sees.
    return 0 if done else 1


def _wrap_blob(blob, geometry, rate_override):
    """Give a headerless region a WAV header from the geometry `locate
    --analyze` inferred, or None if there is nothing to go on.

    Only raw blobs are touched. A carved container already has its own header
    and wrapping it would produce a WAV containing a WAV.
    """
    from acidcat.util.play import _wav_wrap
    from acidcat.commands.wrap import _swap

    geo = geometry or {}
    # `width` is BITS throughout audioscan (analyze_geometry does width // 8 to
    # get bytes). Guessing at the unit here would silently render a genuine
    # 8-bit region as a 64-bit header.
    bits = geo.get("width")
    if bits not in (8, 16, 24, 32, 64):
        return None
    channels = geo.get("channels") or 1
    floating = bool(geo.get("float"))
    # Rate is playback metadata -- it is not in the bytes, and analyze reports
    # it as None with a candidate list. Taking candidates[0] would mean 8000 Hz,
    # which is the least likely of them and would play everything five times too
    # slow. There is no evidence to weigh here, so use the common default and
    # let the caller override; the summary line says the rate was assumed.
    rate = rate_override or geo.get("rate") or _ASSUMED_RATE
    if not 1 <= rate <= 768000 or channels < 1:
        return None

    block = channels * (bits // 8)
    blob = blob[:len(blob) - (len(blob) % block)] if block else blob
    if not blob:
        return None
    if geo.get("endian") == "be":
        blob = _swap(blob, bits)
    return _wav_wrap(blob, rate, channels, bits, 3 if floating else 1)


class NotFound(ValueError):
    """The named region is not in this file. A negative result, not a usage
    error -- carve returns 1 for these and 2 for a malformed invocation."""


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
            raise NotFound(f"no trailing data: container end (0x{end:x}) at/past "
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
    raise NotFound(f"no chunk {args.chunk!r} found (RIFF/AIFF only; use --offset)")


# ---- typed / struct / field modes ------------------------------------------

def _write_out(blob, output, binary=True):
    """Write to `output`; return an error message instead of raising.

    An unwritable path used to escape as a raw OSError, which the CLI's
    closed-pipe handler then mistook for "the reader went away" and turned into
    a silent exit 0 -- a scripted carve reported success and wrote no file.
    """
    try:
        if binary:
            with open(output, "wb") as g:
                g.write(blob)
        else:
            with open(output, "w", encoding="utf-8") as g:
                g.write(blob)
    except OSError as e:
        return f"acidcat carve: {output}: {e.strerror or e}"
    return None


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


def _run_layer(args, filepath):
    """`--layer N`: the bytes of a layer of the file's v1 Document, decoded
    from the file by the decoder the walk named and checked the same way."""
    from acidcat.core.infra import contract, layers
    from acidcat.core.infra.source import MappedSource
    from acidcat.core.walk.base import Unsupported
    with MappedSource(filepath) as src:
        if args.layer == 0:
            blob, what = bytes(src.buffer()), "the file"
        else:
            try:
                doc = contract.walk(src)
            except Unsupported as e:
                raise NotFound(f"{filepath}: no layers: {e}") from None
            ids = [l["id"] for l in doc["layers"]]
            if args.layer not in ids:
                raise NotFound(f"{filepath}: no layer {args.layer}; the walk "
                               f"found {', '.join(map(str, ids))}")
            lay = next(l for l in doc["layers"] if l["id"] == args.layer)
            try:
                blob = layers.layer_bytes(doc, args.layer, src.buffer())
            except layers.LayerError as e:
                raise ValueError(str(e)) from None
            v = lay["verdict"]
            what = (f"layer {args.layer} ({lay['name']}, {lay['decoder']['name']}, "
                    f"{v['result']} {v['method']})")
    if args.encoding and args.encoding != "raw":
        text = _fmt_bytes(blob, args.encoding)
        if text is not None:
            _emit(text, args.output)
            return 0
    if not args.output and sys.stdout.isatty():
        print("acidcat carve: refusing to write binary to the terminal; "
              "redirect or pass -o FILE", file=sys.stderr)
        return 2
    if args.output:
        err = _write_out(blob, args.output)
        if err:
            print(err, file=sys.stderr)
            return 1
    else:
        sys.stdout.buffer.write(blob)
    if not args.quiet:
        print(f"carved {len(blob):,} bytes of {what}"
              + (f" -> {args.output}" if args.output else ""), file=sys.stderr)
    return 0


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

    fmt = args.encoding or "value"
    if fmt != "value":                                   # raw/hex/c/py/b64 of the bytes
        out = _fmt_bytes(blob, fmt)
        if out is None:
            # the range path already refuses this; --type ... --encoding raw
            # reached the same write without the guard
            if not args.output and sys.stdout.isatty():
                print("acidcat carve: refusing to write binary to the terminal "
                      "-- use -o FILE or --encoding hex", file=sys.stderr)
                return 2
            if args.output:
                err = _write_out(blob, args.output)
                if err:
                    print(err, file=sys.stderr)
                    return 1
            else:
                sys.stdout.buffer.write(blob)
            return 0
        return _emit(out, args.output)

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
    if args.encoding == "value":
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
        return 2

    # carve's own --help promises "File to carve from (never modified)". With
    # -o pointing back at the target that promise was broken silently and
    # catastrophically: `carve self.wav --offset 0 --length 4 -o self.wav`
    # opened the output for writing, truncated it, and reported success -- a
    # 2,044-byte WAV became 4 bytes, exit 0, no backup. Reachable by tab
    # completion or a --batch loop that forgets to change directory.
    out = getattr(args, "output", None)
    if out and not args.batch and outpath.same_file(filepath, out):
        print(f"acidcat carve: {out}: output is the input; refusing to "
              f"overwrite the file being carved from", file=sys.stderr)
        return 2

    size = os.path.getsize(filepath)

    if args.batch is not None:
        return _run_batch(args, filepath, size)

    try:
        if getattr(args, "layer", None) is not None:
            return _run_layer(args, filepath)
        if args.field is not None:
            return _run_field(args, filepath)
        if args.struct is not None:
            return _run_struct(args, filepath, size)
        if args.type is not None:
            return _run_typed(args, filepath, size)
        start, length = _resolve_range(args, filepath, size)
    except NotFound as e:
        # ran fine, the thing you asked for is not in this file. Distinct from
        # the usage errors below, which share ValueError: `carve --chunk ZZZZ`
        # returned 2 and `dump FILE ZZZZ` returned 1 for the identical
        # question, so a script could not branch without knowing which verb it
        # had called.
        print(f"acidcat carve: {e}", file=sys.stderr)
        return 1
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
    if args.encoding and args.encoding not in ("raw",):
        out = _fmt_bytes(blob, args.encoding)
        if out is not None:
            _emit(out, args.output)
            if not args.quiet:
                print(f"carved {len(blob):,} bytes from 0x{start:08x} ({args.encoding})",
                      file=sys.stderr)
            return 0

    if not args.output and sys.stdout.isatty():
        print("acidcat carve: refusing to write binary to the terminal; "
              "redirect or pass -o FILE", file=sys.stderr)
        return 2

    if args.output:
        err = _write_out(blob, args.output)
        if err:
            print(err, file=sys.stderr)
            return 1
        if not args.quiet:
            print(f"carved {len(blob):,} bytes from 0x{start:08x} -> {args.output}",
                  file=sys.stderr)
    else:
        sys.stdout.buffer.write(blob)
        if not args.quiet:
            print(f"carved {len(blob):,} bytes from 0x{start:08x}", file=sys.stderr)
    return 0
