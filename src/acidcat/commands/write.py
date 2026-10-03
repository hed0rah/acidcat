"""acidcat write -- edit metadata fields (exiftool-style).

    acidcat write FILE... --set field=value [--set ...] [-o OUT] [--dry-run]

Field names are the ones acidcat displays, so editing is WYSIWYG. By default the
edit happens in place after a `<name>_original` backup is saved; `-o` writes a
modified copy instead. `--dry-run` prints the field-level diff and writes nothing.
"""

import os
import struct
import sys

from acidcat.commands._output import add_output_format_arg, chosen_format, format_of
from acidcat.core.infra.render import output as _render
from acidcat.core.write import writer, edits


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
    # write CHANGES your files and could not tell a script which fields moved,
    # what they moved to, or where the backup went.
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
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


def _plain(v):
    """A value as JSON holds it: bytes as `hex:...`, the spelling --set takes."""
    return "hex:" + bytes(v).hex() if isinstance(v, (bytes, bytearray)) else v


def _shown(v):
    return _plain(v) if isinstance(v, (bytes, bytearray)) else repr(v)


def _edit(path, changes, force=False, cascade=True, repairs=None, quiet=False):
    """Return (format_label, new_bytes, applied) for the file, or raise EditError.

    Goes through the one edit front door (acidcat.core.edit): the changes
    become a Patch, which is verified -- every field read back through its
    profile, no new defect on a re-walk -- before its bytes are returned.

    With `cascade`, an edit to a field or byte range first has the fields a
    constraint ties to it follow (Patch.repair(): a WAV's avg_bytes_per_sec
    after its sample_rate); what changed that way is appended to `repairs`.
    Without it, such an edit is refused by verify() as a new defect."""
    from acidcat.core import edit as editmod
    patch = editmod.edit_path(path, changes, force=force, cascade=cascade)
    if cascade and any(r.kind in ("field", "bytes") for r in patch.records):
        patch.repair()
        if repairs is not None:
            repairs += patch.repairs
    patch.verify()
    for note in ([] if quiet else patch.notes):
        # stored, but not all of what was asked: say so, once, on stderr
        print(f"acidcat edit: {path}: {note}", file=sys.stderr)
    return patch.format, patch.data, patch.applied


def _strip(path):
    """Return (format_label, new_bytes, removed) with identifying metadata gone.
    Routes by format like _edit; audio and functional data are preserved, and
    the result is a verified Patch (no defect the original did not have)."""
    from acidcat.core import edit as editmod
    with open(path, "rb") as f:
        data = f.read()
    fmt, new, removed = _strip_data(path, data)
    editmod.strip_patch(data, new, path, removed, fmt).verify()
    return fmt, new, removed


def _strip_data(path, data):
    ext = os.path.splitext(path)[1].lower()
    head = data[:16]
    if head[:1] == b"{" and (b'"synth_version"' in data[:65536] or ext == ".vital"):
        new, applied = edits.edit_vital(data, {"author": "", "comment": ""})
        # vital keys are cleared to "", not deleted; say so in the report
        return ("Vital preset", new, [a[0] + " (cleared)" for a in applied])
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        from acidcat.core.write import edit_riff
        return ("WAV",) + edit_riff.strip_wav(data)
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        from acidcat.core.write import edit_aiff
        return ("AIFF",) + edit_aiff.strip_aiff(data)
    tagged = (head[:4] == b"fLaC" or head[:3] == b"ID3" or head[:4] == b"OggS"
              or head[4:8] == b"ftyp"
              or ext in (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4"))
    if tagged:
        return ("tagged audio",) + edits.strip_tagged(data, ext or ".mp3")
    raise edits.EditUnmodelled("no metadata to strip for this file type")


def _run_strip(args):
    if args.output and len(args.inputs) > 1:
        print("acidcat edit: -o works with a single input file", file=sys.stderr)
        return 2
    fmt_out = chosen_format(args)
    rows = None if fmt_out == "table" else []
    rc = 0
    for path in args.inputs:
        if not os.path.isfile(path):
            # an input that is not there is could-not-run (2), not a refused
            # edit (1)
            print(f"acidcat edit: {path}: No such file", file=sys.stderr)
            rc = 2
            continue
        try:
            fmt, new_data, removed = _strip(path)
        except (edits.EditError,) + _mutagen_errors() as e:
            print(f"acidcat edit: {path}: {_said(e)}", file=sys.stderr)
            rc = max(rc, 2 if isinstance(e, edits.Unmodelled) else 1)
            continue
        row = None
        if rows is not None:
            row = {"path": path, **format_of(path), "written": None, "backup": None,
                   "dry_run": bool(args.dry_run), "error": None,
                   "detail": ", ".join(removed) or "(nothing to remove)",
                   "stripped": list(removed)}
            rows.append(row)
        else:
            print(f"{os.path.basename(path)}  [{fmt}]  "
                  f"stripped: {', '.join(removed) if removed else '(nothing to remove)'}")
        if args.dry_run:
            continue
        rc = _commit_and_report(path, new_data, args, row) or rc
    if rows is not None:
        _emit_rows(rows, fmt_out)
    return rc


def _emit_rows(rows, fmt):
    """Render the per-file records. csv/tsv drop the nested key rather than
    stringify a list into a cell, the same choice validate and repair make."""
    nested = ("changes", "stripped")
    if fmt in ("csv", "tsv"):
        rows = [{k: v for k, v in r.items() if k not in nested} for r in rows]
    _render(rows, fmt=fmt)


def _commit_and_report(path, new_data, args, row=None):
    """Persist edited bytes and print the outcome; returns 1 on failure, 0 on
    success. A commit failure (locked target, disk full, read-back mismatch)
    must print like any other per-file error, not traceback."""
    try:
        written, backup = writer.commit(
            path, new_data, out=args.output, overwrite=args.overwrite)
    except OSError as e:
        print(f"acidcat edit: {path}: {e}", file=sys.stderr)
        if row is not None:
            row.update(written=None, backup=None, error=str(e))
        return 2
    if row is not None:
        row.update(written=written, backup=backup)
        return 0
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


def _said(e):
    """An editor's refusal as it is, a tag-library exception as what it is."""
    if isinstance(e, edits.EditError):
        return str(e)
    return f"the tag library cannot read this file ({e})"


def _mutagen_errors():
    """Whatever mutagen raises on a file it recognizes but cannot parse.

    mutagen.MutagenError is the base for flac.error, mp3.HeaderNotFoundError,
    id3 errors and friends, but it is not the only thing that escapes -- an
    AIFF missing COMM raises a bare KeyError from inside its parser. These were
    unhandled, so 15 malformed specimens (truncated FLAC, sample rate 0, an
    all-0xFF MP3) reached the user as a raw traceback. Every other verb handles
    the same files cleanly; only the write path did not.
    """
    base = (OSError, ValueError, KeyError, IndexError, struct.error)
    try:
        import mutagen
        return base + (mutagen.MutagenError,)
    except Exception:
        return base


def run(args):
    if args.strip:
        return _run_strip(args)
    try:
        changes = _parse_sets(args.sets)
    except edits.EditError as e:
        print(f"acidcat edit: {e}", file=sys.stderr)
        return 2
    if not changes:
        print("acidcat edit: nothing to change (use --set FIELD=VALUE)",
              file=sys.stderr)
        return 2
    if args.output and len(args.inputs) > 1:
        print("acidcat edit: -o works with a single input file", file=sys.stderr)
        return 2

    fmt_out = chosen_format(args)
    rows = None if fmt_out == "table" else []
    rc = 0
    for path in args.inputs:
        if not os.path.isfile(path):
            # an input that is not there is could-not-run (2), not a refused
            # edit (1)
            print(f"acidcat edit: {path}: No such file", file=sys.stderr)
            rc = 2
            continue
        cascaded = []
        try:
            fmt, new_data, applied = _edit(path, changes,
                                           force=getattr(args, "force", False),
                                           cascade=getattr(args, "cascade", True),
                                           repairs=cascaded,
                                           quiet=getattr(args, "quiet", False))
        except (edits.EditError,) + _mutagen_errors() as e:
            print(f"acidcat edit: {path}: {_said(e)}", file=sys.stderr)
            # no editor for this kind of file, or a --set it cannot take as
            # given, is could-not-run (2); a refused edit is the answer no (1)
            rc = max(rc, 2 if isinstance(e, (edits.Unmodelled, edits.BadValue))
                     or not isinstance(e, edits.EditError) else 1)
            continue
        row = None
        if rows is not None:
            row = {"path": path, **format_of(path), "written": None, "backup": None,
                   "dry_run": bool(args.dry_run), "error": None,
                   "detail": "; ".join(f"{f}: {_shown(o)} -> {_shown(n)}"
                                       for f, o, n in applied),
                   "changes": [{"field": f, "old": _plain(o), "new": _plain(n)}
                               for f, o, n in applied],
                   "cascade": [{"field": r.addr, "old": r.old, "new": r.new,
                                "follows": r.follows} for r in cascaded]}
            rows.append(row)
        else:
            print(f"{os.path.basename(path)}  [{fmt}]")
            for field, old, new in applied:
                print(f"  {field}: {_shown(old)} -> {_shown(new)}")
            for r in cascaded:
                # a field that follows the edit, one line each, saying why
                print(f"  {r.addr}: {r.old!r} -> {r.new!r} (follows {r.follows})")
        if "experimental" in fmt and not getattr(args, "quiet", False):
            print("  note: proprietary preset editing is experimental -- verify "
                  "the preset reloads in its app; a _original backup is kept.",
                  file=sys.stderr)
        if args.dry_run:
            continue
        rc = _commit_and_report(path, new_data, args, row) or rc
    if rows is not None:
        _emit_rows(rows, fmt_out)
    return rc
