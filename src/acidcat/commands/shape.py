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
import sys

from acidcat.commands._output import add_output_format_arg
from acidcat.core.infra.render import output as _render

from acidcat.core.infra import sniff as sniffmod
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
    # tsv stays the DEFAULT -- shape's contract is `sort | uniq -c` and every
    # existing use depends on it. The other renderings are additive: shape was
    # the one verb producing records with no way to hand them to a JSON
    # consumer, which is odd for the verb whose entire output IS data.
    add_output_format_arg(p, default="tsv", only=("tsv", "csv", "json", "table"))
    p.set_defaults(func=run)


def _iter_files(targets):
    """Yield (path, named): `named` is True for a path the caller wrote on the
    command line, False for one the directory recursion turned up."""
    for t in targets:
        if os.path.isfile(t):
            yield t, True
        elif os.path.isdir(t):
            for root, _dirs, names in os.walk(t):
                for name in names:
                    yield os.path.join(root, name), False


def _ids(seq):
    return ",".join(sorted({str(c).strip() for c in seq}))


# A file you NAMED gets a row even when nothing can walk it; a file the
# directory recursion FOUND does not. Naming a file is a question about that
# file, and answering it with zero bytes and exit 0 is indistinguishable from
# "walked it, the filters excluded it" -- `shape mystery.ch1` printed nothing,
# and a sweep over a directory of one unknown format produced an empty
# histogram rather than the cluster of N that is the whole signal. Recursion is
# a question about a tree, where a row per README and .DS_Store is noise.
_UNWALKED = "?unwalked"


def _fast_fingerprint(path):
    """sniff + a cheap chunk-id set, no field parsing -> (label, "", ids, "")."""
    fmt = sniffmod.sniff(path)
    if fmt is None or fmt not in _WALKERS:
        return None
    label = _WALKERS[fmt][0]
    ids = ""
    try:
        if fmt == "wav":
            from acidcat.core.formats.riff import iter_chunks
            ids = _ids(c for c, _, _ in iter_chunks(path))
        elif fmt in ("aiff", "aifc"):
            from acidcat.core.formats.aiff import iter_chunks
            ids = _ids(c for c, _, _ in iter_chunks(path))
        elif fmt == "flac":
            from acidcat.core.formats.flac import iter_metadata_blocks
            ids = _ids(b[1] for b in iter_metadata_blocks(path))
        flag = ""
    except Exception:
        # a parse failure and a legitimately empty file emitted identical rows
        flag = "!parse-failed"
    return (label, "", ids, flag)


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
        from acidcat.core.forensics import anomalies
        try:
            findings = anomalies.scan(path, label, chunks, warns) or []
            flag = ",".join(sorted({f["rule"] for f in findings}))
        except Exception:
            # a scan that crashed is not a file with no anomalies, and
            # --warn-only filters on this flag -- so the empty string dropped
            # exactly the crashiest specimens out of the listing
            flag = "!anomaly-scan-failed"
    else:
        flag = "WARN" if warns else ""
    return (label, summary, ids, flag)


def run(args):
    from acidcat.util.stdin import resolved_input
    from contextlib import ExitStack
    # `-` is stdin. Resolved to a real path up front so the walk below is
    # untouched; a path that is not "-" passes through unchanged.
    with ExitStack() as stack:
        args.targets = [
            stack.enter_context(resolved_input(t)) if t == "-" else t
            for t in args.targets
        ]
        if any(t is None for t in args.targets):
            print("acidcat stats: no data on stdin", file=sys.stderr)
            return 1
        return _run(args)


def _run(args):
    # a target that does not exist yielded nothing, printed nothing and exited
    # 0 -- indistinguishable from "scanned it, matched nothing"
    missing = [t for t in args.targets if not os.path.exists(t)]
    for t in missing:
        print(f"acidcat stats: {t}: No such file or directory", file=sys.stderr)
    if missing and len(missing) == len(args.targets):
        return 2
    emitted = 0
    rows = []
    cap = getattr(args, "max_files", None)       # stats --max-files; 0 or None: none
    seen = 0
    capped = False
    for path, named in _iter_files(args.targets):
        if cap and seen >= cap:
            capped = True
            break
        seen += 1
        fp = (_fast_fingerprint(path) if args.fast
              else _full_fingerprint(path, args.anomalies))
        if fp is None:
            if not named:
                continue
            fp = (_UNWALKED, sniffmod.sniff(path) or "", "", "")
        label, summary, ids, flag = fp
        if args.fmt_filter and args.fmt_filter.lower() not in label.lower():
            continue
        if args.warn_only and not flag:
            continue
        if args.coarse:
            summary = ""
        emitted += 1
        row = {"format": label, "summary": summary, "chunks": ids, "flag": flag}
        if not args.no_path:
            row["path"] = path
        rows.append(row)
    if capped:
        from acidcat.commands.stats import cap_note
        cap_note(cap)
    fmt = getattr(args, "output_format", "tsv")
    if rows:
        if fmt == "tsv":
            # Hand-rolled rather than through the renderer, deliberately: the
            # historical format has NO header row, and shape's whole contract
            # is `sort | uniq -c`, which would count a header as a data line.
            for r in rows:
                print("\t".join(str(v) for v in r.values()))
        elif fmt == "table":
            from acidcat.core.infra.render import format_columns
            # the chunk list last: it is the one column with no useful width
            cols = [("format", "format"), ("flag", "flag"), ("summary", "summary")]
            if not args.no_path:
                cols.append(("path", "path"))
            format_columns(rows, cols + [("chunks", "chunks")])
        else:
            _render(rows, fmt=fmt)
    # a filter that matched nothing is a negative result, not a success --
    # `shape lib --format flac && ...` used to proceed on an empty listing
    return 0 if emitted else 1
