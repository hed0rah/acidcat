"""
acidcat dump -- hex-dump a specific RIFF chunk from a WAV file.
"""

import binascii
import os
import sys

from acidcat.core.riff import iter_chunks


def register(subparsers):
    p = subparsers.add_parser("dump", help="Hex-dump a specific chunk from a WAV file.")
    p.add_argument("target", help="Path to a WAV file.")
    p.add_argument("chunks", nargs="+",
                   help="Chunk IDs to dump (e.g. acid smpl LIST). Case-insensitive.")
    p.add_argument("-b", "--bytes", type=int, default=64, help="Hex preview length in bytes.")
    p.add_argument("--write", help="Write raw chunk payloads to this directory.")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=run)


def run(args):
    filepath = args.target
    if not os.path.isfile(filepath):
        print(f"acidcat dump: {filepath}: No such file", file=sys.stderr)
        return 1

    # RIFF chunk IDs are always 4 bytes -- pad short names (e.g. "fmt" -> "fmt ")
    wanted = {c.upper().ljust(4)[:4] for c in args.chunks}
    preview_len = getattr(args, 'bytes', 64)
    outdir = getattr(args, 'write', None)
    base = os.path.basename(filepath)

    if outdir:
        os.makedirs(outdir, exist_ok=True)

    found = False
    for cid, offset, size in iter_chunks(filepath):
        if cid.upper() not in wanted:
            continue
        found = True

        with open(filepath, "rb") as f:
            f.seek(offset + 8)  # skip chunk header
            payload = f.read(size)

        preview = binascii.hexlify(payload[:preview_len]).decode()
        hex_str = " ".join(preview[i:i+2] for i in range(0, len(preview), 2))

        print(f"[{cid}] @ offset {offset}, {size} bytes")
        print(f"  {hex_str}")

        if outdir:
            outname = f"{os.path.splitext(base)[0]}_{cid}_{offset}.bin"
            outpath = os.path.join(outdir, outname)
            with open(outpath, "wb") as g:
                g.write(payload)
            print(f"  → {outpath}")
        print()

    if not found:
        print(f"None of the requested chunks ({', '.join(args.chunks)}) found.", file=sys.stderr)
        return 1

    return 0
