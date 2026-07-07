"""Shared filter-SQL builder for the sample index, used by both `acidcat query`
(CLI) and the MCP search_samples tool so the WHERE/JOIN logic lives once instead
of drifting in two copies. Returns fragments; each caller assembles the final
SELECT + ORDER BY / LIMIT and does its own FTS-error translation (the CLI raises
FTSQueryError, the MCP raises ToolError).

All user values are bound parameters. The only interpolated text is column names,
drawn from a fixed set here, never from user input.
"""


def build_filter(*, bpm_min=None, bpm_max=None, duration_min=None,
                 duration_max=None, key=None, file_format=None, device=None,
                 category=None, creator=None, product=None, tags=(), text=None):
    """Return (where_clauses, params, joins) for the given filters."""
    where, params, joins = [], [], []

    if bpm_min is not None:
        where.append("s.bpm >= ?")
        params.append(float(bpm_min))
    if bpm_max is not None:
        where.append("s.bpm <= ?")
        params.append(float(bpm_max))
    if duration_min is not None:
        where.append("s.duration >= ?")
        params.append(float(duration_min))
    if duration_max is not None:
        where.append("s.duration <= ?")
        params.append(float(duration_max))

    # case-insensitive equality; hits the LOWER()-expression indexes (see
    # core/index.py:ensure_query_indexes). column names are from this fixed list.
    for col, val in (("key", key), ("format", file_format), ("device", device),
                     ("category", category), ("creator", creator),
                     ("product", product)):
        if val:
            where.append(f"LOWER(s.{col}) = LOWER(?)")
            params.append(val)

    tags = [t for t in (tags or []) if t]
    if tags:
        ph = ",".join("?" for _ in tags)
        where.append(f"s.path IN (SELECT path FROM tags WHERE tag IN ({ph}) "
                     f"GROUP BY path HAVING COUNT(DISTINCT tag) = ?)")
        params.extend(tags)
        params.append(len(tags))

    if text:
        joins.append("JOIN samples_fts fts ON fts.path = s.path")
        where.append("samples_fts MATCH ?")
        params.append(text)

    return where, params, joins


def assemble(where, joins, *, order="s.path", limit_placeholder=False):
    """Assemble a full SELECT from build_filter fragments."""
    sql = "SELECT s.* FROM samples s " + " ".join(joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    if order:
        sql += f" ORDER BY {order}"
    if limit_placeholder:
        sql += " LIMIT ?"
    return sql
