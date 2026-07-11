"""
acidcat carve -- extract a structurally-identified byte range to a file or stdout.

The general "pull out a region" primitive. Forensics is the motivating case
(extract a flagged polyglot / appended blob for downstream analysis), but the
same verb serves any byte surgery: raw headerless PCM out of a WAV, a codec-
config blob to feed a decoder, or a chunk carved from a real file to build a
minimal test specimen.

Read-only on the source: carve never modifies the input, it only writes the
carved bytes out. Pick exactly one target:

    acidcat carve FILE --offset 0x100 --length 256          # explicit range
    acidcat carve FILE --offset 0x100 --end 0x200 -o out.bin
    acidcat carve FILE --trailing -o appended.bin           # blob past the container
    acidcat carve FILE --chunk data -o audio.raw            # a RIFF/AIFF chunk payload

With no -o the raw bytes go to stdout (pipe-friendly); carve refuses to spew
binary at an interactive terminal, so redirect or pass -o there.
"""

import os
import sys

from acidcat.core.anomalies import _declared_end, _rf64_end
from acidcat.core.riff import iter_chunks


def register(subparsers):
    p = subparsers.add_parser(
        "carve", help="Extract a byte range (chunk / trailing blob / offset) to a file.")
    p.add_argument("target", help="File to carve from (never modified).")
    p.add_argument("--offset", help="Start offset (0x.. hex or decimal).")
    p.add_argument("--length", help="Number of bytes from --offset (0x.. or decimal).")
    p.add_argument("--end", help="End offset (exclusive), instead of --length.")
    p.add_argument("--trailing", action="store_true",
                   help="Everything past the declared container end (RIFF/AIFF/RF64).")
    p.add_argument("--chunk", metavar="ID",
                   help="Payload of a named RIFF/AIFF chunk (e.g. data, COMM). "
                        "First match; use --offset for other formats.")
    p.add_argument("--raw", action="store_true",
                   help="With --chunk, include the 8-byte chunk header.")
    p.add_argument("-o", "--output", help="Write here (default: stdout).")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress the summary line on stderr.")
    p.set_defaults(func=run)


def _int(text, what):
    """Parse a hex (0x..) or decimal integer, or raise ValueError with context."""
    try:
        return int(text, 0)
    except (ValueError, TypeError):
        raise ValueError(f"{what}: not an offset/length: {text!r}")


def _resolve_range(args, filepath, size):
    """Return (start, length) for the chosen target, or raise ValueError.
    Exactly one of --offset / --trailing / --chunk must be given."""
    chosen = [bool(args.offset is not None), bool(args.trailing), bool(args.chunk)]
    if sum(chosen) != 1:
        raise ValueError("pick exactly one of --offset, --trailing, --chunk")

    if args.offset is not None:
        start = _int(args.offset, "--offset")
        if args.end is not None:
            end = _int(args.end, "--end")
            length = end - start
        elif args.length is not None:
            length = _int(args.length, "--length")
        else:
            length = size - start                    # to EOF
        if length < 0:
            raise ValueError("range length is negative (--end before --offset?)")
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
            raise ValueError(f"no trailing data: the container end (0x{end:x}) is at "
                             f"or past EOF (0x{size:x})")
        return end, size - end

    # --chunk: RIFF/AIFF payload via iter_chunks (unambiguous: [offset+8, +size])
    wanted = args.chunk.upper().ljust(4)[:4]
    try:
        for cid, offset, csize in iter_chunks(filepath):
            if cid.upper().ljust(4)[:4] == wanted:
                if args.raw:
                    return offset, 8 + csize
                return offset + 8, csize
    except Exception as e:
        raise ValueError(f"could not walk chunks (RIFF/AIFF only?): "
                         f"{e.__class__.__name__}: {e}")
    raise ValueError(f"no chunk {args.chunk!r} found (RIFF/AIFF only; use --offset "
                     f"for other formats)")


def run(args):
    filepath = args.target
    if not os.path.isfile(filepath):
        print(f"acidcat carve: {filepath}: No such file", file=sys.stderr)
        return 1
    size = os.path.getsize(filepath)

    try:
        start, length = _resolve_range(args, filepath, size)
    except ValueError as e:
        print(f"acidcat carve: {e}", file=sys.stderr)
        return 2

    if start < 0 or start > size:
        print(f"acidcat carve: start 0x{start:x} outside the file "
              f"(0..0x{size:x})", file=sys.stderr)
        return 2
    avail = size - start
    if length > avail:
        print(f"acidcat carve: range runs {length - avail:,} bytes past EOF; "
              f"carving the {avail:,} available", file=sys.stderr)
        length = avail

    if not args.output and sys.stdout.isatty():
        print("acidcat carve: refusing to write binary to the terminal; "
              "redirect or pass -o FILE", file=sys.stderr)
        return 2

    with open(filepath, "rb") as f:
        f.seek(start)
        blob = f.read(length)

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
