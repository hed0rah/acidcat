"""acidcat shape -- one structural fingerprint per file, for specimen hunting.

Prints one tab-separated line per file: format, a header summary, the sorted set
of chunk/block ids, a warn flag, and the path. Grep/sort/uniq friendly, so a big
sample tree collapses to its distinct shapes:

    acidcat shape ~/samples --no-path | sort | uniq -c | sort -n   # count 1 = a specimen
    acidcat shape ~/samples --warn-only                            # the malformed / odd ones
    acidcat shape ~/samples | grep cart                            # files carrying a rare chunk

Rides on the walker (walk_file), so it covers every format acidcat parses and
inherits the degrade-never-raise contract: a file the walker cannot decode is
skipped, one that crashes it is flagged (a specimen in its own right).
"""

import os

from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported

# chunk/block ids whose summary is the file's headline (first match wins)
_HEADER_IDS = ("fmt", "STREAMINFO", "COMM", "MThd", "ftyp")


def register(subparsers):
    p = subparsers.add_parser(
        "shape", help="one structural fingerprint per file (for sort | uniq -c)")
    p.add_argument("targets", nargs="+", metavar="target",
                   help="files or directories (directories are recursed)")
    p.add_argument("--no-path", action="store_true",
                   help="omit the path column so identical shapes collapse under uniq -c")
    p.add_argument("--coarse", action="store_true",
                   help="drop the header summary (cluster on format + chunk-set only; "
                        "avoids fragmenting on per-file facts like duration)")
    p.add_argument("--warn-only", action="store_true",
                   help="only files that emit a structural warning")
    p.set_defaults(func=run)


def _iter_files(targets):
    for t in targets:
        if os.path.isfile(t):
            yield t
        elif os.path.isdir(t):
            for root, _dirs, names in os.walk(t):
                for name in names:
                    yield os.path.join(root, name)


def _fingerprint(path):
    """(label, summary, chunk_ids, warned), or None if not a decodable file."""
    try:
        label, chunks, warns = walk_file(path)
    except Unsupported:
        return None
    except Exception as e:                 # a crash IS a specimen -- flag it
        return (f"!{type(e).__name__}", "", "", True)
    ids = ",".join(sorted({str(c["id"]).strip() for c in chunks}))
    summary = next((c["summary"] for c in chunks
                    if str(c["id"]).strip() in _HEADER_IDS), "")
    return (label, summary, ids, bool(warns))


def run(args):
    for path in _iter_files(args.targets):
        fp = _fingerprint(path)
        if fp is None:
            continue
        label, summary, ids, warned = fp
        if args.warn_only and not warned:
            continue
        if args.coarse:
            summary = ""
        cols = [label, summary, ids, "WARN" if warned else ""]
        if not args.no_path:
            cols.append(path)
        print("\t".join(cols))
    return 0
