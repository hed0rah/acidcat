"""Shared filter-SQL builder for the sample index, used by both `acidcat query`
(CLI) and the MCP search_samples tool so the WHERE/JOIN logic lives once instead
of drifting in two copies. Returns fragments; each caller assembles the final
SELECT + ORDER BY / LIMIT and does its own FTS-error translation (the CLI raises
FTSQueryError, the MCP raises ToolError).

All user values are bound parameters. The only interpolated text is column names,
drawn from a fixed set here, never from user input.
"""


# every SELECT carries the file's pitch (core/analysis/pitch.py) when it has
# one. the join is on the pitch table's primary key, so SQLite drops it from a
# query that never reads a pitch column.
PITCH_JOIN = "LEFT JOIN pitch p ON p.path = s.path"
PITCH_COLUMNS = "p.pitch_hz, p.pitch_note, p.pitch_cents, p.tunability, p.glide_cents"


def build_filter(*, bpm_min=None, bpm_max=None, duration_min=None,
                 duration_max=None, key=None, file_format=None, device=None,
                 category=None, creator=None, product=None, tags=(), text=None,
                 note=None, tunability_min=None, tunability_max=None):
    """Return (where_clauses, params, joins) for the given filters.

    `note` is "F#" (any octave) or "F#1" (that MIDI note); ValueError when it
    is neither."""
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
        # LOWER() on both sides, like every scalar filter above. Exact `tag IN
        # (?)` made tags the one case-SENSITIVE filter in the builder, so
        # `--tag wavetable` returned 0 rows against 85 stored as 'Wavetable' --
        # a confident empty answer with no hint that case was the reason, and
        # the same builder backs the MCP search tool, so a model hit it too.
        # COUNT(DISTINCT LOWER(tag)) so two casings of one tag cannot satisfy
        # a two-tag AND on their own.
        ph = ",".join("?" for _ in tags)
        where.append(f"s.path IN (SELECT path FROM tags "
                     f"WHERE LOWER(tag) IN ({ph}) "
                     f"GROUP BY path HAVING COUNT(DISTINCT LOWER(tag)) = ?)")
        params.extend(t.lower() for t in tags)
        params.append(len({t.lower() for t in tags}))

    if note:
        from acidcat.core.analysis.pitch import parse_note
        parsed = parse_note(note)
        if parsed is None:
            raise ValueError(f"not a note: {note!r} (want e.g. F#, Bb, C2)")
        pc, midi = parsed
        if midi is None:
            where.append("p.pitch_note % 12 = ?")
            params.append(pc)
        else:
            where.append("p.pitch_note = ?")
            params.append(midi)
    if tunability_min is not None:
        where.append("p.tunability >= ?")
        params.append(float(tunability_min))
    if tunability_max is not None:
        where.append("p.tunability <= ?")
        params.append(float(tunability_max))

    if text:
        joins.append("JOIN samples_fts fts ON fts.path = s.path")
        where.append("samples_fts MATCH ?")
        params.append(text)

    return where, params, joins


def assemble_count(where, joins):
    """How many rows the same filter matches, unbounded.

    A caller that applies LIMIT has to be able to say "50 of 382" rather than
    just "50", and it cannot get 382 from a result set it truncated. No ORDER BY
    (sorting a count is wasted work) and no LIMIT (the count IS the thing the
    limit hides).

    Summing this across libraries is exact rather than approximate: the registry
    refuses to register a library whose root overlaps another, so a given file
    belongs to exactly one library and cannot be counted twice.
    """
    sql = f"SELECT COUNT(*) FROM samples s {PITCH_JOIN} " + " ".join(joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql


def assemble(where, joins, *, order="s.path", limit_placeholder=False):
    """Assemble a full SELECT from build_filter fragments."""
    sql = (f"SELECT s.*, {PITCH_COLUMNS} FROM samples s {PITCH_JOIN} "
           + " ".join(joins))
    if where:
        sql += " WHERE " + " AND ".join(where)
    if order:
        sql += f" ORDER BY {order}"
    if limit_placeholder:
        sql += " LIMIT ?"
    return sql
