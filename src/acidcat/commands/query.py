"""
acidcat query -- filter samples across all registered libraries.

Default behavior is to fan out across every library in the registry,
merge results, dedup by path, and apply a final LIMIT. Use --root to
scope to one or more libraries by label or path. Override the registry
location with --registry or the ACIDCAT_REGISTRY env var.
"""

import os
import sys

from acidcat.core import index as idx
from acidcat.core import paths as acidpaths
from acidcat.core import registry as reg
from acidcat.core.formats import output


DEFAULT_FIELDS = [
    "path", "format", "bpm", "key", "duration",
    "title", "artist", "album", "scan_root",
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
    p.add_argument("--text", help="Full-text search across title/artist/album/"
                   "genre/comment/description/tags/path.")
    p.add_argument("--root",
                   help="Scope results to one or more libraries (label or "
                        "path). Comma-separate to query multiple libraries.")
    p.add_argument("--limit", type=int, default=50, help="Max rows (default 50).")
    p.add_argument("-f", "--output-format", dest="output_format",
                   default="table", choices=["table", "json", "csv"],
                   help="Output format (default: table).")
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
    registry_path = getattr(args, "registry", None)
    rconn = reg.open_registry(registry_path)
    try:
        libs = reg.list_libraries(rconn, only_existing=True)
    finally:
        rconn.close()

    if not libs:
        print("acidcat query: no libraries registered. "
              "Run `acidcat index DIR --label NAME` first.", file=sys.stderr)
        return 1

    scopes = None
    if args.root:
        scopes = [s.strip() for s in args.root.split(",") if s.strip()]
        libs = _scope_libraries(libs, scopes)
        if not libs:
            print(f"acidcat query: no library matches --root {args.root!r}",
                  file=sys.stderr)
            return 1

    rows = _fan_out(libs, args)
    rows.sort(key=lambda r: r.get("path") or "")
    rows = rows[: args.limit]

    if not rows:
        if not getattr(args, "paths_only", False):
            print("(no matches)", file=sys.stderr)
        return 0

    if args.paths_only:
        for r in rows:
            print(r["path"])
        return 0

    stream = sys.stdout
    if getattr(args, "output", None):
        stream = open(args.output, "w", encoding="utf-8", newline="")
    try:
        output(rows, fmt=args.output_format, stream=stream)
    finally:
        if stream is not sys.stdout:
            stream.close()
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
                norm = acidpaths.normalize(s)
                root = lib["root_path"]
                if root == norm or root.startswith(norm + "/") \
                        or norm.startswith(root + "/"):
                    out.append(lib)
                    break
    return out


def _fan_out(libs, args):
    """Open each library's DB, run the query, accumulate rows, dedup."""
    sql, params = _build_sql(args)
    per_db_sql = sql + " LIMIT ?"
    per_db_limit = args.limit

    accumulated = []
    seen_paths = set()
    for lib in libs:
        try:
            conn = idx.open_db(lib["db_path"])
        except Exception as e:
            _vlog(args, f"[query] {lib['label']} skipped: {e}")
            continue
        try:
            rows = conn.execute(per_db_sql, params + [per_db_limit]).fetchall()
        except Exception as e:
            _vlog(args, f"[query] {lib['label']} query failed: {e}")
            conn.close()
            continue
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
    return accumulated


def _build_sql(args):
    """Build the WHERE clause shared across every library DB."""
    where = []
    params = []
    joins = []

    if args.bpm:
        lo, hi = _parse_range(args.bpm, field_name="bpm")
        if lo is not None:
            where.append("s.bpm >= ?")
            params.append(lo)
        if hi is not None:
            where.append("s.bpm <= ?")
            params.append(hi)

    if args.duration:
        lo, hi = _parse_range(args.duration, field_name="duration")
        if lo is not None:
            where.append("s.duration >= ?")
            params.append(lo)
        if hi is not None:
            where.append("s.duration <= ?")
            params.append(hi)

    if args.key:
        where.append("LOWER(s.key) = LOWER(?)")
        params.append(args.key)

    if args.file_format:
        where.append("LOWER(s.format) = LOWER(?)")
        params.append(args.file_format)

    tags = [t for t in (args.tag or []) if t]
    if tags:
        placeholders = ",".join("?" for _ in tags)
        where.append(
            f"s.path IN ("
            f"  SELECT path FROM tags WHERE tag IN ({placeholders}) "
            f"  GROUP BY path HAVING COUNT(DISTINCT tag) = ?"
            f")"
        )
        params.extend(tags)
        params.append(len(tags))

    if args.text:
        joins.append("JOIN samples_fts fts ON fts.path = s.path")
        where.append("samples_fts MATCH ?")
        params.append(args.text)

    sql = "SELECT s.* FROM samples s " + " ".join(joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.path"
    return sql, params


def _shape_row(row):
    """Project to the default display columns, keeping non-null values only."""
    out = {}
    for k in DEFAULT_FIELDS:
        if k in row and row[k] is not None:
            out[k] = row[k]
    return out


def _parse_range(spec, field_name="value"):
    """Accept '120', '120:130', ':130', '120:' and return (lo, hi)."""
    if ":" not in spec:
        try:
            v = float(spec)
        except ValueError:
            raise SystemExit(f"acidcat query: bad --{field_name} value: {spec}")
        return v, v
    lo_s, hi_s = spec.split(":", 1)
    lo = float(lo_s) if lo_s else None
    hi = float(hi_s) if hi_s else None
    return lo, hi
