"""acidcat write -- edit metadata fields (exiftool-style).

    acidcat write FILE... --set field=value [--set ...] [-o OUT] [--dry-run]

Field names are the ones acidcat displays, so editing is WYSIWYG. By default the
edit happens in place after a `<name>_original` backup is saved; `-o` writes a
modified copy instead. `--dry-run` prints the field-level diff and writes nothing.
"""

import os
import sys

from acidcat.core import writer, edits


def register(subparsers):
    p = subparsers.add_parser(
        "write",
        help="Edit metadata fields in place (or to a -o copy).",
    )
    p.add_argument("inputs", nargs="+", help="File(s) to edit.")
    p.add_argument("--set", dest="sets", action="append", default=[],
                   metavar="FIELD=VALUE",
                   help="Set a field (repeatable). Empty value clears it.")
    p.add_argument("-o", "--output",
                   help="Write a modified copy here (single input only).")
    p.add_argument("--dry-run", action="store_true",
                   help="Show the diff and write nothing.")
    p.add_argument("--overwrite", action="store_true",
                   help="Skip the _original backup on in-place edits.")
    p.set_defaults(func=run)


def _parse_sets(set_args):
    changes = {}
    for s in set_args:
        if "=" not in s:
            raise edits.EditError(f"--set expects FIELD=VALUE, got {s!r}")
        field, value = s.split("=", 1)
        field = field.strip()
        if not field:
            raise edits.EditError(f"--set has an empty field name: {s!r}")
        changes[field] = value if value != "" else None
    return changes


def _edit(path, changes):
    """Return (format_label, new_bytes, applied) for the file, or raise EditError."""
    with open(path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(path)[1].lower()
    head = data[:16]
    if head[:1] == b"{" and (b'"synth_version"' in data[:65536] or ext == ".vital"):
        return ("Vital preset",) + edits.edit_vital(data, changes)
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        try:
            from acidcat.core import edit_riff
        except ImportError:
            raise edits.EditError("WAV editing is not available in this build")
        return ("WAV",) + edit_riff.edit_wav(data, changes)
    tagged = (head[:4] == b"fLaC" or head[:3] == b"ID3" or head[:4] == b"OggS"
              or head[4:8] == b"ftyp"
              or ext in (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4"))
    if tagged:
        return ("tagged audio",) + edits.edit_tagged(data, ext or ".mp3", changes)
    raise edits.EditError("no metadata editor for this file type")


def run(args):
    try:
        changes = _parse_sets(args.sets)
    except edits.EditError as e:
        print(f"acidcat write: {e}", file=sys.stderr)
        return 2
    if not changes:
        print("acidcat write: nothing to change (use --set FIELD=VALUE)",
              file=sys.stderr)
        return 2
    if args.output and len(args.inputs) > 1:
        print("acidcat write: -o works with a single input file", file=sys.stderr)
        return 2

    rc = 0
    for path in args.inputs:
        try:
            fmt, new_data, applied = _edit(path, changes)
        except (edits.EditError, OSError, ValueError) as e:
            print(f"acidcat write: {path}: {e}", file=sys.stderr)
            rc = 1
            continue
        print(f"{os.path.basename(path)}  [{fmt}]")
        for field, old, new in applied:
            print(f"  {field}: {old!r} -> {new!r}")
        if args.dry_run:
            continue
        written, backup = writer.commit(
            path, new_data, out=args.output, overwrite=args.overwrite)
        note = f"  (backup: {os.path.basename(backup)})" if backup else ""
        print(f"  wrote {os.path.basename(written)}{note}")
    return rc
