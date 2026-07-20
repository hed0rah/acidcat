"""In-sandbox worker entry point.

Run INSIDE the bwrap namespace as ``python -m acidcat._sandbox_worker <input>
[--deep]``: walk the (read-only, bind-mounted) input and emit the result as one
JSON object on stdout. Nothing else is printed, so the parent reads stdout as
the result channel. Kept tiny and import-light -- it starts with no network and
a read-only filesystem, so it must not need to write or reach out for anything.
"""

import json
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    deep = "--deep" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        sys.stdout.write(json.dumps({"ok": False, "err": "no input path"}))
        return 0
    try:
        from acidcat.core.walk import walk_file
        label, chunks, warns = walk_file(paths[0], deep=deep)
        out = {"ok": True, "label": label, "chunks": chunks, "warns": warns}
    except MemoryError:
        out = {"ok": False, "err": "memory limit exceeded"}
    except BaseException as e:                       # incl. Unsupported, walker bugs
        out = {"ok": False, "err": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
