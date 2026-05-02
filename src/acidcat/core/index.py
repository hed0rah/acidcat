"""SQLite-backed sample index.

Single global DB at ~/.acidcat/index.db holds metadata for every sample
the user has pointed `acidcat index` at. Schema groups immutable audio
facts (samples), per-root bookkeeping (scan_roots), user annotations
(tags, descriptions), an FTS5 mirror (samples_fts), and optional librosa
features (features).

Everything here is stdlib-only. Connections are opened with foreign_keys
on and WAL journaling for concurrent readers.
"""

import json
import os
import sqlite3
import time


SCHEMA_VERSION = 1


SAMPLE_COLUMNS = (
    "path", "scan_root", "mtime", "size",
    "format", "duration", "bpm", "key",
    "title", "artist", "album", "genre", "comment",
    "acid_beats", "root_note",
    "sample_rate", "channels", "bits_per_sample",
    "chunks",
    "indexed_at", "last_seen_at",
)


def default_db_path():
    """Return ~/.acidcat/index.db (works on Windows/macOS/Linux)."""
    return os.path.join(os.path.expanduser("~"), ".acidcat", "index.db")


def resolve_db_path(cli_value=None):
    """Resolve DB path from --db, ACIDCAT_DB env, or default."""
    if cli_value:
        return cli_value
    env = os.environ.get("ACIDCAT_DB")
    if env:
        return env
    return default_db_path()


def open_db(path):
    """Open (or create) a DB at path. Applies schema if new."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass

    _apply_schema(conn)
    return conn


class SchemaVersionError(RuntimeError):
    """Raised when an existing per-library DB has a schema version we
    do not know how to read.

    Forward incompatibility: client at version N opens a DB written by
    a future version N+1 with new columns or tables. We refuse to touch
    it rather than running old SQL against a new schema and producing
    silent corruption.
    """


def _apply_schema(conn):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            k TEXT PRIMARY KEY,
            v TEXT
        )
    """)

    row = cur.execute("SELECT v FROM meta WHERE k = 'schema_version'").fetchone()
    if row is None:
        _create_tables(cur)
        cur.execute(
            "INSERT INTO meta (k, v) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
        return

    try:
        on_disk = int(row["v"])
    except (TypeError, ValueError):
        raise SchemaVersionError(
            f"per-library DB has unparseable schema_version {row['v']!r}; "
            f"refusing to open."
        )
    if on_disk == SCHEMA_VERSION:
        return
    if on_disk > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"per-library DB has schema_version {on_disk}, but this "
            f"acidcat build only knows version {SCHEMA_VERSION}. Upgrade "
            f"acidcat or open the DB with a newer client."
        )
    # on_disk < SCHEMA_VERSION: a real migration registry would dispatch
    # by version here. Today the only known version is 1 so this branch
    # is unreachable; keeping the error explicit so a future bump
    # without a migration registers as a clean failure rather than a
    # silent run of old SQL against an old schema.
    raise SchemaVersionError(
        f"per-library DB at schema_version {on_disk} needs migration to "
        f"{SCHEMA_VERSION}; no migration registered."
    )


def _create_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            path TEXT PRIMARY KEY,
            scan_root TEXT,
            mtime REAL,
            size INTEGER,
            format TEXT,
            duration REAL,
            bpm REAL,
            key TEXT,
            title TEXT,
            artist TEXT,
            album TEXT,
            genre TEXT,
            comment TEXT,
            acid_beats INTEGER,
            root_note INTEGER,
            sample_rate INTEGER,
            channels INTEGER,
            bits_per_sample INTEGER,
            chunks TEXT,
            indexed_at REAL,
            last_seen_at REAL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_bpm ON samples(bpm)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_key ON samples(key)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_duration ON samples(duration)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_format ON samples(format)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_scan_root ON samples(scan_root)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS scan_roots (
            path TEXT PRIMARY KEY,
            added_at REAL,
            last_indexed_at REAL,
            file_count INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            path TEXT,
            tag TEXT,
            PRIMARY KEY (path, tag)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS descriptions (
            path TEXT PRIMARY KEY,
            description TEXT
        )
    """)

    cur.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS samples_fts USING fts5(
            path, title, artist, album, genre, comment, description, tags,
            tokenize='porter'
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS features (
            path TEXT PRIMARY KEY,
            features_json TEXT,
            features_version INTEGER,
            extracted_at REAL
        )
    """)


def normalize_path(p):
    """Canonical path form for DB keys."""
    return os.path.abspath(p).replace("\\", "/")


def get_sample_stat(conn, path):
    """Return (mtime, size) stored for path, or None if not indexed."""
    row = conn.execute(
        "SELECT mtime, size FROM samples WHERE path = ?", (path,)
    ).fetchone()
    if row is None:
        return None
    return (row["mtime"], row["size"])


def upsert_sample(conn, row):
    """Insert or update a sample. `row` is a dict keyed by SAMPLE_COLUMNS.

    Missing keys default to None. `path` is required.
    """
    values = [row.get(col) for col in SAMPLE_COLUMNS]
    placeholders = ",".join("?" for _ in SAMPLE_COLUMNS)
    cols = ",".join(SAMPLE_COLUMNS)
    updates = ",".join(f"{c}=excluded.{c}" for c in SAMPLE_COLUMNS if c != "path")
    conn.execute(
        f"INSERT INTO samples ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(path) DO UPDATE SET {updates}",
        values,
    )
    rebuild_fts_for_path(conn, row["path"])


def touch_last_seen(conn, path, ts):
    """Stamp last_seen_at without re-parsing the file."""
    conn.execute(
        "UPDATE samples SET last_seen_at = ? WHERE path = ?",
        (ts, path),
    )


def upsert_tags(conn, path, tags, replace=False):
    """Add tags to a sample. If replace=True, wipes existing tags first."""
    if replace:
        conn.execute("DELETE FROM tags WHERE path = ?", (path,))
    for t in tags:
        t = t.strip()
        if not t:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO tags (path, tag) VALUES (?, ?)",
            (path, t),
        )
    rebuild_fts_for_path(conn, path)


def remove_tags(conn, path, tags):
    for t in tags:
        conn.execute("DELETE FROM tags WHERE path = ? AND tag = ?", (path, t))
    rebuild_fts_for_path(conn, path)


def upsert_description(conn, path, description):
    if description is None or description == "":
        conn.execute("DELETE FROM descriptions WHERE path = ?", (path,))
    else:
        conn.execute(
            "INSERT INTO descriptions (path, description) VALUES (?, ?) "
            "ON CONFLICT(path) DO UPDATE SET description=excluded.description",
            (path, description),
        )
    rebuild_fts_for_path(conn, path)


def upsert_features(conn, path, features, version=1):
    """Store librosa features as JSON blob."""
    payload = json.dumps(features, default=str)
    conn.execute(
        "INSERT INTO features (path, features_json, features_version, extracted_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET features_json=excluded.features_json, "
        "features_version=excluded.features_version, extracted_at=excluded.extracted_at",
        (path, payload, version, time.time()),
    )


def get_features(conn, path):
    row = conn.execute(
        "SELECT features_json FROM features WHERE path = ?", (path,)
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["features_json"])


def rebuild_fts_for_path(conn, path):
    """Refresh the FTS row for a single path.

    Wrapped in an explicit transaction so the DELETE + INSERT pair is
    atomic: an early return on missing samples row, or any failure in
    the read steps, rolls back the DELETE rather than leaving the FTS
    table missing a row whose samples row still exists.
    """
    with conn:
        conn.execute("DELETE FROM samples_fts WHERE path = ?", (path,))
        sample = conn.execute(
            "SELECT title, artist, album, genre, comment "
            "FROM samples WHERE path = ?",
            (path,),
        ).fetchone()
        if sample is None:
            # samples row gone: leave the FTS row deleted (correct end
            # state) and roll back nothing since DELETE already aligned
            # the two tables.
            return
        desc_row = conn.execute(
            "SELECT description FROM descriptions WHERE path = ?", (path,)
        ).fetchone()
        description = desc_row["description"] if desc_row else None
        tag_rows = conn.execute(
            "SELECT tag FROM tags WHERE path = ?", (path,)
        ).fetchall()
        tags_text = " ".join(r["tag"] for r in tag_rows)
        conn.execute(
            "INSERT INTO samples_fts "
            "(path, title, artist, album, genre, comment, description, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                path,
                sample["title"] or "",
                sample["artist"] or "",
                sample["album"] or "",
                sample["genre"] or "",
                sample["comment"] or "",
                description or "",
                tags_text,
            ),
        )


def record_scan_root(conn, path, file_count, indexed_at):
    """Insert or update a scan_roots entry."""
    existing = conn.execute(
        "SELECT path FROM scan_roots WHERE path = ?", (path,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE scan_roots SET last_indexed_at = ?, file_count = ? WHERE path = ?",
            (indexed_at, file_count, path),
        )
    else:
        conn.execute(
            "INSERT INTO scan_roots (path, added_at, last_indexed_at, file_count) "
            "VALUES (?, ?, ?, ?)",
            (path, indexed_at, indexed_at, file_count),
        )


def list_roots(conn):
    rows = conn.execute(
        "SELECT path, added_at, last_indexed_at, file_count FROM scan_roots "
        "ORDER BY path"
    ).fetchall()
    return [dict(r) for r in rows]


def remove_root(conn, root):
    """Delete all samples under root and drop the scan_roots entry."""
    like = root.rstrip("/") + "/%"
    paths = [
        r["path"] for r in conn.execute(
            "SELECT path FROM samples WHERE scan_root = ? OR path LIKE ?",
            (root, like),
        ).fetchall()
    ]
    for p in paths:
        conn.execute("DELETE FROM samples WHERE path = ?", (p,))
        conn.execute("DELETE FROM tags WHERE path = ?", (p,))
        conn.execute("DELETE FROM descriptions WHERE path = ?", (p,))
        conn.execute("DELETE FROM features WHERE path = ?", (p,))
        conn.execute("DELETE FROM samples_fts WHERE path = ?", (p,))
    conn.execute("DELETE FROM scan_roots WHERE path = ?", (root,))
    return len(paths)


def prune_missing(conn, scan_root, before_ts):
    """Remove rows under scan_root whose last_seen_at is older than before_ts.

    Timing model: callers pass before_ts = walk_start (the timestamp at
    the beginning of the walk). Files added to disk after the walk
    started but before this prune runs will appear in samples (because
    a later upsert touched them) but with last_seen_at >= before_ts, so
    they are NOT pruned. Conversely, a file that was never visited
    during the walk because of a symlink loop, permission error, or
    skipped junk filter will keep its old last_seen_at and IS pruned.
    Re-running the index recovers any wrongly-pruned file.
    """
    paths = [
        r["path"] for r in conn.execute(
            "SELECT path FROM samples WHERE scan_root = ? AND "
            "(last_seen_at IS NULL OR last_seen_at < ?)",
            (scan_root, before_ts),
        ).fetchall()
    ]
    for p in paths:
        conn.execute("DELETE FROM samples WHERE path = ?", (p,))
        conn.execute("DELETE FROM tags WHERE path = ?", (p,))
        conn.execute("DELETE FROM descriptions WHERE path = ?", (p,))
        conn.execute("DELETE FROM features WHERE path = ?", (p,))
        conn.execute("DELETE FROM samples_fts WHERE path = ?", (p,))
    return len(paths)


def index_stats(conn):
    """Summary counts for `acidcat index --stats` / MCP index_stats."""
    total = conn.execute("SELECT COUNT(*) AS c FROM samples").fetchone()["c"]
    by_format = [
        dict(r) for r in conn.execute(
            "SELECT format, COUNT(*) AS count FROM samples "
            "GROUP BY format ORDER BY count DESC"
        ).fetchall()
    ]
    feat_count = conn.execute("SELECT COUNT(*) AS c FROM features").fetchone()["c"]
    tag_count = conn.execute(
        "SELECT COUNT(DISTINCT tag) AS c FROM tags"
    ).fetchone()["c"]
    desc_count = conn.execute(
        "SELECT COUNT(*) AS c FROM descriptions"
    ).fetchone()["c"]
    last_root = conn.execute(
        "SELECT MAX(last_indexed_at) AS t FROM scan_roots"
    ).fetchone()["t"]
    return {
        "total_samples": total,
        "with_features": feat_count,
        "unique_tags": tag_count,
        "with_descriptions": desc_count,
        "last_indexed_at": last_root,
        "by_format": by_format,
        "roots": list_roots(conn),
    }
