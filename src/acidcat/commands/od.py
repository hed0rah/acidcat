"""acidcat od -- objdump-x-style annotated, colored hex dump of a file's structure.

Lays out the parsed bytes as a hex dump -- offset, hex, meaning -- with each
chunk/block header and each decoded field on its own line, the field's byte-run
colored (cycling palette) and labeled with its name/value/note, so the structure
is legible directly in the raw byte stream. Bulk audio payload is elided.

Like od(1), it never refuses to show you bytes. A format it can walk gets the
annotated layout; anything else -- a proprietary container, a carved region, a
raw dump off a device -- falls back to a plain hex dump. Structure is a bonus,
not a precondition.

    acidcat od song.wav                       # annotated, by chunk and field
    acidcat od unknown.bin                    # plain hex, no walker needed
    acidcat od blob.img --offset 0x5d1000 --length 2048
    acidcat od blob.img --at find:RIFF --length 64
    acidcat locate blob.img --json | acidcat od blob.img --region 0

Complements `inspect --hex` (a value-first table); this is a bytes-first layout.
"""

import os
import sys
from acidcat.util.color import add_color_arg, color_enabled
from acidcat.util.stdin import as_given

from acidcat.core.infra import bytefields as bf
from acidcat.commands._output import add_output_format_arg, chosen_format, add_report_arg
from acidcat.core.infra.mapped import map_file
from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported

# cycling foreground colors so adjacent fields are visually distinct
_FIELD_COLORS = (36, 32, 33, 35, 34, 31, 96, 92, 93, 95)


def register(subparsers):
    p = subparsers.add_parser(
        "od", help="objdump-x-style annotated, colored hex dump of a file's structure")
    p.add_argument("target", metavar="FILE", help="File to dump, or '-' for stdin.")
    p.add_argument("addrs", nargs="*", metavar="ADDR",
                   help="What to dump: a node (its payload: RIFF/smpl), a field "
                        "(RIFF/fmt_#sample_rate) or bytes (@0x100+64, "
                        "@0x100..0x200). Several are dumped in turn. Without "
                        "one, the whole file, annotated by its structure.")
    # table (the dump) or json: with an ADDR, each range as
    # {addr, offset, length, hex}
    add_output_format_arg(p, only=("table", "json"), deprecated_f=False)
    add_report_arg(p)
    add_color_arg(p)
    p.add_argument("--width", type=int, default=16, metavar="N",
                   help="hex bytes per line, and per field before eliding "
                        "(default 16)")
    # a search anchor, the one range spelling that is not an ADDR
    p.add_argument("--at", metavar="EXPR",
                   help="A search anchor: end[-N] | find:STR|0xHEX[+N] | "
                        "chunk:ID[+N] | 0xNN; dumps from there to the end.")
    p.add_argument("--region", type=int, metavar="N",
                   help="Dump the Nth region reported by `locate` (0-based); "
                        "runs locate itself, so no piping is required.")
    p.add_argument("--marks", action="store_true",
                   help="Tint the raw dump by byte class, and highlight values "
                        "that look like a file size or an offset table. "
                        "Inferred, not decoded -- and only on the raw dump; "
                        "a walked file already colours by decoded field.")
    # the 1.8 --offset/--length/--end are aliases now (cli_aliases)
    p.set_defaults(func=run, offset=None, length=None, end=None)




def _c(code, text, on):
    return f"\033[{code}m{text}\033[0m" if on else text


def _hexcells(b):
    return " ".join(f"{x:02x}" for x in b)


def _ascii(b):
    return "".join(chr(x) if 32 <= x < 127 else "." for x in b)


def run(args):
    from acidcat.util.stdin import resolved_input
    with resolved_input(args.target) as _p:
        if _p is None:
            print("acidcat od: no data on stdin", file=sys.stderr)
            return 2
        args.target = _p
        return _run(args)


def _size(path):
    import os
    return os.path.getsize(path)


def _int(text, what):
    try:
        return int(text, 0)
    except (ValueError, TypeError):
        raise ValueError(f"{what}: not an offset/length: {text!r}")


def _requested_range(args, path, size):
    """(start, length) from --offset/--at/--length/--end/--region, or None when
    the whole file was asked for. Raises ValueError on a bad expression.

    Read through getattr so a programmatic caller (the TUI, a test, another
    command) can pass a namespace carrying only the flags it cares about.
    """
    region = getattr(args, "region", None)
    at = getattr(args, "at", None)
    offset = getattr(args, "offset", None)
    length_s = getattr(args, "length", None)
    end_s = getattr(args, "end", None)
    if region is not None:
        from acidcat.core.forensics import locate as locatemod
        with open(path, "rb") as f:
            recs = locatemod.locate(f.read(), mode="normal")
        if not recs:
            raise ValueError("no regions located in this file")
        if not 0 <= region < len(recs):
            raise ValueError(f"--region {region} out of range "
                             f"(locate found {len(recs)}: 0..{len(recs) - 1})")
        r = recs[region]
        return r["offset"], r["length"]

    start = None
    if at is not None:
        start = bf.resolve_offset(at, path, size)
    elif offset is not None:
        start = _int(offset, "--offset")
    if start is None and length_s is None and end_s is None:
        return None
    start = start or 0
    if end_s is not None:
        length = max(0, _int(end_s, "--end") - start)
    elif length_s is not None:
        length = _int(length_s, "--length")
    else:
        length = size - start
    return start, length


# How much an automatic (no-walker) dump shows before it stops and says so.
# An explicit --offset/--length is never capped: the user asked for that range.
_AUTO_DUMP_CAP = 16 * 1024 * 1024


# Foreground says what kind of byte this is; background says the value means
# something. Two channels, because the structured path already spends
# foreground on field identity and an overlay must not fight it.
_MARK_BG = {"mark:size": "#3A3E45", "mark:table": "#4A3327"}


def _styled_cells(row, base, tags, on):
    """Hex cells for one row, with escapes RUN-LENGTH ENCODED.

    A per-byte escape is about 19 bytes. Emitting one per byte turns a 16 MB
    dump into roughly 380 MB of stdout, on a path that has already cost this
    project 1.4 GB of output in five and a half minutes (see the note in
    _run). A style only changes when the tags change, and in a header the runs
    are long -- a field of nulls is one escape, not sixty-four.
    """
    from acidcat.tui_theme import BYTE_CLASS_TEXT
    from acidcat.util.color import bg, fg

    out = []
    run = []
    run_key = object()

    def flush():
        if not run:
            return
        text = " ".join(f"{x:02x}" for x in run)
        if on and run_key is not None:
            cls, mark = run_key
            if cls:
                text = fg(BYTE_CLASS_TEXT[cls], text)
            if mark:
                text = bg(_MARK_BG[mark], text)
        out.append(text)
        run.clear()

    for i, b in enumerate(row):
        t = tags.get(base + i, ())
        cls = next((x.split(":", 1)[1] for x in t if x.startswith("class:")), None)
        mark = next((x for x in t if x.startswith("mark:")), None)
        key = (cls, mark)
        if key != run_key and run:
            flush()
        run_key = key
        run.append(b)
    flush()
    return " ".join(out)


# annotate() returns a tag list per byte, which is the right shape for a hex
# window and the wrong one for a whole file: measured, it costs ~217 MB per MB
# of input, so the 16 MB _AUTO_DUMP_CAP would ask for ~3.5 GB. Marks are a
# header-reading aid -- nobody eyeballs 16 MB of hex for a size field -- so the
# overlay is bounded and the summary line says where it stopped.
_MARK_CAP = 256 * 1024


def _marks_for(args, data, start, length):
    """(tags, covered) for the window about to be dumped, or None if --marks
    is off.

    The window is what gets scanned, but the marks are defined against the
    whole file: a size field means nothing measured against a slice, and an
    offset that lands in-file is only checkable if you know how big the file
    is. base_off keeps the 4-byte grid the file's, not the window's.
    """
    if not getattr(args, "marks", False):
        return None
    from acidcat.core.probe import annotate

    end = min(start + length, len(data), start + _MARK_CAP)
    covered = end - start
    return annotate(bytes(data[start:end]), base_off=start,
                    file_size=len(data)), covered


def _raw_dump(data, start, length, width, on, note, marks=None):
    """A plain hex dump of a byte range -- what od(1) does. Used when the file
    has no walker, or when an explicit range was asked for."""
    end = min(start + length, len(data))
    tags, covered = marks if marks else (None, 0)
    print(_c("1", note, on))
    if tags:
        line = _mark_summary(tags, on, covered, end - start)
        if line:
            print(line)
    for base in range(start, end, width):
        row = data[base:min(base + width, end)]
        if tags and base - start < covered:
            cells = _styled_cells(row, base - start, tags, on)
            # cells carries escapes, so it cannot be width-padded by format
            # spec -- the padding has to be computed from the visible length,
            # which is the same 3n-1 the unstyled path produces.
            pad = " " * max(0, width * 3 - (3 * len(row) - 1))
            print(f"  0x{base:08x}  {cells}{pad} {_ascii(row)}")
        else:
            print(f"  0x{base:08x}  {_hexcells(row):<{width * 3}} {_ascii(row)}")
    return 0


def _mark_summary(tags, on, covered, dumped):
    """One line naming what was inferred, rather than forty silent hints.

    A statistical inference must never outrank a checked magic number
    (core/forensics/locate.py), and a view that estimates has to say so
    (tests/test_tui_viz_honesty.py). Saying it once discharges both.
    """
    kinds = {}
    for ts in tags.values():
        for t in ts:
            if t.startswith("mark:"):
                kinds[t] = kinds.get(t, 0) + 1
    short = covered < dumped
    if not kinds and not short:
        return None
    words = {"mark:size": "a value matching the file size",
             "mark:table": "a run of ascending in-file offsets"}
    parts = [f"{words[k]} ({n // 4} field(s))" for k, n in sorted(kinds.items())]
    body = "; ".join(parts) if parts else "nothing found"
    # A cap reported as a complete scan is the bug this project keeps finding
    # in itself: say which bytes were actually looked at.
    if short:
        body += f" -- in the first {covered:,} of {dumped:,} bytes dumped"
    return _c("2", "  marks: " + body + " -- inferred, not decoded", on)


def _run_addrs(args, path, addrs, on):
    """Dump each ADDR's bytes in turn, or give them as JSON.

    Every ADDR that resolves is dumped and each one that does not is named on
    stderr, as `dump FILE acid smpl` printed the acid chunk and skipped the
    missing smpl. Exit 0 when at least one resolved; otherwise 1 when they
    named nothing there, 2 when one could not be read as an address at all."""
    from acidcat import AddrError
    from acidcat.commands import _addr
    from acidcat.core.infra.render import format_json
    ranges, failed = [], 0
    for a in addrs:
        try:
            layer, start, length, name, blob = _addr.locate(path, a)
        except AddrError as e:
            print(f"acidcat od: {as_given(path)}: {e}", file=sys.stderr)
            failed = max(failed, 1)
            continue
        except OSError as e:
            # the file, not this address: no other address will do better
            print(f"acidcat od: {as_given(path)}: {e.strerror or e}", file=sys.stderr)
            return 2
        except ValueError as e:
            print(f"acidcat od: {as_given(path)}: {e}", file=sys.stderr)
            failed = 2
            continue
        ranges.append((name, start, length, blob))
    if not ranges:
        if chosen_format(args) == "json":
            format_json([], sys.stdout)     # no rows is still an array
        return failed
    data, close = map_file(path)
    try:
        # an address in a decoded layer reads that layer's bytes, and its
        # offsets are the layer's (`1:@0+16`, `1:lh5/header#frames`)
        if chosen_format(args) == "json":
            format_json([{"addr": a, "offset": s, "length": n,
                          "hex": bytes((data if blob is None else blob)[s:s + n]).hex()}
                         for a, s, n, blob in ranges], sys.stdout)
            return 0
        rc = 0
        for a, start, length, blob in ranges:
            src = data if blob is None else blob
            rc = _raw_dump(src, start, length, args.width, on,
                           f"{as_given(path)}  {a}  0x{start:08x} .. 0x{start + length:08x}"
                           f"  ({length:,} bytes)",
                           _marks_for(args, src, start, length)
                           if blob is None else None) or rc
        return rc
    finally:
        close()


def _run(args):
    path = args.target
    on = color_enabled(args)
    # A directory reaches _size() fine and only fails later, inside sniff, as a
    # raw PermissionError on Windows or IsADirectoryError elsewhere -- the one
    # uncaught traceback across 21 verbs x 4 kinds of bad input. `od` takes one
    # file because its whole vocabulary is offsets into that file.
    if path != "-" and os.path.isdir(path):
        print(f"acidcat od: {as_given(path)}: Is a directory", file=sys.stderr)
        return 2
    addrs = getattr(args, "addrs", None) or []
    if addrs:
        return _run_addrs(args, path, addrs, on)
    if chosen_format(args) == "json":
        print("acidcat od: --json needs an ADDR (the bytes to give as JSON)",
              file=sys.stderr)
        return 2
    try:
        rng = _requested_range(args, path, _size(path))
    except (ValueError, KeyError, OSError) as e:
        print(f"acidcat od: {as_given(path)}: {e}", file=sys.stderr)
        # a search that finds nothing is the answer no (cli-2.0.md section 1)
        return 1 if isinstance(e, bf.AnchorNotFound) else 2

    # an explicit byte range has no structure of its own -- dump it plainly
    if rng is not None:
        start, length = rng
        data, close = map_file(path)
        try:
            return _raw_dump(data, start, length, args.width, on,
                             f"{as_given(path)}  0x{start:08x} .. 0x{start + length:08x}"
                             f"  ({length:,} bytes)",
                             _marks_for(args, data, start, length))
        finally:
            close()

    try:
        label, chunks, warns = walk_file(path)
    except Unsupported:
        # od never refuses to show bytes: no walker means a plain dump, not an
        # error. `inspect` is where "I do not know this format" is the answer.
        data, close = map_file(path)
        try:
            # Bounded, and it says so. Unbounded, this printed 285 MB as hex --
            # 1.4 GB of stdout over five and a half minutes -- where the old
            # behaviour was to refuse in 0.19s. `locate` in this same release
            # learned to name the engine that stopped short; the fallback that
            # replaced a refusal has to do the same.
            shown = min(len(data), _AUTO_DUMP_CAP)
            note = (f"{as_given(path)}  {len(data):,} bytes  "
                    f"(no structural walker -- raw dump)")
            if shown < len(data):
                note += (f"\n  showing the first {shown:,} bytes; "
                         f"give an ADDR (@0x{shown:x}+N) for the rest")
            return _raw_dump(data, 0, shown, args.width, on, note,
                             _marks_for(args, data, 0, shown))
        finally:
            close()
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
            if c.get("offset") is None:
                continue                # a tree node (an SF2 preset) has no bytes to dump
            base = c.get("payload_base")
            if base is None:
                base = c["offset"] + 8
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
