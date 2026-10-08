"""acidcat analyze -- listen to the audio: tempo and key, or ML features.

    acidcat analyze --bpm-key FILE|DIR...
    acidcat analyze --features FILE|DIR...
    acidcat analyze --bpm-key --features FILE      # both, one after the other

Needs the `analysis` extra (librosa); without it, exit 2. Unlike `stats`,
there is no default --max-files: an analysis runs over what it is given.
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
    p.add_argument("--max-files", type=int, default=None, metavar="N",
                   help="Stop after N files (no limit by default).")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"), deprecated_f=False)
    p.add_argument("-o", "--output", help="Write the rows here.")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Drop progress lines on stderr.")
    p.set_defaults(func=run)


def run(args):
    if not (args.bpm_key or args.features):
        print("acidcat analyze: say what to analyze: --bpm-key, --features, or "
              "both", file=sys.stderr)
        return 2
    import importlib.util
    if importlib.util.find_spec("librosa") is None:
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
    text = _merge(parts, fmt)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return rc


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
