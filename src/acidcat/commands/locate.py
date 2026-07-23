"""acidcat locate -- find the regions of a blob that are audio.

The low-level primitive behind "PhotoRec for audio": scan an unknown blob (a disk
image, a chip dump, a proprietary file that embeds samples) with two engines --
a signature sweep for known containers, and a statistical detector for
signatureless raw PCM (core/locate.py) -- and REPORT the regions it finds. It
never writes: locate reports, `carve` extracts.

    acidcat locate disk.img                         # a table of regions
    acidcat locate disk.img --analyze               # + inferred PCM geometry per blob
    acidcat locate doom.cdi -f json | acidcat carve doom.cdi --batch -   # the pipeline
    dd if=/dev/sdcard | acidcat locate - --mode aggressive

A record's offset/length is exactly a `carve` range; the records go to stdout,
the summary to stderr, so `locate | carve` composes cleanly. `--analyze` adds the
inferred width / channels / endianness of each raw blob (sample rate is not in
the bytes -- reported null, with common candidates).
"""

import json
import sys

from acidcat.core import audioscan
from acidcat.core import locate as locatemod
from acidcat.util.stdin import is_stdin_target

_PUBLIC_KEYS = ("kind", "format", "offset", "end", "length", "confidence",
                "streaming_extent", "corrupt_extent", "inspectable", "geometry")


def register(subparsers):
    p = subparsers.add_parser(
        "locate",
        help='Find audio regions in a blob or disk image ("PhotoRec for audio").')
    p.add_argument("input", help="File to scan, or '-' to read the blob from stdin.")
    p.add_argument("--mode", choices=locatemod.MODES, default="normal",
                   help="Forensics level: strict (validated containers only), "
                        "normal (+ high-confidence blobs), aggressive (every "
                        "candidate).")
    p.add_argument("--analyze", action="store_true",
                   help="Infer PCM geometry (width/channels/endian) of each raw "
                        "blob. Sample rate is not in the bytes; reported as null.")
    p.add_argument("-f", "--format", choices=("table", "json", "tsv"),
                   default="table", help="Output shape (default: table).")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress the summary line (kept on stderr otherwise).")
    p.set_defaults(func=run)


def _read(path):
    if is_stdin_target(path):
        return sys.stdin.buffer.read()
    with open(path, "rb") as f:
        return f.read()


def _analyze(data, recs):
    for r in recs:
        if r["kind"] == "blob":
            r["geometry"] = audioscan.analyze_geometry(
                data[r["offset"]:min(r["end"], r["offset"] + 16384)])


def _public(rec):
    return {k: rec[k] for k in _PUBLIC_KEYS if k in rec}


def _print_table(recs):
    if not recs:
        print("(no audio located)")
        return
    hasgeo = any("geometry" in r for r in recs)
    head = f"{'offset':>10}  {'end':>10}  {'kind':9}  {'format':7}  {'conf':>4}  {'length':>12}"
    if hasgeo:
        head += "  geometry"
    print(head)
    for r in recs:
        fmt = r["format"] or "raw-pcm"
        note = "  corrupt-extent" if r.get("corrupt_extent") else (
            "  approx-extent" if r.get("streaming_extent") else "")
        line = (f"0x{r['offset']:08x}  0x{r['end']:08x}  {r['kind']:9}  {fmt:7}  "
                f"{r['confidence']:.2f}  {r['length']:>12,}{note}")
        g = r.get("geometry")
        if g:
            ch = "stereo" if g["channels"] == 2 else "mono"
            en = f"{g['endian']}-" if g["endian"] else ""
            line += f"  {en}{g['width']}bit {ch} @ ?Hz"
        print(line)


def _print_tsv(recs):
    for r in recs:
        row = [f"0x{r['offset']:08x}", str(r["length"]), r["kind"],
               r["format"] or "raw-pcm", f"{r['confidence']:.2f}"]
        g = r.get("geometry")
        if g:
            row += [str(g["width"]), str(g["channels"]), g["endian"] or ""]
        sys.stdout.write("\t".join(row) + "\n")


def run(args):
    try:
        data = _read(args.input)
    except OSError as e:
        print(f"acidcat locate: {args.input}: {e}", file=sys.stderr)
        return 1
    if not data:
        print("acidcat locate: no input bytes", file=sys.stderr)
        return 1

    recs = locatemod.locate(data, mode=args.mode)
    if args.analyze:
        _analyze(data, recs)

    if args.format == "json":
        json.dump([_public(r) for r in recs], sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif args.format == "tsv":
        _print_tsv(recs)
    else:
        _print_table(recs)

    if not args.quiet:
        nc = sum(1 for r in recs if r["kind"] == "container")
        print(f"located {len(recs)} region(s): {nc} container(s), "
              f"{len(recs) - nc} blob(s) [{args.mode}]", file=sys.stderr)
    return 0
