"""
acidcat dump -- hex-dump a specific RIFF chunk from a WAV file.

Default output is a human-friendly hex preview. `-f json` emits a machine
readable list that composes with jq and other tools.
"""

import binascii
import json
import os
import sys

from acidcat.core.riff import iter_chunks


def register(subparsers):
    p = subparsers.add_parser("dump", help="Hex-dump a specific chunk from a WAV file.")
    p.add_argument("target", help="Path to a WAV file.")
    p.add_argument("chunks", nargs="+",
                   help="Chunk IDs to dump (e.g. acid smpl LIST). Case-insensitive.")
    p.add_argument("-b", "--bytes", type=int, default=64, help="Hex preview length in bytes.")
    p.add_argument("-f", "--format", default="hex", choices=["hex", "json"],
                   help="Output format (default: hex). json emits full hex payloads.")
    p.add_argument("--write", help="Write raw chunk payloads to this directory.")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Diagnostic lines on stderr (wanted/found/skipped).")
    p.set_defaults(func=run)


def _vlog(args, msg):
    if getattr(args, "verbose", False) and not getattr(args, "quiet", False):
        print(msg, file=sys.stderr)


def run(args):
    filepath = args.target
    if not os.path.isfile(filepath):
        print(f"acidcat dump: {filepath}: No such file", file=sys.stderr)
        return 1

    # RIFF chunk IDs are always 4 bytes -- pad short names (e.g. "fmt" -> "fmt ")
    wanted = {c.upper().ljust(4)[:4] for c in args.chunks}
    preview_len = getattr(args, 'bytes', 64)
    outdir = getattr(args, 'write', None)
    fmt_name = getattr(args, 'format', 'hex')
    base = os.path.basename(filepath)

    _vlog(args, f"[dump] wanted={sorted(wanted)} preview={preview_len}B fmt={fmt_name}")

    if outdir:
        os.makedirs(outdir, exist_ok=True)

    collected = []
    found = False
    for cid, offset, size in iter_chunks(filepath):
        if cid.upper() not in wanted:
            continue
        found = True

        with open(filepath, "rb") as f:
            f.seek(offset + 8)  # skip chunk header
            payload = f.read(size)

        entry = {
            "chunk": cid,
            "offset": offset,
            "size": size,
        }

        if fmt_name == "hex":
            preview = binascii.hexlify(payload[:preview_len]).decode()
            hex_str = " ".join(preview[i:i + 2] for i in range(0, len(preview), 2))
            print(f"[{cid}] @ offset {offset}, {size} bytes")
            print(f"  {hex_str}")
            if outdir:
                outname = f"{os.path.splitext(base)[0]}_{cid}_{offset}.bin"
                outpath = os.path.join(outdir, outname)
                with open(outpath, "wb") as g:
                    g.write(payload)
                print(f"  -> {outpath}")
                _vlog(args, f"[dump] wrote {size}B to {outpath}")
            print()
        else:
            # json: include the full payload as a hex string so it survives
            # serialization cleanly. Truncation here would surprise callers.
            entry["hex"] = binascii.hexlify(payload).decode()
            if outdir:
                outname = f"{os.path.splitext(base)[0]}_{cid}_{offset}.bin"
                outpath = os.path.join(outdir, outname)
                with open(outpath, "wb") as g:
                    g.write(payload)
                entry["written_to"] = outpath
                _vlog(args, f"[dump] wrote {size}B to {outpath}")
            collected.append(entry)

    if not found:
        print(f"None of the requested chunks ({', '.join(args.chunks)}) found.", file=sys.stderr)
        return 1

    if fmt_name == "json":
        json.dump(collected, sys.stdout, indent=2)
        sys.stdout.write("\n")

    _vlog(args, f"[dump] matched {len(collected) if fmt_name == 'json' else 'N/A'} chunks")
    return 0
