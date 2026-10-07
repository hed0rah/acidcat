"""acidcat repair -- fix structural inconsistencies without touching the audio.

Repair is one move over the constraint model (core/constraints): parse the
container, find the derived fields whose stored value disagrees with their
function, and re-emit with the witnessed ones corrected. The bytes it changes are
only the ones it can justify from an independent witness -- a stale master size
(end-of-file witnesses it), a nested size (its parsed contents), a broken MP4
offset table (mdat's real position plus the sample sizes), a non-zero pad byte
(the spec). It never invents or removes content, and the audio payload is guarded.

    acidcat repair FILE...              # fix in place (keeps a _original backup)
    acidcat repair FILE -o fixed.wav    # write a corrected copy instead
    acidcat repair FILE --dry-run       # show what would change, write nothing

Supports the containers acidcat models structurally: RIFF/WAVE, RF64, AIFF/AIFC,
the SoundFont (sfbk) containers, and MP4/M4A. Anything else reports "nothing to
repair here" rather than guessing.
"""

import os
import sys

from acidcat.commands._output import add_output_format_arg, chosen_format, format_of
from acidcat.core.infra.render import output as _render
from acidcat.core.write import constraints, writer
from acidcat.core.write.repairers import AudioGuardError


def register(subparsers):
    p = subparsers.add_parser(
        "repair",
        help="Recompute stale size/offset/pad fields in a container (audio preserved).")
    p.add_argument("inputs", nargs="+", help="File(s) to repair.")
    p.add_argument("-o", "--output", help="Write a corrected copy here (single input).")
    p.add_argument("--dry-run", action="store_true",
                   help="Show the changes and write nothing.")
    p.add_argument("--overwrite", action="store_true",
                   help="Skip the _original backup on in-place repair.")
    p.add_argument("--keep-pad", action="store_true",
                   help="Do not normalize a non-zero pad byte to 0x00.")
    # repair CHANGES your files and could not tell a script which ones or how.
    # json carries the per-violation detail; csv/tsv are one row per file.
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
    p.set_defaults(func=run)


def _record(path, report):
    """The machine shape of a report: what this file is and what is wrong."""
    return {"path": path, **format_of(path),
            "issues": len(report.violations),
            "repairable": any(v.repairable for v in report.violations),
            "detail": "; ".join(v.describe() for v in report.violations),
            "violations": [{"describe": v.describe(), "kind": v.kind,
                            "field": v.field, "stored": v.stored,
                            "computed": v.computed, "repairable": v.repairable}
                           for v in report.violations]}


def _present(path, report, rows=None):
    """Print a report's header + one line per violation, or append a record
    when `rows` is given. Returns True if there is anything to write."""
    if rows is not None:
        rows.append(_record(path, report))
        return any(v.repairable for v in report.violations)
    base = os.path.basename(path)
    if not report.violations:
        tail = f"  {report.note}" if report.note else "  already consistent"
        print(f"{base}  [{report.label}]{tail}")
        return False
    print(f"{base}  [{report.label}]")
    for v in report.violations:
        mark = "" if v.repairable else "  (no witness, left as-is)"
        print(f"  {v.describe()}{mark}")
    return any(v.repairable for v in report.violations)


def _repair_one(path, args, rows=None):
    with open(path, "rb") as f:
        data = f.read()
    opts = {"keep_pad": args.keep_pad}

    if constraints.repairer_for(data) is None:
        # nothing checkable, the same answer `validate` gives on a format it
        # does not model -- not a passing result for a file never examined
        if rows is not None:
            rows.append({"path": path, **format_of(path), "action": "skipped",
                         "issues": 0, "repairable": False, "written": None,
                         "backup": None,
                         "detail": "not a structurally-modeled container"})
        from acidcat.commands._output import checked_formats
        print(f"acidcat check: {path}: not a format check models (nothing to "
              f"repair here); check covers: {checked_formats()}", file=sys.stderr)
        return 2

    if args.dry_run:
        report = constraints.analyze(data, opts)
        _present(path, report, rows)
        # 1 for any violation, the same answer `validate` gives on the same
        # file. --dry-run always returned 0, so `repair --dry-run f && echo
        # clean` printed "clean" over a list of pending repairs. Keyed on
        # violations rather than on repairability so the two verbs cannot
        # disagree about whether a file is sound.
        if rows is not None:
            rows[-1]["action"] = "would-repair" if report.violations else "clean"
            rows[-1]["written"] = rows[-1]["backup"] = None
        return 1 if report.violations else 0

    try:
        new_data, report = constraints.repair(data, opts)
    except AudioGuardError as e:
        print(f"acidcat check: {path}: aborted, {e} (refusing to write)",
              file=sys.stderr)
        return 1

    # a defect with no witness is left in the file. --fix used to exit 0 and
    # call such a file "clean", so `check --fix f && ship f` shipped it while
    # --dry-run on the same file said 1. Whatever is left after the fix
    # decides the exit, the same way it decides --dry-run's.
    left = [v for v in report.violations if not v.repairable]
    rc = 1 if left else 0
    to_write = _present(path, report, rows)
    if left:
        print(f"acidcat check: {path}: {len(left)} defect(s) left unrepaired "
              f"(no witness to fix them from)", file=sys.stderr)
    if not to_write:
        if rows is not None:
            rows[-1].update(action="unrepaired" if left else "clean",
                            written=None, backup=None)
        return rc
    try:
        written, backup = writer.commit(
            path, new_data, out=args.output, overwrite=args.overwrite)
    except OSError as e:
        print(f"acidcat check: {path}: {e}", file=sys.stderr)
        return 2
    if rows is not None:
        rows[-1].update(action="partly-repaired" if left else "repaired",
                        written=written, backup=backup)
        return rc
    if backup:
        note = f"  (backup: {os.path.basename(backup)})"
    elif not args.output and not args.overwrite:
        # commit returns None both when it made no backup AND when it found a
        # <name>_original already on disk and kept it. Printing nothing for the
        # second case told the user their input was rewritten in place with a
        # backup, when the file holding that name may predate acidcat and have
        # nothing to do with this original. write.py has said so since it was
        # written; repair, which rewrites structure, did not.
        note = "  (existing backup kept)"
    else:
        note = ""
    print(f"  wrote {os.path.basename(written)}{note}")
    return rc


def run(args):
    if args.output and len(args.inputs) > 1:
        print("acidcat check: -o works with a single input file", file=sys.stderr)
        return 2
    fmt = chosen_format(args)
    rows = None if fmt == "table" else []
    rc = 0
    for path in args.inputs:
        try:
            rc = _repair_one(path, args, rows) or rc
        except (OSError, ValueError) as e:
            print(f"acidcat check: {path}: {e}", file=sys.stderr)
            rc = 2
    if rows is not None:
        if fmt in ("csv", "tsv"):
            # a nested violation list has no honest cell; drop the key rather
            # than stringify it, same as validate
            rows = [{k: v for k, v in r.items() if k != "violations"} for r in rows]
        _render(rows, fmt=fmt)
    return rc
