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

from acidcat.core import constraints, writer
from acidcat.core.repairers import AudioGuardError


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
    p.set_defaults(func=run)


def _present(path, report):
    """Print a report's header + one line per violation. Returns True if there
    is anything to write."""
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


def _repair_one(path, args):
    with open(path, "rb") as f:
        data = f.read()
    opts = {"keep_pad": args.keep_pad}

    if constraints.repairer_for(data) is None:
        print(f"acidcat repair: {path}: not a RIFF/AIFF/MP4 container "
              f"(nothing to repair here)", file=sys.stderr)
        return 1

    if args.dry_run:
        report = constraints.analyze(data, opts)
        _present(path, report)
        return 0

    try:
        new_data, report = constraints.repair(data, opts)
    except AudioGuardError as e:
        print(f"acidcat repair: {path}: aborted, {e} (refusing to write)",
              file=sys.stderr)
        return 1

    if not _present(path, report):
        return 0
    try:
        written, backup = writer.commit(
            path, new_data, out=args.output, overwrite=args.overwrite)
    except OSError as e:
        print(f"acidcat repair: {path}: {e}", file=sys.stderr)
        return 1
    note = f"  (backup: {os.path.basename(backup)})" if backup else ""
    print(f"  wrote {os.path.basename(written)}{note}")
    return 0


def run(args):
    if args.output and len(args.inputs) > 1:
        print("acidcat repair: -o works with a single input file", file=sys.stderr)
        return 2
    rc = 0
    for path in args.inputs:
        try:
            rc = _repair_one(path, args) or rc
        except (OSError, ValueError) as e:
            print(f"acidcat repair: {path}: {e}", file=sys.stderr)
            rc = 1
    return rc
