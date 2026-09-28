"""acidcat analyze -- listen to the audio: tempo and key, or ML features.

    acidcat analyze --bpm-key FILE|DIR
    acidcat analyze --features FILE|DIR
    acidcat analyze --bpm-key --features FILE      # both, one after the other

Needs the `analysis` extra (librosa); without it, exit 2. Unlike `stats`,
there is no default --max-files: an analysis runs over what it is given.
"""

import sys

from acidcat.commands import _legacy
from acidcat.commands._output import add_output_format_arg, chosen_format


def register(subparsers):
    p = subparsers.add_parser(
        "analyze", help="Estimate tempo and key, or extract ML features, from "
                        "the audio (needs the analysis extra).")
    p.add_argument("target", help="A file or a directory.")
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
    n = args.max_files if args.max_files and args.max_files > 0 else 10 ** 18
    rc = 0
    for want, module_name in ((args.bpm_key, "detect"), (args.features, "features")):
        if not want:
            continue
        from importlib import import_module
        module = import_module("acidcat.commands." + module_name)
        argv = [args.target, "--output-format", chosen_format(args), "-n", str(n)]
        _legacy.flag(argv, "-o", args.output)
        _legacy.switch(argv, "-q", args.quiet)
        rc = max(rc, _legacy.run(module, module_name, argv) or 0)
    return rc
