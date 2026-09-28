"""acidcat lib -- the sample library index: build it, list it, search it.

    acidcat lib index DIR [--label L]          # index (or re-index) a library
    acidcat lib index --discover ROOT          # register every library under ROOT
    acidcat lib list [--orphans]               # the registered libraries
    acidcat lib stats [LIB] [--refresh]        # counts, from the index
    acidcat lib forget LIB [--delete-db]       # unregister (and delete the DB)
    acidcat lib query --bpm 120:130 --key Am   # search every library
    acidcat lib similar FILE --top 10          # nearest by audio features

`lib index` never stops part-way through a library: it has no --max-files.
"""

import sys

from acidcat.commands import _legacy
from acidcat.commands._output import add_output_format_arg, chosen_format

_KINDS = ("loop", "one_shot", "any")


def _registry(p):
    p.add_argument("--registry", help="The registry DB (default "
                                      "~/.acidcat/registry.db).")


def register(subparsers):
    p = subparsers.add_parser("lib", help="Build, list and search the sample "
                                          "library index.")
    sub = p.add_subparsers(dest="lib_command", metavar="SUBCOMMAND")

    ix = sub.add_parser("index", help="Index a directory as a library.")
    ix.add_argument("target", nargs="?", help="Directory to index.")
    ix.add_argument("--label", help="The library's label (default: the "
                                    "directory's name).")
    ix.add_argument("--in-tree", action="store_true",
                    help="Keep the DB in <DIR>/.acidcat/index.db.")
    ix.add_argument("--rebuild", action="store_true",
                    help="Delete the library's DB before indexing.")
    ix.add_argument("--reread", action="store_true",
                    help="Re-read files whose size and mtime are unchanged "
                         "(after a parser upgrade).")
    ix.add_argument("--features", action="store_true",
                    help="Extract librosa features while indexing.")
    ix.add_argument("--jobs", "-j", type=int, default=None,
                    help="Worker processes for features (default CPU-1).")
    ix.add_argument("--analyze", action="store_true",
                    help="Estimate BPM/key with librosa where the metadata has "
                         "none.")
    ix.add_argument("--import-tags", help="Import a legacy <name>_tags.json.")
    ix.add_argument("--discover", metavar="ROOT",
                    help="Register every qualifying subdirectory of ROOT as its "
                         "own library.")
    ix.add_argument("--min-samples", type=int, default=20,
                    help="--discover: audio files a subtree needs (default 20).")
    ix.add_argument("--max-depth", type=int, default=3,
                    help="--discover: levels to walk (default 3).")
    ix.add_argument("--label-prefix", default="",
                    help="--discover: prefix every derived label.")
    ix.add_argument("--dry-run", action="store_true",
                    help="--discover: say what would be registered.")
    ix.add_argument("-q", "--quiet", action="store_true")
    ix.add_argument("-v", "--verbose", action="store_true",
                    help="Diagnostic lines on stderr.")
    _registry(ix)

    ls = sub.add_parser("list", help="The registered libraries.")
    ls.add_argument("--orphans", action="store_true",
                    help="Only libraries whose DB file is missing.")
    _registry(ls)

    st = sub.add_parser("stats", help="A library's counts, from its index.")
    st.add_argument("target", nargs="?", metavar="LIB",
                    help="A library by label or path.")
    st.add_argument("--refresh", action="store_true",
                    help="Re-read the DB(s) and refresh the registry's counts "
                         "(every library without LIB).")
    _registry(st)

    fg = sub.add_parser("forget", help="Unregister a library.")
    fg.add_argument("target", metavar="LIB", help="A library by label or path.")
    fg.add_argument("--delete-db", action="store_true",
                    help="Also delete its DB file.")
    _registry(fg)

    q = sub.add_parser("query", help="Search every registered library.")
    _registry(q)
    for name, hlp in (("--bpm", "A tempo or range (120, 120:130)."),
                      ("--key", "A key (Am, F#)."),
                      ("--duration", "A duration range in seconds (0:2)."),
                      ("--device", "A device or plugin name."),
                      ("--category", "A category."),
                      ("--creator", "A creator."),
                      ("--product", "A product or pack."),
                      ("--text", "Free text over names and tags."),
                      ("--root", "Only files under this path."),
                      ("--compatible-with", "Files that mix with this one.")):
        q.add_argument(name, help=hlp)
    q.add_argument("--tag", action="append", default=[], help="A tag (repeatable).")
    q.add_argument("--only-format", metavar="FMT", help="Only this format.")
    q.add_argument("--bpm-tolerance", type=float, default=6.0,
                   help="--compatible-with: tempo tolerance in percent.")
    q.add_argument("--same-key", action="store_true",
                   help="--compatible-with: the same key only.")
    q.add_argument("--no-half-double", action="store_true",
                   help="--compatible-with: not half or double tempo.")
    q.add_argument("--kind", choices=_KINDS, help="loop, one_shot or any.")
    q.add_argument("--top", type=int, default=50, help="Keep the first N (50).")
    q.add_argument("--paths-only", action="store_true", help="Print paths only.")
    q.add_argument("-o", "--output", help="Write the rows here.")
    q.add_argument("-v", "--verbose", action="store_true",
                   help="Diagnostic lines on stderr.")
    add_output_format_arg(q, only=("table", "json", "csv", "tsv"), deprecated_f=False)

    sm = sub.add_parser("similar", help="The nearest files by audio features.")
    sm.add_argument("target", help="The file to match.")
    sm.add_argument("--top", type=int, default=5, help="Keep the first N (5).")
    sm.add_argument("--kind", choices=_KINDS, help="loop, one_shot or any.")
    sm.add_argument("--no-kind-filter", action="store_true",
                    help="Match across kinds.")
    sm.add_argument("--paths-only", action="store_true", help="Print paths only.")
    sm.add_argument("-o", "--output", help="Write the rows here.")
    _registry(sm)
    add_output_format_arg(sm, only=("table", "json", "csv", "tsv"), deprecated_f=False)

    p.set_defaults(func=run, _lib_parser=p)


def run(args):
    cmd = args.lib_command
    if cmd is None:
        args._lib_parser.print_help(sys.stderr)
        return 2
    return globals()["_" + cmd](args)


def _index_argv(args, extra):
    argv = list(extra)
    _legacy.flag(argv, "--registry", args.registry)
    return argv


def _index(args):
    from acidcat.commands import index
    if args.discover and args.target:
        print("acidcat lib index: give a directory or --discover ROOT, not both",
              file=sys.stderr)
        return 2
    argv = [args.target] if args.target else []
    _legacy.flag(argv, "--label", args.label)
    _legacy.switch(argv, "--in-tree", args.in_tree)
    _legacy.switch(argv, "--rebuild", args.rebuild)
    _legacy.switch(argv, "--force", args.reread)
    _legacy.switch(argv, "--features", args.features)
    _legacy.flag(argv, "--jobs", args.jobs)
    _legacy.switch(argv, "--deep", args.analyze)
    _legacy.flag(argv, "--import-tags", args.import_tags)
    if args.discover:
        argv += ["--discover", args.discover, "--min-samples", str(args.min_samples),
                 "--max-depth", str(args.max_depth)]
        if args.label_prefix:
            argv += ["--label-prefix", args.label_prefix]
        _legacy.switch(argv, "--dry-run", args.dry_run)
    _legacy.switch(argv, "-q", args.quiet)
    _legacy.switch(argv, "-v", args.verbose)
    return _legacy.run(index, "index", _index_argv(args, argv))


def _list(args):
    from acidcat.commands import index
    argv = ["--orphans" if args.orphans else "--list"]
    return _legacy.run(index, "index", _index_argv(args, argv))


def _stats(args):
    from acidcat.commands import index
    if args.refresh:
        argv = ["--refresh-stats"]
        _legacy.flag(argv, "--refresh-stats-target", args.target)
    else:
        if not args.target:
            print("acidcat lib stats: name a library (or --refresh for all)",
                  file=sys.stderr)
            return 2
        argv = ["--stats", args.target]
    return _legacy.run(index, "index", _index_argv(args, argv))


def _forget(args):
    from acidcat.commands import index
    argv = ["--remove" if args.delete_db else "--forget", args.target]
    return _legacy.run(index, "index", _index_argv(args, argv))


def _query(args):
    from acidcat.commands import query
    argv = ["--output-format", chosen_format(args), "--limit", str(args.top),
            "--bpm-tolerance", str(args.bpm_tolerance)]
    for name in ("registry", "bpm", "key", "duration", "device", "category",
                 "creator", "product", "text", "root", "compatible_with", "kind",
                 "output"):
        _legacy.flag(argv, "--" + name.replace("_", "-") if name != "output" else "-o",
                     getattr(args, name))
    _legacy.flag(argv, "--format", args.only_format)
    for t in args.tag:
        argv += ["--tag", t]
    for name in ("same_key", "no_half_double", "paths_only", "verbose"):
        _legacy.switch(argv, "--" + name.replace("_", "-"), getattr(args, name))
    return _legacy.run(query, "query", argv)


def _similar(args):
    from acidcat.commands import similar
    argv = [args.target, "--output-format", chosen_format(args), "-n", str(args.top)]
    _legacy.flag(argv, "--kind", args.kind)
    _legacy.switch(argv, "--no-kind-filter", args.no_kind_filter)
    _legacy.switch(argv, "--paths-only", args.paths_only)
    _legacy.flag(argv, "--registry", args.registry)
    _legacy.flag(argv, "-o", args.output)
    return _legacy.run(similar, "similar", argv)
