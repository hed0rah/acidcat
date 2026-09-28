"""acidcat check -- are a file's derived fields consistent, and can they be fixed?

    acidcat check FILE...                 # what `validate` did
    acidcat check FILE... --fix           # what `repair` did
    acidcat check DIR --problems-only     # only the files with issues

Exit 0 when every checked file is consistent, 1 when one is not (or --fix
found something it would not fix), 2 when nothing could be checked.
"""

from acidcat.commands import _legacy
from acidcat.commands._output import add_output_format_arg, chosen_format


def register(subparsers):
    p = subparsers.add_parser(
        "check", help="Check derived fields (sizes, counts, rates) against the "
                      "data; --fix rewrites them.")
    p.add_argument("inputs", nargs="+", metavar="target",
                   help="Files or directories to check.")
    p.add_argument("--fix", action="store_true",
                   help="Rewrite what is inconsistent (with a _original backup "
                        "unless --overwrite).")
    p.add_argument("--deep", action="store_true",
                   help="Also verify the checksums a format carries (FLAC MD5, "
                        "frame CRCs): the decoding work, so slower.")
    p.add_argument("--problems-only", action="store_true",
                   help="Report only the files with issues.")
    p.add_argument("-o", "--output",
                   help="With --fix: write the repaired copy here (single input).")
    p.add_argument("--dry-run", action="store_true",
                   help="With --fix: say what would change and write nothing.")
    p.add_argument("--overwrite", action="store_true",
                   help="With --fix: skip the _original backup.")
    p.add_argument("--keep-pad", action="store_true",
                   help="With --fix: keep a RIFF pad byte the size field counts.")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"), deprecated_f=False)
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Drop the summary line on stderr (never changes stdout).")
    p.set_defaults(func=run)


def run(args):
    fmt = chosen_format(args)
    if args.fix:
        from acidcat.commands import repair
        argv = list(args.inputs) + ["--output-format", fmt]
        _legacy.flag(argv, "-o", args.output)
        _legacy.switch(argv, "--dry-run", args.dry_run)
        _legacy.switch(argv, "--overwrite", args.overwrite)
        _legacy.switch(argv, "--keep-pad", args.keep_pad)
        return _legacy.run(repair, "repair", argv)
    from acidcat.commands import validate
    argv = list(args.inputs) + ["--output-format", fmt]
    _legacy.switch(argv, "--deep", args.deep)
    _legacy.switch(argv, "-q", args.problems_only)
    ns = _legacy.parser_for(validate, "validate").parse_args(argv)
    ns.hush = args.quiet
    return validate.run(ns)
