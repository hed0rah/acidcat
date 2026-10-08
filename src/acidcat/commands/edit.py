"""acidcat edit -- change what a file says: tags, typed fields, the cover.

    acidcat edit FILE... --set title="Kick" --set bpm=128
    acidcat edit FILE --set RIFF/fmt_#sample_rate=48000 --set ... --force
    acidcat edit FILE --set cover=@art.png          # embed the front cover
    acidcat edit FILE --unset cover                 # remove it
    acidcat edit FILE --get cover -o art.jpg        # write it out
    acidcat edit FILE... --strip                    # remove identifying metadata

A NAME is a tag of the file's metadata profile (the names `inspect --tags`
shows), `cover`, or an ADDR naming a typed field (`NODE#KEY`, `@OFF+LEN`).
Every edit is a verified Patch (acidcat.core.edit): it is read back from the
new bytes and the file re-walked before anything is written, and the original
is kept as `<name>_original` unless --overwrite.
"""

import sys

from acidcat.commands import _legacy
from acidcat.commands._output import add_output_format_arg, chosen_format


def register(subparsers):
    p = subparsers.add_parser(
        "edit", help="Change tags, typed fields or the cover (verified, with a "
                     "backup).")
    p.add_argument("inputs", nargs="+", metavar="FILE", help="File(s) to edit.")
    p.add_argument("--set", dest="sets", action="append", default=[],
                   metavar="NAME=VALUE",
                   help="Set a tag, a typed field (an ADDR) or the cover "
                        "(cover=@IMAGE). Repeatable; an empty VALUE clears.")
    p.add_argument("--unset", dest="unsets", action="append", default=[],
                   metavar="NAME", help="Clear a tag, or remove the cover.")
    p.add_argument("--get", metavar="NAME",
                   help="Read something out instead of editing: `cover` (with "
                        "-o PATH to write the image).")
    p.add_argument("-o", "--output",
                   help="Write the edited copy here (single input), or with "
                        "--get, the thing read out.")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would change and write nothing.")
    p.add_argument("--overwrite", action="store_true",
                   help="Skip the _original backup on in-place edits.")
    p.add_argument("--force", action="store_true",
                   help="Write a typed field whose type the walk only inferred.")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Nothing on stderr but errors (no notes).")
    p.add_argument("--no-cascade", action="store_false", dest="cascade",
                   help="Do not set the fields a field edit ties to (a WAV's "
                        "avg_bytes_per_sec after its sample_rate): refuse the "
                        "edit instead, as a new inconsistency.")
    p.add_argument("--strip", action="store_true",
                   help="Remove identifying metadata (tags/bext/iXML/ID3/...); "
                        "keeps audio and functional chunks.")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"), deprecated_f=False)
    p.set_defaults(func=run)


def _cover_only(args):
    """The cover edit this call is, or None: `get`, `set` with an image path,
    or `remove`."""
    sets = [s for s in args.sets if s.split("=", 1)[0].strip() == "cover"]
    others = [s for s in args.sets if s not in sets]
    unset_cover = "cover" in args.unsets
    other_unsets = [u for u in args.unsets if u != "cover"]
    if args.get is not None:
        return ("get", None) if not (args.sets or args.unsets) else ("bad", None)
    if others or other_unsets or args.strip:
        return None if not (sets or unset_cover) else ("bad", None)
    if sets and not unset_cover and len(sets) == 1:
        value = sets[0].split("=", 1)[1] if "=" in sets[0] else ""
        if not value.startswith("@"):
            return ("bad-value", value)
        return ("set", value[1:])
    if unset_cover and not sets:
        return ("remove", None)
    return None


def run(args):
    if args.get is not None and args.get != "cover":
        print(f"acidcat edit: --get {args.get}: only `cover` can be read out",
              file=sys.stderr)
        return 2
    cover = _cover_only(args)
    if cover is not None:
        kind, value = cover
        if kind == "bad":
            print("acidcat edit: give the cover on its own (--get, --set "
                  "cover=@IMAGE or --unset cover), not with other edits",
                  file=sys.stderr)
            return 2
        if kind == "bad-value":
            print(f"acidcat edit: cover={value!r}: name the image as cover=@PATH",
                  file=sys.stderr)
            return 2
        if len(args.inputs) != 1:
            print("acidcat edit: the cover is edited one file at a time",
                  file=sys.stderr)
            return 2
        from acidcat.commands import cover as covercmd
        if kind != "get":
            # set and remove here, not through `cover`'s parser: its -o means
            # "extract to", and it has no --dry-run, so both were dropped and
            # a dry run, or an -o copy, rewrote the input in place
            return covercmd.change(args.inputs[0], value if kind == "set" else None,
                                   out=args.output, dry_run=args.dry_run,
                                   overwrite=args.overwrite)
        argv = [args.inputs[0]]
        _legacy.flag(argv, "-o", args.output)
        return _legacy.run(covercmd, "cover", argv)

    from acidcat.commands import write
    argv = list(args.inputs) + ["--output-format", chosen_format(args)]
    for s in args.sets:
        argv += ["--set", s]
    for u in args.unsets:
        argv += ["--set", u + "="]
    _legacy.flag(argv, "-o", args.output)
    _legacy.switch(argv, "--dry-run", args.dry_run)
    _legacy.switch(argv, "--overwrite", args.overwrite)
    _legacy.switch(argv, "--strip", args.strip)
    ns = _legacy.parser_for(write, "write").parse_args(argv)
    ns.force = args.force
    ns.cascade = args.cascade
    ns.quiet = args.quiet
    return write.run(ns)
