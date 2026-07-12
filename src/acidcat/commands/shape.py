"""acidcat shape -- one structural fingerprint per file, for specimen hunting.

Prints one tab-separated line per file: format, a header summary, the sorted set
of chunk/block ids, a warn/anomaly flag, and the path. Grep/sort/uniq friendly,
so a big sample tree collapses to its distinct shapes:

    acidcat shape ~/samples --no-path | sort | uniq -c | sort -n   # count 1 = a specimen
    acidcat shape ~/samples --coarse                               # cluster on format+chunk-set
    acidcat shape ~/samples --anomalies --warn-only                # polyglots/cavities/trailing
    acidcat shape ~/samples --format wav | grep cart               # rare chunk in a given format
    acidcat shape ~/samples --fast                                 # header-only, for huge trees

Default rides on the walker (full parse: summary + warn + optional anomaly scan).
``--fast`` sniffs the format and reads only the chunk-id set (no field parsing),
for scanning very large trees. Both inherit degrade-never-raise: an undecodable
file is skipped, one that crashes the walker is flagged (a specimen in itself).
"""

import os

from acidcat.core import sniff as sniffmod
from acidcat.core.walk import walk_file, _WALKERS
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
                   help="drop the header summary (cluster on format + chunk-set only)")
    p.add_argument("--fast", action="store_true",
                   help="header-only: sniff + chunk-id set, no field parsing (for huge trees)")
    p.add_argument("--anomalies", action="store_true",
                   help="emit the anomaly types (polyglot/cavity/trailing/...) in place of a bare WARN")
    p.add_argument("--format", metavar="FMT", dest="fmt_filter",
                   help="only files whose format label contains FMT (case-insensitive)")
    p.add_argument("--warn-only", action="store_true",
                   help="only files that carry a warning / anomaly")
    p.set_defaults(func=run)


def _iter_files(targets):
    for t in targets:
        if os.path.isfile(t):
            yield t
        elif os.path.isdir(t):
            for root, _dirs, names in os.walk(t):
                for name in names:
                    yield os.path.join(root, name)


def _ids(seq):
    return ",".join(sorted({str(c).strip() for c in seq}))


def _fast_fingerprint(path):
    """sniff + a cheap chunk-id set, no field parsing -> (label, "", ids, "")."""
    fmt = sniffmod.sniff(path)
    if fmt is None or fmt not in _WALKERS:
        return None
    label = _WALKERS[fmt][0]
    ids = ""
    try:
        if fmt == "wav":
            from acidcat.core.riff import iter_chunks
            ids = _ids(c for c, _, _ in iter_chunks(path))
        elif fmt in ("aiff", "aifc"):
            from acidcat.core.aiff import iter_chunks
            ids = _ids(c for c, _, _ in iter_chunks(path))
        elif fmt == "flac":
            from acidcat.core.flac import iter_metadata_blocks
            ids = _ids(b[1] for b in iter_metadata_blocks(path))
    except Exception:
        pass
    return (label, "", ids, "")


def _full_fingerprint(path, want_anomalies):
    """walk the file -> (label, header-summary, chunk-id set, flag). flag is the
    anomaly-rule set when want_anomalies, else 'WARN'/''"""
    try:
        label, chunks, warns = walk_file(path)
    except Unsupported:
        return None
    except Exception as e:                 # a crash IS a specimen -- flag it
        return (f"!{type(e).__name__}", "", "", "crash")
    ids = _ids(c["id"] for c in chunks)
    summary = next((c["summary"] for c in chunks
                    if str(c["id"]).strip() in _HEADER_IDS), "")
    if want_anomalies:
        from acidcat.core import anomalies
        try:
            findings = anomalies.scan(path, label, chunks, warns) or []
        except Exception:
            findings = []
        flag = ",".join(sorted({f["rule"] for f in findings}))
    else:
        flag = "WARN" if warns else ""
    return (label, summary, ids, flag)


def run(args):
    for path in _iter_files(args.targets):
        fp = (_fast_fingerprint(path) if args.fast
              else _full_fingerprint(path, args.anomalies))
        if fp is None:
            continue
        label, summary, ids, flag = fp
        if args.fmt_filter and args.fmt_filter.lower() not in label.lower():
            continue
        if args.warn_only and not flag:
            continue
        if args.coarse:
            summary = ""
        cols = [label, summary, ids, flag]
        if not args.no_path:
            cols.append(path)
        print("\t".join(cols))
    return 0
