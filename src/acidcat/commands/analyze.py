"""acidcat analyze -- listen to the audio: tempo and key, or ML features.

    acidcat analyze --bpm-key FILE|DIR...
    acidcat analyze --features FILE|DIR...
    acidcat analyze --bpm-key --features FILE      # both, one after the other
    acidcat analyze --pitch FILE|DIR...            # the note a one-shot plays

Needs the `analysis` extra (librosa; --pitch needs only numpy); without it,
exit 2. Unlike `stats`, there is no default --max-files: an analysis runs over
what it is given.
"""

import contextlib
import io
import json
import sys

from acidcat.commands import _legacy
from acidcat.commands._output import add_output_format_arg, chosen_format


def register(subparsers):
    p = subparsers.add_parser(
        "analyze", help="Estimate tempo and key, or extract ML features, from "
                        "the audio (needs the analysis extra).")
    p.add_argument("targets", nargs="+", metavar="FILE",
                   help="Files or directories.")
    p.add_argument("--bpm-key", action="store_true",
                   help="Estimate tempo and key (what `detect` did).")
    p.add_argument("--features", action="store_true",
                   help="Extract the feature vector (what `features` did).")
    p.add_argument("--pitch", action="store_true",
                   help="The note a one-shot settles on, its offset in cents, "
                        "and how clearly it has one pitch.")
    p.add_argument("--profile", action="store_true",
                   help="The envelope (hit, decay, sustain, pulse), attack "
                        "and decay times, the energy in each band, and how "
                        "wet it is (reverb or room, 0 to 1).")
    p.add_argument("--max-files", type=int, default=None, metavar="N",
                   help="Stop after N files (no limit by default).")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"), deprecated_f=False)
    p.add_argument("-o", "--output", help="Write the rows here.")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Drop progress lines on stderr.")
    p.set_defaults(func=run)


def run(args):
    if not (args.bpm_key or args.features or args.pitch or args.profile):
        print("acidcat analyze: say what to analyze: --bpm-key, --features, "
              "--pitch, --profile, or several", file=sys.stderr)
        return 2
    import importlib.util
    if (args.bpm_key or args.features) and importlib.util.find_spec("librosa") is None:
        # could not run (2), said once, not a row of Nones per file
        print("acidcat analyze: needs the analysis extra (librosa): "
              "pip install acidcat[analysis]", file=sys.stderr)
        return 2
    n = args.max_files if args.max_files and args.max_files > 0 else 10 ** 18
    fmt = chosen_format(args)
    targets = list(getattr(args, "targets", None) or [args.target])
    rc, parts = 0, []
    for want, module_name in ((args.bpm_key, "detect"), (args.features, "features")):
        if not want:
            continue
        from importlib import import_module
        module = import_module("acidcat.commands." + module_name)
        for target in targets:
            argv = [target, "--output-format", fmt, "-n", str(n)]
            _legacy.switch(argv, "-q", args.quiet)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = max(rc, _legacy.run(module, module_name, argv) or 0)
            parts.append(out.getvalue())
    if args.pitch or args.profile:
        from acidcat.util.deps import require
        if not require("numpy", group="analysis"):
            return 2
        rc = max(rc, _sound(targets, n, fmt, args.quiet, parts,
                            pitch=args.pitch, profile=args.profile))
    text = _merge(parts, fmt)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return rc


_PROFILE_FIELDS = ("envelope", "attack_ms", "decay_ms", "sustain", "reattacks",
                 "centroid_hz", "band", "wetness")


def _sound(targets, n, fmt, quiet, parts, pitch=False, profile=False):
    """--pitch and --profile: one row per file with the columns asked for
    (core/analysis/pitch.py, core/analysis/profile.py)."""
    from acidcat.core.analysis.pitch import estimate_pitch
    from acidcat.core.analysis.profile import BAND_NAMES, estimate_profile
    from acidcat.core.infra.render import output
    from acidcat.util import targets as tg
    files, skipped = tg.expand(targets)
    rows, failed = [], 0
    for path in files[:n]:
        row = {"path": path}
        if pitch:
            row.update(note=None, cents=None, f0_hz=None, tunability=None,
                       glide_cents=None)
        if profile:
            row.update({k: None for k in _PROFILE_FIELDS})
            row.update({"band_" + b: None for b in BAND_NAMES})
        try:
            est = estimate_pitch(path) if pitch else None
            d = estimate_profile(path) if profile else None
        except Exception as e:
            failed += 1
            if not quiet:
                print(f"acidcat analyze: {path}: could not decode the audio "
                      f"({e.__class__.__name__})", file=sys.stderr)
        else:
            if est is not None:
                row.update(note=est["note_name"], cents=est["cents"],
                           f0_hz=est["f0_hz"], tunability=est["tunability"],
                           glide_cents=est["glide_cents"])
            if d is not None:
                row.update({k: d[k] for k in _PROFILE_FIELDS})
                row.update({"band_" + b: v for b, v in d["bands"].items()})
        rows.append(row)
    if not quiet:
        note = tg.skip_note(skipped)
        if note:
            print(f"  {note}", file=sys.stderr)
    out = io.StringIO()
    if len(rows) == 1 and fmt == "table":
        output(rows[0], fmt="table", stream=out)
    else:
        output(rows, fmt="csv" if fmt == "table" else fmt, stream=out)
    parts.append(out.getvalue())
    return 2 if rows and failed == len(rows) else 0


def _merge(parts, fmt):
    """Several targets' output as one: one JSON array of every row
    (cli-2.0.md section 4.1), one csv/tsv header, table blocks in turn."""
    parts = [p for p in parts if p.strip()]
    if fmt == "json":
        rows = []
        for p in parts:
            got = json.loads(p)
            rows += got if isinstance(got, list) else [got]
        return json.dumps(rows, indent=2) + "\n"
    if fmt in ("csv", "tsv") and parts:
        head, *_ = parts[0].splitlines(keepends=True)
        body = [l for p in parts for l in p.splitlines(keepends=True)[1:]]
        return head + "".join(body)
    return "\n".join(p.rstrip("\n") for p in parts) + ("\n" if parts else "")
