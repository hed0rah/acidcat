"""Forensic audio recovery -- "PhotoRec for audio".

Locate audio in an unknown blob (a raw disk image, a chip dump, a carved
fragment) by two engines: a signature sweep for known containers, and a
statistical detector for signatureless raw PCM (core/recover.py). Reports the
recoveries as a table or `--json`; `--extract DIR` writes each one out, fusing
the recover -> carve step so a run is usable on its own.

Reads a file or `-` (stdin), so acidcat sits in a pipe:

    dd if=/dev/sdcard | acidcat recover - --mode aggressive
    acidcat recover disk.img --json | jq '.[] | select(.kind=="blob")'
    acidcat recover disk.img --extract out/     # then: acidcat convert out/*.8svx

The record offset/length is exactly a `carve` range and a recovered container is
exactly an `inspect`/`convert` input -- recover locates, the other verbs act.
"""

import json
import os
import sys

from acidcat.core import recover as recovermod
from acidcat.util.stdin import is_stdin_target

# file extension for an extracted recovery, by detected format (blobs -> .raw)
_EXT = {"wav": "wav", "rf64": "wav", "aiff": "aiff", "aifc": "aiff",
        "8svx": "8svx", "flac": "flac", "ogg": "ogg", "sf2": "sf2"}

_PUBLIC_KEYS = ("kind", "format", "offset", "end", "length", "confidence",
                "streaming_extent", "corrupt_extent", "inspectable", "evidence")


def register(subparsers):
    p = subparsers.add_parser(
        "recover",
        help='Recover audio from a blob or disk image ("PhotoRec for audio").',
    )
    p.add_argument("input", help="File to scan, or '-' to read the blob from stdin.")
    p.add_argument("--mode", choices=recovermod.MODES, default="normal",
                   help="Forensics level: strict (validated containers only), "
                        "normal (+ high-confidence blobs), aggressive (every "
                        "candidate -- best-effort on raw/unknown/corrupt).")
    p.add_argument("--json", action="store_true",
                   help="Emit the recovery records as JSON on stdout.")
    p.add_argument("-x", "--extract", metavar="DIR",
                   help="Write each recovery's bytes into DIR (recover + carve).")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress the summary line (kept on stderr otherwise).")
    p.set_defaults(func=run)


def _read(path):
    if is_stdin_target(path):
        return sys.stdin.buffer.read()
    with open(path, "rb") as f:
        return f.read()


def _public(rec):
    return {k: rec[k] for k in _PUBLIC_KEYS if k in rec}


def _print_table(recs):
    if not recs:
        print("(no audio recovered)")
        return
    print(f"{'offset':>10}  {'end':>10}  {'kind':9}  {'format':7}  "
          f"{'conf':>4}  {'length':>12}")
    for r in recs:
        fmt = r["format"] or "raw-pcm"
        note = ""
        if r.get("corrupt_extent"):
            note = "  corrupt-extent"
        elif r.get("streaming_extent"):
            note = "  approx-extent"
        print(f"0x{r['offset']:08x}  0x{r['end']:08x}  {r['kind']:9}  {fmt:7}  "
              f"{r['confidence']:.2f}  {r['length']:>12,}{note}")


def _extract(data, recs, outdir):
    os.makedirs(outdir, exist_ok=True)
    written = 0
    for i, r in enumerate(recs):
        ext = _EXT.get(r["format"], "raw")
        name = f"{i:04d}_0x{r['offset']:08x}_{r['kind']}.{ext}"
        with open(os.path.join(outdir, name), "wb") as f:
            f.write(data[r["offset"]:r["end"]])
        written += 1
    print(f"extracted {written} recovery file(s) -> {outdir}", file=sys.stderr)


def run(args):
    try:
        data = _read(args.input)
    except OSError as e:
        print(f"acidcat recover: {args.input}: {e}", file=sys.stderr)
        return 1
    if not data:
        print("acidcat recover: no input bytes", file=sys.stderr)
        return 1

    recs = recovermod.recover(data, mode=args.mode)

    if args.json:
        json.dump([_public(r) for r in recs], sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_table(recs)

    if args.extract:
        _extract(data, recs, args.extract)

    if not args.quiet:
        nc = sum(1 for r in recs if r["kind"] == "container")
        print(f"recovered {len(recs)} region(s): {nc} container(s), "
              f"{len(recs) - nc} blob(s) [{args.mode}]", file=sys.stderr)
    return 0
