"""
acidcat query -- filter samples across all registered libraries.

Default behavior is to fan out across every library in the registry,
merge results, dedup by path, and apply a final LIMIT. Use --root to
scope to one or more libraries by label or path. Override the registry
location with --registry or the ACIDCAT_REGISTRY env var.
"""

import json
import os
import sqlite3
import sys

from acidcat.util.argtypes import nonneg_int
from acidcat.core.catalogue import index as idx
from acidcat.commands._output import add_output_format_arg
from acidcat.core.catalogue import paths as acidpaths
from acidcat.core.catalogue import query_sql
from acidcat.core.catalogue import registry as reg
from acidcat.core.infra.render import format_json, output


DEFAULT_FIELDS = [
    "path", "format", "bpm", "key", "duration",
    "title", "artist", "album",
    "preset_name", "device", "product", "creator", "category",
    "scan_root",
]


def register(subparsers):
    p = subparsers.add_parser(
        "query",
        help="Filter samples across all registered libraries.",
    )
    p.add_argument("--registry",
                   help="Override registry DB path "
                        "(default: ~/.acidcat/registry.db).")
    p.add_argument("--bpm", help="BPM filter. Exact (128) or range (120:130).")
    p.add_argument("--key", help="Key filter (exact match, e.g. Am, C#).")
    p.add_argument("--duration", help="Duration filter in seconds. "
                   "Exact (2) or range (0.5:2).")
    p.add_argument("--tag", action="append", default=[],
                   help="Tag filter. Repeat for AND semantics.")
    p.add_argument("--format", dest="file_format",
                   help="File format filter (wav, mp3, flac, midi, ...).")
    p.add_argument("--device",
                   help="Preset device/instrument (e.g. Polysynth, Massive).")
    p.add_argument("--category",
                   help="Preset category (e.g. Reverb, Bass, Synth).")
    p.add_argument("--creator", help="Preset creator/author.")
    p.add_argument("--product",
                   help="Product (Bitwig, Vital, Massive, Absynth, FM8, ...).")
    p.add_argument("--text", help="Full-text search across title/artist/album/"
                   "genre/comment/description/tags/preset/device/creator/path.")
    p.add_argument("--root",
                   help="Scope results to one or more libraries (label or "
                        "path). Comma-separate to query multiple libraries.")
    p.add_argument("--compatible-with", dest="compatible_with", metavar="FILE",
                   help="Find samples that mix with FILE: harmonically "
                        "compatible key (Camelot neighbours) + compatible tempo "
                        "(incl. half/double-time). Reads FILE's key/BPM/kind from "
                        "the index or the file itself.")
    p.add_argument("--bpm-tolerance", dest="bpm_tolerance", type=float,
                   default=6.0,
                   help="Percent BPM window for --compatible-with (default 6).")
    p.add_argument("--same-key", dest="same_key", action="store_true",
                   help="With --compatible-with, require the exact key.")
    p.add_argument("--no-half-double", dest="no_half_double", action="store_true",
                   help="With --compatible-with, skip half-/double-time matches.")
    p.add_argument("--kind", dest="kind", choices=["loop", "one_shot", "any"],
                   help="With --compatible-with, override the inferred sample "
                        "kind filter (loop / one_shot / any).")
    p.add_argument("--limit", type=nonneg_int, default=50,
                   help="Max rows (default 50).")
    add_output_format_arg(p, only=("table", "json", "csv", "tsv"))
    p.add_argument("-o", "--output", help="Write output to file.")
    p.add_argument("--paths-only", action="store_true",
                   help="Print bare paths, one per line.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Diagnostic lines on stderr (per-library row counts).")
    p.set_defaults(func=run)


def _vlog(args, msg):
    if getattr(args, "verbose", False):
        print(msg, file=sys.stderr)


def run(args):
    try:
        return _run(args)
    except QueryUsageError as e:
        print(f"acidcat lib: {e}", file=sys.stderr)
        return 2


def _run(args):
    # Validate the invocation before consulting any state. A malformed --bpm is
    # wrong whether or not a library happens to be registered, and checking it
    # second meant the same command reported "no libraries" (1) on a fresh
    # machine and "bad value" (2) on a populated one.
    for field in ("bpm", "duration"):
        spec = getattr(args, field, None)
        if spec:
            _parse_range(str(spec), field)

    registry_path = getattr(args, "registry", None)
    rconn = reg.open_registry(registry_path)
    try:
        libs = reg.list_libraries(rconn, only_existing=True)
        # A registered library whose DB is gone -- unmounted drive, file
        # deleted by hand -- was dropped in silence, so a query that should
        # have searched five libraries searched four and said nothing. "(no
        # matches)" and "the drive is not plugged in" are different answers and
        # the user has to be able to tell them apart.
        missing = [r["label"] for r in reg.find_orphans(rconn)]
    finally:
        rconn.close()
    if missing:
        print(f"acidcat lib: {len(missing)} registered librar"
              f"{'y' if len(missing) == 1 else 'ies'} not searched, DB missing: "
              f"{', '.join(sorted(missing))}", file=sys.stderr)

    if not libs:
        # 2: there is nothing to search, so the query could not run at all --
        # the same answer `validate` gives when nothing was checkable.
        print("acidcat lib: no libraries registered. "
              "Run `acidcat lib index DIR --label NAME` first.", file=sys.stderr)
        return 2

    scopes = None
    if args.root:
        scopes = [s.strip() for s in args.root.split(",") if s.strip()]
        libs = _scope_libraries(libs, scopes)
        if not libs:
            print(f"acidcat lib: no library matches --root {args.root!r}",
                  file=sys.stderr)
            return 1

    if getattr(args, "compatible_with", None):
        return _run_compatible(args, libs)

    try:
        rows, total_matched = _fan_out(libs, args)
    except idx.FTSQueryError as e:
        # 2: a malformed --text is the same class of mistake as a malformed
        # --bpm, which already exits 2. It was 1 (ran fine, no answer), so a
        # script could not tell "your syntax is wrong" from "nothing matched".
        print(f"acidcat lib: {e}", file=sys.stderr)
        return 2
    rows.sort(key=lambda r: r.get("path") or "")
    shown = len(rows)
    rows = rows[: args.limit]
    # A truncated answer must say so. `query --format wav` printed 50 paths and
    # nothing else, in every format, so a caller could not tell a complete
    # result from the first page of one -- and the JSON was a bare 50-element
    # array with no marker at all.
    truncated = (total_matched is not None and total_matched > len(rows))         or (total_matched is None and shown > len(rows))

    if not rows:
        # An empty result still has to be VALID output in a machine format:
        # `acidcat query --bpm 128 --json` emitted zero bytes, so `jq` and
        # json.loads both fail on "no matches" -- in a tool whose whole design
        # goal is piping, that turns an ordinary empty answer into a parse
        # error downstream. The human note stays on stderr.
        fmt = getattr(args, "output_format", "table")
        if fmt == "json":
            format_json([], sys.stdout)
        if not getattr(args, "paths_only", False):
            print("(no matches)", file=sys.stderr)
        return 0

    def _note():
        """One line on stderr when the limit hid something.

        stderr, so `--paths-only` stays pipeable and the JSON stays parseable:
        the note is for the human, the records are for the program.
        """
        if not truncated:
            return
        if total_matched is None:
            print(f"acidcat lib: showing {len(rows)}; the total could not be "
                  f"counted (raise --limit to see more)", file=sys.stderr)
        else:
            print(f"acidcat lib: showing {len(rows)} of {total_matched} "
                  f"match(es) -- raise --limit to see more", file=sys.stderr)

    if args.paths_only:
        for r in rows:
            print(r["path"])
        _note()
        return 0

    stream = sys.stdout
    if getattr(args, "output", None):
        stream = open(args.output, "w", encoding="utf-8", newline="")
    try:
        output(rows, fmt=args.output_format, stream=stream)
    finally:
        if stream is not sys.stdout:
            stream.close()
    _note()
    return 0


def _scope_libraries(libs, scopes):
    """Filter libs to those matching any scope (by label or path)."""
    out = []
    for lib in libs:
        for s in scopes:
            if lib["label"] == s:
                out.append(lib)
                break
            if os.path.exists(s):
                norm = acidpaths.compare_path(acidpaths.normalize(s))
                root = acidpaths.compare_path(lib["root_path"])
                if root == norm or root.startswith(norm + "/") \
                        or norm.startswith(root + "/"):
                    out.append(lib)
                    break
    return out


def _run_compatible(args, libs):
    """--compatible-with: resolve the reference's key/BPM/kind, then fan out for
    harmonically- and tempo-compatible samples via core.search."""
    from acidcat.core.catalogue import search
    ref = args.compatible_with
    if not os.path.exists(ref):
        print(f"acidcat lib: --compatible-with file not found: {ref}",
              file=sys.stderr)
        return 1
    row, source, _lib = search.resolve_reference(ref, libs)
    if row is None:
        print(f"acidcat lib: could not read {ref} (index it, or ensure it "
              "carries key/tempo metadata).", file=sys.stderr)
        return 1
    key, bpm = row.get("key"), row.get("bpm")
    if key is None and bpm is None:
        print(f"acidcat lib: {ref} has no key or BPM to match on.",
              file=sys.stderr)
        return 1
    kind = (args.kind or "").lower() or search.infer_kind(row.get("duration"),
                                                          row.get("acid_beats"))
    _vlog(args, f"[query] reference {os.path.basename(ref)}: "
                f"key={key} bpm={bpm} kind={kind} (from {source})")
    rows = search.find_compatible(
        libs, key=key, bpm=bpm, kind=kind,
        bpm_tol=max(0.0, args.bpm_tolerance) / 100.0,
        half_double=not args.no_half_double,
        same_key_only=args.same_key,
        limit=args.limit, exclude_path=ref)
    if not rows:
        if not getattr(args, "paths_only", False):
            print(f"(no compatible samples for key {key or '?'}, "
                  f"{bpm or '?'} bpm)", file=sys.stderr)
        return 0
    if args.paths_only:
        for r in rows:
            print(r["path"])
        return 0
    shaped = [{**{k: r[k] for k in DEFAULT_FIELDS if r.get(k) is not None},
               "compatibility": r.get("compatibility", "")} for r in rows]
    stream = sys.stdout
    if getattr(args, "output", None):
        stream = open(args.output, "w", encoding="utf-8", newline="")
    try:
        output(shaped, fmt=args.output_format, stream=stream)
    finally:
        if stream is not sys.stdout:
            stream.close()
    return 0


def _fan_out(libs, args):
    """Open each library's DB, run the query, accumulate rows, dedup.

    Returns (rows, total_matched). The total costs one extra COUNT per library
    and is what lets the caller say "50 of 382" instead of "50". Without it a
    truncated answer is indistinguishable from a complete one, in every output
    format -- the JSON was a bare 50-element array with nothing marking it as a
    prefix.
    """
    sql, count_sql, params = _build_sql(args)
    per_db_sql = sql + " LIMIT ?"
    per_db_limit = args.limit

    accumulated = []
    seen_paths = set()
    total_matched = 0
    counted_all = True
    skipped_libs = []
    for lib in libs:
        try:
            conn = idx.open_db(lib["db_path"])
        except Exception as e:
            # unreadable, corrupt, or written by a newer acidcat. Named on
            # stderr, not just under -v: a library that silently drops out
            # turns a partial search into one that looks complete.
            skipped_libs.append((lib["label"], str(e)))
            _vlog(args, f"[query] {lib['label']} skipped: {e}")
            continue
        try:
            try:
                rows = conn.execute(
                    per_db_sql, params + [per_db_limit]
                ).fetchall()
            except sqlite3.OperationalError as e:
                # FTS5 metacharacters in --text fail every library
                # identically. Surface once with the shared error
                # message so the user sees the actual problem instead
                # of a silent "(no matches)".
                if args.text and "fts5" in str(e).lower():
                    raise idx.FTSQueryError(
                        idx.fts5_syntax_message(args.text)
                    ) from e
                _vlog(args, f"[query] {lib['label']} query failed: {e}")
                continue
            try:
                # one extra COUNT per library, on the connection already open.
                # A failed count must not lose the rows we did get, so this is
                # its own try: a missing total is a weaker answer than a wrong
                # one, and losing real results to get it would be worse than
                # both.
                total_matched += conn.execute(count_sql, params).fetchone()[0]
            except Exception as e:
                counted_all = False
                _vlog(args, f"[query] {lib['label']} count failed: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
        _vlog(args, f"[query] {lib['label']:<20s} {len(rows)} rows")
        for r in rows:
            d = dict(r)
            p = d.get("path")
            if p in seen_paths:
                continue
            seen_paths.add(p)
            accumulated.append(_shape_row(d))
    for label, why in skipped_libs:
        print(f"acidcat lib: library {label!r} could not be searched: {why}",
              file=sys.stderr)
    return accumulated, (total_matched if counted_all else None)


def _build_sql(args):
    """Build the shared filter SQL via core.query_sql (same builder the MCP
    search tool uses). --bpm/--duration are ranges, split here to min/max."""
    bpm_lo = bpm_hi = dur_lo = dur_hi = None
    if args.bpm:
        bpm_lo, bpm_hi = _parse_range(args.bpm, field_name="bpm")
    if args.duration:
        dur_lo, dur_hi = _parse_range(args.duration, field_name="duration")
    where, params, joins = query_sql.build_filter(
        bpm_min=bpm_lo, bpm_max=bpm_hi,
        duration_min=dur_lo, duration_max=dur_hi,
        key=args.key, file_format=args.file_format,
        device=getattr(args, "device", None),
        category=getattr(args, "category", None),
        creator=getattr(args, "creator", None),
        product=getattr(args, "product", None),
        tags=args.tag, text=args.text)
    return (query_sql.assemble(where, joins, order="s.path"),
            query_sql.assemble_count(where, joins), params)


def _shape_row(row):
    """Project to the default display columns, keeping non-null values only."""
    out = {}
    for k in DEFAULT_FIELDS:
        if k in row and row[k] is not None:
            out[k] = row[k]
    return out


class QueryUsageError(ValueError):
    """A bad filter value. Raised so run() can exit 2 like every other usage
    error, instead of the string SystemExit this used to raise -- that printed
    to stderr, exited 1, and bypassed the CLI's dispatch entirely."""


def _parse_range(spec, field_name="value"):
    """Accept '120', '120:130', ':130', '120:' and return (lo, hi)."""
    if ":" not in spec:
        try:
            v = float(spec)
        except ValueError:
            raise QueryUsageError(f"bad --{field_name} value: {spec}")
        return v, v
    lo_s, hi_s = spec.split(":", 1)
    # the bare branch above was guarded and this one was not, so
    # `--bpm a:b` raised an uncaught ValueError and printed a traceback
    try:
        lo = float(lo_s) if lo_s else None
        hi = float(hi_s) if hi_s else None
    except ValueError:
        raise QueryUsageError(f"bad --{field_name} range: {spec}")
    return lo, hi
