"""
acidcat chunks -- walk RIFF chunks in a file, showing offsets and parsed fields.
"""

import os
import sys

from acidcat.core.riff import parse_riff, iter_chunks, get_riff_info
from acidcat.core.formats import output


def register(subparsers):
    p = subparsers.add_parser("chunks", help="Walk RIFF chunks in a WAV file.")
    p.add_argument("target", help="Path to a WAV file.")
    p.add_argument("-f", "--format", default="table", choices=["table", "json", "csv"],
                   help="Output format (default: table).")
    p.add_argument("-o", "--output", help="Write output to file.")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Diagnostic lines on stderr (container size, walk summary).")
    p.set_defaults(func=run)


def _vlog(args, msg):
    if getattr(args, "verbose", False) and not getattr(args, "quiet", False):
        print(msg, file=sys.stderr)


def run(args):
    filepath = args.target
    if os.path.isdir(filepath):
        print(f"acidcat chunks: {filepath}: is a directory (expected a file)", file=sys.stderr)
        return 1
    if not os.path.isfile(filepath):
        print(f"acidcat chunks: {filepath}: No such file", file=sys.stderr)
        return 1

    fmt_name = getattr(args, 'format', 'table')

    _vlog(args, f"[chunks] file={os.path.basename(filepath)} "
                f"size={os.path.getsize(filepath)}")

    # Get RIFF container info
    riff_info = get_riff_info(filepath)
    if riff_info is None:
        print(f"acidcat chunks: {filepath}: Not a RIFF file", file=sys.stderr)
        return 1

    # Walk raw chunks (offsets + sizes)
    chunk_list = []
    for cid, offset, size in iter_chunks(filepath):
        chunk_list.append({
            "chunk": cid,
            "offset": offset,
            "size": size,
        })
    _vlog(args, f"[chunks] walked {len(chunk_list)} chunks in "
                f"RIFF {riff_info['type']}")

    # Also get parsed fields
    results, meta, seen = parse_riff(filepath, enumerate_all=True)
    _vlog(args, f"[chunks] parsed {len(results)} fields from "
                f"{len(seen)} unique chunk types")

    if fmt_name == "table":
        stream = sys.stdout
        if getattr(args, 'output', None):
            stream = open(args.output, 'w')

        stream.write(f"RIFF container: {riff_info['size']} bytes, type={riff_info['type']}\n")
        stream.write(f"File: {os.path.basename(filepath)}\n\n")

        # Raw chunk layout
        stream.write("Chunk Layout:\n")
        for c in chunk_list:
            stream.write(f"  {c['chunk']:4s}  @ {c['offset']:>8d}  size={c['size']}\n")

        # Parsed fields
        if results:
            stream.write(f"\nParsed Fields:\n")
            for cid, key, val in results:
                stream.write(f"  {cid}.{key} = {val}\n")

        if stream is not sys.stdout:
            stream.close()
    else:
        # JSON or CSV: emit the parsed fields
        data = [{"chunk": cid, "key": key, "value": val} for cid, key, val in results]
        stream = sys.stdout
        if getattr(args, 'output', None):
            stream = open(args.output, 'w')
        output(data, fmt=fmt_name, stream=stream)
        if stream is not sys.stdout:
            stream.close()

    return 0
