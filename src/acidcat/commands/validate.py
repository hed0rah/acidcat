"""acidcat validate -- report structural constraint violations, read-only.

The read-only face of the constraint model (core/constraints): it runs the same
analysis ``repair`` uses, but writes nothing and returns an exit code, so it fits
a CI check or a sweep over a whole library to find the broken files before they
bite. A file whose container acidcat does not model structurally is skipped, not
failed.

    acidcat validate FILE...            # check specific files
    acidcat validate DIR                # walk a directory tree
    acidcat validate DIR -q             # only print files with issues

Exit status: 0 when every checked file is consistent, 1 when any file has a
violation, 2 on a usage error.
"""

import os
import sys

from acidcat.core import constraints

_EXTS = (".wav", ".rf64", ".bwf", ".aif", ".aiff", ".aifc", ".sf2", ".sf3",
         ".m4a", ".mp4", ".mov", ".m4b")


def register(subparsers):
    p = subparsers.add_parser(
        "validate",
        help="Check container structure for stale size/offset/pad fields (read-only).")
    p.add_argument("inputs", nargs="+", help="File(s) or directory(ies) to check.")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Only print files that have violations.")
    p.set_defaults(func=run)


def _iter_paths(inputs):
    for inp in inputs:
        if os.path.isdir(inp):
            for root, _dirs, files in os.walk(inp):
                for name in sorted(files):
                    if name.lower().endswith(_EXTS):
                        yield os.path.join(root, name)
        else:
            yield inp


def _check(path, quiet):
    """Return (checked, ok): checked is False for a skipped/unreadable file."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"acidcat validate: {path}: {e}", file=sys.stderr)
        return False, True
    report = constraints.analyze(data)
    if report is None:
        return False, True                      # not a structurally-modeled container
    base = os.path.basename(path)
    if not report.violations:
        if not quiet:
            print(f"OK    {base}  [{report.label}]")
        return True, True
    print(f"FAIL  {base}  [{report.label}]  {len(report.violations)} issue(s)")
    for v in report.violations:
        mark = "" if v.repairable else "  (no witness)"
        print(f"        {v.describe()}{mark}")
    return True, False


def run(args):
    checked = failed = 0
    for path in _iter_paths(args.inputs):
        did, ok = _check(path, args.quiet)
        if did:
            checked += 1
            if not ok:
                failed += 1
    if checked == 0:
        print("acidcat validate: no structurally-modeled files to check",
              file=sys.stderr)
        return 0
    if failed:
        print(f"\n{failed} of {checked} file(s) have structural issues "
              f"(fix with: acidcat repair)")
        return 1
    if not args.quiet:
        print(f"\nall {checked} file(s) consistent")
    return 0
