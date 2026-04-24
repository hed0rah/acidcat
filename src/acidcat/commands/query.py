"""
acidcat query -- filter samples from the global SQLite index.
"""

import os
import sys

from acidcat.core import index as idx
from acidcat.core.formats import output


DEFAULT_FIELDS = [
    "path", "format", "bpm", "key", "duration",
    "title", "artist", "album", "scan_root",
]


def register(subparsers):
    p = subparsers.add_parser(
        "query",
        help="Filter the global sample index.",
    )
    p.add_argument("--db", help="Override DB path (default: ~/.acidcat/index.db).")
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
    p.add_argument("--root", help="Scope results to samples under this scan root.")
    p.add_argument("--limit", type=int, default=50, help="Max rows (default 50).")
    p.add_argument("-f", "--output-format", dest="output_format",
                   default="table", choices=["table", "json", "csv"],
                   help="Output format (default: table).")
    p.add_argument("-o", "--output", help="Write output to file.")
    p.add_argument("--paths-only", action="store_true",
                   help="Print bare paths, one per line.")
    p.set_defaults(func=run)


def run(args):
    db_path = idx.resolve_db_path(getattr(args, "db", None))
    if not os.path.isfile(db_path):
        print(f"acidcat query: no index at {db_path}. "
              f"Run `acidcat index DIR` first.", file=sys.stderr)
        return 1

    conn = idx.open_db(db_path)
    try:
        rows = _run_query(conn, args)
    finally:
        conn.close()

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


def _run_query(conn, args):
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

    if args.root:
        root = idx.normalize_path(args.root)
        where.append("(s.scan_root = ? OR s.path LIKE ?)")
        params.append(root)
        params.append(root.rstrip("/") + "/%")

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
    sql += " ORDER BY s.path LIMIT ?"
    params.append(args.limit)

    rows = conn.execute(sql, params).fetchall()
    return [_shape_row(dict(r)) for r in rows]


def _shape_row(row):
    """Project to the default display columns, keeping key data."""
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
