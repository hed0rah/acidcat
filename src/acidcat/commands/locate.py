"""acidcat locate -- find the regions of a blob that are audio.

The low-level primitive behind audio recovery: scan an unknown blob (a disk
image, a chip dump, a proprietary file that embeds samples) with two engines --
a signature sweep for known containers, and a statistical detector for
signatureless raw PCM (core/locate.py) -- and REPORT the regions it finds. It
never writes: locate reports, `carve` extracts.

    acidcat locate disk.img                         # a table of regions
    acidcat locate disk.img --analyze               # + inferred PCM geometry per blob
    acidcat locate doom.cdi --json | acidcat carve doom.cdi --batch -   # the pipeline
    dd if=/dev/sdcard | acidcat locate - --mode aggressive

A record's offset/length is exactly a `carve` range; the records go to stdout,
the summary to stderr, so `locate | carve` composes cleanly. `--analyze` adds the
inferred width / channels / endianness of each raw blob (sample rate is not in
the bytes -- reported null, with common candidates).
"""

import sys

from acidcat.core.forensics import audioscan
from acidcat.commands._output import add_output_format_arg, add_report_arg
from acidcat.core.forensics import locate as locatemod
from acidcat.core.infra.render import format_json
from acidcat.util.stdin import is_stdin_target

_PUBLIC_KEYS = ("kind", "format", "offset", "end", "length", "confidence",
                "streaming_extent", "corrupt_extent", "inspectable", "geometry",
                "frames", "stream_info", "transform")


def register(subparsers):
    p = subparsers.add_parser(
        "locate",
        help="Find audio regions in a blob or disk image (containers + raw PCM).")
    p.add_argument("input", metavar="FILE", help="File to scan, or '-' to read the blob from stdin.")
    p.add_argument("--mode", choices=locatemod.MODES, default="normal",
                   help="Forensics level: strict (validated containers only), "
                        "normal (+ high-confidence blobs), aggressive (every "
                        "candidate).")
    p.add_argument("--analyze", action="store_true",
                   help="Infer PCM geometry (width/channels/endian) of each raw "
                        "blob. Sample rate is not in the bytes; reported as null.")
    p.add_argument("--transforms", action="store_true",
                   help="Also hunt for audio hidden under a reversible transform "
                        "(XOR-byte / bit-rotate / nibble-swap) -- the CTF "
                        "obfuscation lens. The reported key is a candidate "
                        "(polarity/low-bits are ambiguous). Reads at most 16 MB.")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
    add_report_arg(p)
    p.add_argument("--min-confidence", type=float, default=0.0, metavar="C",
                   help="Only report regions at or above this confidence (0..1). "
                        "A signature-matched container is 0.90; a headerless "
                        "blob is inferred statistically and never exceeds 0.89, "
                        "so --min-confidence 0.90 means 'validated containers "
                        "only'. Filtering here keeps `locate | carve --batch` a "
                        "one-liner instead of needing jq in the middle.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Show the evidence behind each region (entropy, "
                        "autocorrelation, distribution) and any debug tells "
                        "(silence / DC-offset / clipping).")
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


def _public(rec, verbose=False):
    keys = _PUBLIC_KEYS + ("evidence",) if verbose else _PUBLIC_KEYS
    return {k: rec[k] for k in keys if k in rec}


def _geo_str(g):
    ch = "stereo" if g["channels"] == 2 else "mono"
    kind = f"float{g['width']}" if g.get("float") else f"{g['endian']}-{g['width']}bit"
    tells = [t for t, on in (("silence", g.get("silence")),
                             (f"dc {g.get('dc_offset')}", g.get("dc_offset")),
                             (f"clip {g.get('clipping')}", g.get("clipping"))) if on]
    s = f"{kind} {ch} @ ?Hz"
    if tells:
        s += "  [" + ", ".join(tells) + "]"
    return s


def _print_table(recs, verbose=False):
    if not recs:
        print("(no audio located)")
        return
    hasgeo = any("geometry" in r for r in recs)
    head = f"{'offset':>10}  {'end':>10}  {'kind':9}  {'format':7}  {'conf':>4}  {'length':>12}"
    if hasgeo:
        head += "  geometry"
    print(head)
    for r in recs:
        fmt = r.get("transform") or r["format"] or "raw-pcm"
        note = "  corrupt-extent" if r.get("corrupt_extent") else (
            "  approx-extent" if r.get("streaming_extent") else "")
        line = (f"0x{r['offset']:08x}  0x{r['end']:08x}  {r['kind']:9}  {fmt:7}  "
                f"{r['confidence']:.2f}  {r['length']:>12,}{note}")
        if r.get("geometry"):
            line += "  " + _geo_str(r["geometry"])
        print(line)
        if verbose:
            ev = r.get("evidence")
            if ev:
                ac = ev.get("autocorr", {})
                print(f"           evidence: entropy {ev['entropy']:.2f}  "
                      f"r1 {ac.get(1, 0):+.2f} r2 {ac.get(2, 0):+.2f} "
                      f"r8 {ac.get(8, 0):+.2f}  width {ev.get('width', '?')}")


def _print_tsv(recs):
    for r in recs:
        row = [f"0x{r['offset']:08x}", str(r["length"]), r["kind"],
               r.get("transform") or r["format"] or "raw-pcm", f"{r['confidence']:.2f}"]
        g = r.get("geometry")
        if g:
            row += [str(g["width"]), str(g["channels"]), g["endian"] or ""]
        sys.stdout.write("\t".join(row) + "\n")


def run(args):
    try:
        data = _read(args.input)
    except OSError as e:
        print(f"acidcat locate: {args.input}: {e}", file=sys.stderr)
        return 2
    if not data:
        print("acidcat locate: no input bytes", file=sys.stderr)
        return 2

    # Only the signature sweep is unbounded. The statistical pass and the frame
    # scan each cap out, so on a large image they cover a prefix while containers
    # are found everywhere -- and nothing on screen would distinguish "no raw
    # audio there" from "never looked". Name the engines that stopped short
    # rather than making a blanket claim about coverage.
    from acidcat.core.forensics.audioscan import DEFAULT_READ_CAP
    from acidcat.core.forensics.framescan import _READ_CAP as FRAME_CAP
    from acidcat.core.forensics.transforms import _READ_CAP as XFORM_CAP
    limited = []
    if args.mode != "strict" and len(data) > DEFAULT_READ_CAP:
        limited.append(("raw-audio scan", DEFAULT_READ_CAP))
    if len(data) > FRAME_CAP:
        limited.append(("stream scan", FRAME_CAP))
    # the transform hunt caps at 16 MB -- much lower than the others -- and used
    # to report "0 transformed" for a whole image with no note at all
    if args.transforms and len(data) > XFORM_CAP:
        limited.append(("transform scan", XFORM_CAP))
    for label, cap in limited:
        print(f"acidcat locate: {label} covers the first "
              f"{cap // (1024 * 1024)} MB of "
              f"{len(data) / (1024 * 1024):.0f} MB; container signatures are "
              f"found throughout", file=sys.stderr)

    recs = locatemod.locate(data, mode=args.mode)
    if args.transforms:
        from acidcat.core.forensics import transforms
        recs = sorted(recs + transforms.find_transformed_audio(data),
                      key=lambda r: r["offset"])
    if args.analyze:
        _analyze(data, recs)

    floor = getattr(args, "min_confidence", 0.0) or 0.0
    if floor > 0:
        kept = [r for r in recs if r["confidence"] >= floor]
        dropped = len(recs) - len(kept)
        recs = kept
        # say what was withheld: a filtered "nothing found" and a genuine one
        # must not look the same
        if dropped and not args.quiet:
            print(f"acidcat locate: {dropped} region(s) below confidence "
                  f"{floor:g} not reported", file=sys.stderr)

    if args.output_format == "json":
        format_json([_public(r, args.verbose) for r in recs], sys.stdout)
    elif args.output_format == "tsv":
        _print_tsv(recs)                     # historical layout, no header
    elif args.output_format == "csv":
        from acidcat.core.infra.render import output as _render
        _render([_public(r, args.verbose) for r in recs], fmt="csv")
    else:
        _print_table(recs, args.verbose)

    if not args.quiet:
        nc = sum(1 for r in recs if r["kind"] == "container")
        ns = sum(1 for r in recs if r["kind"] == "stream")
        nt = sum(1 for r in recs if r["kind"] == "transformed")
        tail = f", {nt} transformed" if nt else ""
        print(f"located {len(recs)} region(s): {nc} container(s), {ns} stream(s), "
              f"{len(recs) - nc - ns - nt} blob(s){tail} [{args.mode}]", file=sys.stderr)
    # `locate | carve` on a blob with no audio in it exited 0 all the way
    # through, so a recovery script branching on $? believed it had succeeded
    # and carried on with an empty output directory. 1 is the grep convention
    # for ran-fine-nothing-matched, and `probe scan`, `probe find`, `dump` and
    # `extract` in this same tool already use it.
    return 0 if recs else 1
