"""acidcat stats -- one line per file across a tree, or a count over it.

    acidcat stats DIR                     # --by meta: format, tempo, key, duration
    acidcat stats DIR... --by shape       # format, structure summary, chunk ids
    acidcat stats DIR... --by chunks      # chunk ids over the tree: files, occurrences
    acidcat stats DIR --max-files 0       # every file, however many

`--max-files` stops after that many files, 10,000 by default so a mistyped
`/` does not walk a disk. Stopping there is coverage, not failure: one line on
stderr names the flag and the run exits 0. `--max-files 0` means no limit.
"""

import sys

from acidcat.commands import _legacy
from acidcat.commands._output import add_output_format_arg, chosen_format

DEFAULT_MAX_FILES = 10000
_BY = ("meta", "shape", "chunks")


def cap_note(n):
    """The one coverage line a run that stopped at --max-files prints."""
    print(f"acidcat stats: stopped at --max-files {n:,}; more files may remain "
          f"(--max-files 0 reads them all)", file=sys.stderr)


def register(subparsers):
    p = subparsers.add_parser(
        "stats", help="Per-file rows (--by meta, shape) or counts (--by chunks) "
                      "over a tree.")
    p.add_argument("targets", nargs="+", metavar="FILE",
                   help="Directories or files ('-' is stdin for --by shape).")
    p.add_argument("--by", choices=_BY, default="meta",
                   help="meta: format, tempo, key and duration per file "
                        "(default); shape: the structure summary per file; "
                        "chunks: chunk-id counts over the tree.")
    p.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, metavar="N",
                   help=f"Stop after N files (default {DEFAULT_MAX_FILES:,}; "
                        f"0 = no limit).")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"), deprecated_f=False)
    p.add_argument("-o", "--output", help="Write the rows here (--by meta).")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Drop progress and summary lines on stderr.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Diagnostic lines on stderr.")
    p.add_argument("--has", metavar="IDS",
                   help="--by meta: only WAV files holding any of these chunk "
                        "ids; --by chunks: count only files holding all of them.")
    p.add_argument("--examples", type=int, default=None, metavar="N",
                   help="--by chunks: example paths to keep per chunk id "
                        "(default 5).")
    p.add_argument("--top", type=int, default=60, metavar="N",
                   help="--by chunks: the N commonest ids in the histogram "
                        "(default 60).")
    # the census engine's reader: it serves --by chunks
    p.add_argument("--jobs", default="auto",
                   help="Reader threads: N, or 'auto' (1 on a spinning disk, "
                        "CPU*4 on SSD). The census engine (--by chunks).")
    p.add_argument("--io-hint", default="auto", choices=("auto", "ssd", "hdd"),
                   help="Storage kind for the default --jobs.")
    p.add_argument("--follow-symlinks", action="store_true",
                   help="Follow directory symlinks (loop-safe).")
    p.add_argument("--one-file-system", action="store_true",
                   help="Do not cross into other mounted filesystems.")
    p.add_argument("--noatime", action="store_true",
                   help="Open with O_NOATIME where permitted (Linux).")
    p.add_argument("--no-fadvise", action="store_true",
                   help="Do not hint the kernel to drop scanned pages.")
    p.add_argument("--only-format", metavar="FMT",
                   help="--by shape: only files whose format matches FMT.")
    p.add_argument("--no-path", action="store_true",
                   help="--by shape: leave the path column out.")
    p.add_argument("--coarse", action="store_true",
                   help="--by shape: drop the per-file summary, so like files "
                        "group.")
    p.add_argument("--fast", action="store_true",
                   help="--by shape: fingerprint from the header only.")
    p.add_argument("--anomalies", action="store_true",
                   help="--by shape: fold the forensic scan into the flag column.")
    p.add_argument("--warn-only", action="store_true",
                   help="--by shape: only files with a warning.")
    p.set_defaults(func=run)


def _limit(n):
    return n if n and n > 0 else None


# census's reader and report; --by meta and shape read files another way
_CHUNKS_ONLY = (("jobs", "auto"), ("io_hint", "auto"), ("follow_symlinks", False),
                ("one_file_system", False), ("noatime", False),
                ("no_fadvise", False), ("examples", None), ("top", 60))


# the flags each --by takes beyond the shared ones; one given to a mode that
# does not read it is refused, not ignored (review R9)
_SHAPE_ONLY = (("only_format", None), ("no_path", False), ("coarse", False),
               ("fast", False), ("anomalies", False), ("warn_only", False))
_META_ONLY = (("has", None),)
_FOR = {"meta": _META_ONLY, "shape": _SHAPE_ONLY,
        "chunks": _CHUNKS_ONLY + (("has", None),)}


def _wrong_flags(args):
    """The flags given that the chosen --by does not take."""
    own = {k for k, _off in _FOR[args.by]}
    every = _CHUNKS_ONLY + _SHAPE_ONLY + _META_ONLY
    return sorted({"--" + k.replace("_", "-") for k, off in every
                   if k not in own and getattr(args, k, off) != off})


def run(args):
    fmt = chosen_format(args)
    if args.max_files is not None and args.max_files < 0:
        print("acidcat stats: --max-files must be 0 (no limit) or more",
              file=sys.stderr)
        return 2
    wrong = _wrong_flags(args)
    if wrong:
        print(f"acidcat stats: {', '.join(wrong)}: not for --by {args.by}",
              file=sys.stderr)
        return 2
    if args.by == "meta":
        from acidcat.commands import scan
        cap = _limit(args.max_files)
        argv = [args.targets[0], "--output-format", fmt,
                "-n", str(cap if cap else 10 ** 18)]
        _legacy.flag(argv, "-o", args.output)
        _legacy.switch(argv, "-q", args.quiet)
        _legacy.switch(argv, "-v", args.verbose)
        _legacy.flag(argv, "--has", args.has)
        ns = _legacy.parser_for(scan, "scan").parse_args(argv)
        ns.targets = list(args.targets)      # files and directories, any number
        ns.cap_flag = "--max-files" if cap else None
        return scan.run(ns)
    if args.by == "shape":
        from acidcat.commands import shape
        argv = list(args.targets) + ["--output-format", fmt]
        _legacy.flag(argv, "--format", args.only_format)
        for name in ("no_path", "coarse", "fast", "anomalies", "warn_only"):
            _legacy.switch(argv, "--" + name.replace("_", "-"), getattr(args, name))
        ns = _legacy.parser_for(shape, "shape").parse_args(argv)
        ns.max_files = _limit(args.max_files)
        return shape.run(ns)
    from acidcat.commands import census
    cap = _limit(args.max_files)
    argv = list(args.targets) + ["--output-format", fmt, "--top", str(args.top),
                                 "--jobs", str(args.jobs), "--io-hint", args.io_hint]
    _legacy.flag(argv, "--limit", cap)
    _legacy.flag(argv, "-o", args.output)
    _legacy.flag(argv, "--has", args.has)
    _legacy.flag(argv, "--examples", args.examples)
    for name in ("follow_symlinks", "one_file_system", "noatime", "no_fadvise"):
        _legacy.switch(argv, "--" + name.replace("_", "-"), getattr(args, name))
    _legacy.switch(argv, "-q", args.quiet)
    ns = _legacy.parser_for(census, "census").parse_args(argv)
    ns.cap_flag = "--max-files" if cap else None
    return census.run(ns)
