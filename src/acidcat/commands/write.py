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
    p.add_argument("--strip", action="store_true",
                   help="Remove identifying metadata (tags/bext/iXML/ID3/etc.); "
                        "keeps audio and functional chunks. Ignores --set.")
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
    if head[:4] == b"BtWg":
        return ("Bitwig preset (experimental)",) + edits.edit_bitwig(data, changes)
    if head[12:16] == b"hsin" or head[:4] == b"-in-" \
            or (head[:4] == b"RIFF" and head[8:12] == b"NIKS"):
        return ("NI preset (experimental)",) + edits.edit_ni(data, changes)
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        try:
            from acidcat.core import edit_riff
        except ImportError:
            raise edits.EditError("WAV editing is not available in this build")
        return ("WAV",) + edit_riff.edit_wav(data, changes)
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        from acidcat.core import edit_aiff
        return ("AIFF",) + edit_aiff.edit_aiff(data, changes)
    tagged = (head[:4] == b"fLaC" or head[:3] == b"ID3" or head[:4] == b"OggS"
              or head[4:8] == b"ftyp"
              or ext in (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4"))
    if tagged:
        return ("tagged audio",) + edits.edit_tagged(data, ext or ".mp3", changes)
    raise edits.EditError("no metadata editor for this file type")


def _strip(path):
    """Return (format_label, new_bytes, removed) with identifying metadata gone.
    Routes by format like _edit; audio and functional data are preserved."""
    with open(path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(path)[1].lower()
    head = data[:16]
    if head[:1] == b"{" and (b'"synth_version"' in data[:65536] or ext == ".vital"):
        new, applied = edits.edit_vital(data, {"author": "", "comment": ""})
        # vital keys are cleared to "", not deleted; say so in the report
        return ("Vital preset", new, [a[0] + " (cleared)" for a in applied])
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        from acidcat.core import edit_riff
        return ("WAV",) + edit_riff.strip_wav(data)
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        from acidcat.core import edit_aiff
        return ("AIFF",) + edit_aiff.strip_aiff(data)
    tagged = (head[:4] == b"fLaC" or head[:3] == b"ID3" or head[:4] == b"OggS"
              or head[4:8] == b"ftyp"
              or ext in (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4"))
    if tagged:
        return ("tagged audio",) + edits.strip_tagged(data, ext or ".mp3")
    raise edits.EditError("no metadata to strip for this file type")


def _run_strip(args):
    if args.output and len(args.inputs) > 1:
        print("acidcat write: -o works with a single input file", file=sys.stderr)
        return 2
    rc = 0
    for path in args.inputs:
        try:
            fmt, new_data, removed = _strip(path)
        except (edits.EditError, OSError, ValueError) as e:
            print(f"acidcat write: {path}: {e}", file=sys.stderr)
            rc = 1
            continue
        print(f"{os.path.basename(path)}  [{fmt}]  "
              f"stripped: {', '.join(removed) if removed else '(nothing to remove)'}")
        if args.dry_run:
            continue
        rc = _commit_and_report(path, new_data, args) or rc
    return rc


def _commit_and_report(path, new_data, args):
    """Persist edited bytes and print the outcome; returns 1 on failure, 0 on
    success. A commit failure (locked target, disk full, read-back mismatch)
    must print like any other per-file error, not traceback."""
    try:
        written, backup = writer.commit(
            path, new_data, out=args.output, overwrite=args.overwrite)
    except OSError as e:
        print(f"acidcat write: {path}: {e}", file=sys.stderr)
        return 1
    if backup:
        note = f"  (backup: {os.path.basename(backup)})"
    elif not args.output and not args.overwrite:
        # commit found a <name>_original already on disk and kept it; say so,
        # because that file may predate acidcat and not hold this original
        note = "  (existing backup kept)"
    else:
        note = ""
    print(f"  wrote {os.path.basename(written)}{note}")
    return 0


def run(args):
    if args.strip:
        return _run_strip(args)
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
        if "experimental" in fmt:
            print("  note: proprietary preset editing is experimental -- verify "
                  "the preset reloads in its app; a _original backup is kept.",
                  file=sys.stderr)
        if args.dry_run:
            continue
        rc = _commit_and_report(path, new_data, args) or rc
    return rc
