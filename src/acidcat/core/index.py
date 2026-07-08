"""SQLite-backed sample index.

One DB per registered library (central default
~/.acidcat/libraries/<label>_<hash>.db, or <root>/.acidcat/index.db
in-tree); the global registry in core/registry.py tracks them all.
Schema groups immutable audio facts (samples), per-root bookkeeping
(scan_roots), user annotations (tags, descriptions), an FTS5 mirror
(samples_fts), and optional librosa features (features).

Everything here is stdlib-only. Connections are opened with foreign_keys
on and WAL journaling for concurrent readers.
"""

import json
import os
import sqlite3
import struct
import time


SCHEMA_VERSION = 3


# preset-metadata columns added in schema v2 (Bitwig/NI/Vital/etc.)
PRESET_COLUMNS = ("device", "product", "creator", "category", "preset_name")

SAMPLE_COLUMNS = (
    "path", "scan_root", "mtime", "size",
    "format", "duration", "bpm", "key",
    "title", "artist", "album", "genre", "comment",
    "acid_beats", "root_note",
    "sample_rate", "channels", "bits_per_sample",
    "chunks",
    "device", "product", "creator", "category", "preset_name",
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


_CI_INDEX_COLS = ("key", "format", "device", "category", "creator", "product")


def ensure_query_indexes(ex):
    """Create the LOWER()-expression indexes the query layer relies on (its
    predicates wrap these columns in LOWER(), which a plain B-tree index can't
    serve). Idempotent, so it is safe on the write path (walk) for existing DBs,
    which otherwise never get these since _apply_schema returns early when the
    on-disk version already matches. Additive; no schema-version bump. `ex` is a
    cursor or connection."""
    for col in _CI_INDEX_COLS:
        ex.execute(f"CREATE INDEX IF NOT EXISTS idx_samples_{col}_ci "
                   f"ON samples(LOWER({col}))")


def tune_connection(conn):
    """Read-path pragmas for the query/fan-out workload. NORMAL is crash-safe
    under WAL (only risks the last uncommitted txn, never integrity); a larger
    page cache and mmap cut repeated cross-library reads. Cheap, per-connection,
    no on-disk change. Shared so a connection cache can reuse it."""
    try:
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA cache_size = -16000")     # ~16 MB page cache
        conn.execute("PRAGMA mmap_size = 268435456")   # 256 MB
        conn.execute("PRAGMA temp_store = MEMORY")
    except sqlite3.DatabaseError:
        pass


def open_db(path, check_same_thread=True):
    """Open (or create) a DB at path. Applies schema if new. Pass
    check_same_thread=False for a connection that will be shared across threads
    (the long-lived MCP server's connection cache); the caller must then
    serialize use with a lock."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass
    tune_connection(conn)

    _apply_schema(conn)
    return conn


class FTSQueryError(ValueError):
    """Raised when a user-supplied --text contains FTS5 metacharacters
    that the embedded SQLite full-text engine cannot parse.

    SQLite raises a bare `sqlite3.OperationalError: fts5: syntax
    error` for inputs like `foo*bar`, `path:"a"`, or `NOT widget`,
    which leaks internals and gives the user no idea what to escape.
    Callers should translate that OperationalError into this class
    via `fts5_syntax_message` and surface the result.
    """


def fts5_syntax_message(text):
    """Return a human-readable explanation of an FTS5 syntax error
    for the given user-supplied text. Same wording across the CLI
    (commands/query.py) and the MCP server so a user sees the same
    message regardless of which surface they hit.
    """
    return (
        f"invalid search text: {text!r}. "
        f"FTS5 special chars (* \" ( ) NOT AND OR) need to be "
        f"quoted as a literal phrase."
    )


def escape_like(s):
    """Escape SQLite LIKE metacharacters in user-supplied fragments.

    LIKE treats `_` as "any single character" and `%` as "any
    sequence", so `kick_126.wav` would also match `kickX126.wav`.
    Pair with `ESCAPE '\\'` on the SQL side.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
    _migrate(conn, cur, on_disk)


def _migrate(conn, cur, on_disk):
    """Forward-only schema migration, made interruption-safe. The whole step runs
    in one explicit transaction that rolls back on error, and each ADD COLUMN is
    guarded against a pre-existing column, so a migration killed midway cannot
    wedge the DB with 'duplicate column name' on the next open (the columns from a
    rolled-back attempt are gone, and the guard covers a legacy partial state)."""
    try:
        cur.execute("BEGIN")
        if on_disk < 2:
            # add the preset-metadata columns and widen the FTS to cover them.
            have = {r["name"] for r in cur.execute("PRAGMA table_info(samples)")}
            for col in PRESET_COLUMNS:
                if col not in have:
                    cur.execute(f"ALTER TABLE samples ADD COLUMN {col} TEXT")
            cur.execute("DROP TABLE IF EXISTS samples_fts")
            cur.execute(_SAMPLES_FTS_DDL)
            # FTS is repopulated by the v3 step below (which re-keys it to
            # samples.id), so no per-path rebuild here: at this point samples has
            # no id column yet, and rebuild_fts_for_path now keys on it.
        if on_disk < 3:
            _migrate_v3(conn, cur)
        cur.execute(
            "UPDATE meta SET v = ? WHERE k = 'schema_version'",
            (str(SCHEMA_VERSION),))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate_v3(conn, cur):
    """v2 -> v3: give `samples` an explicit `id INTEGER PRIMARY KEY` and re-key the
    FTS mirror to it. Before this, samples was `path TEXT PRIMARY KEY` (a plain
    rowid table) and samples_fts was refreshed with `DELETE ... WHERE path = ?`;
    path is an FTS *column*, not the rowid, so that delete scanned the whole FTS
    index for a match, making a full --force rebuild O(n^2). With id as a stable
    rowid alias (VACUUM-safe, unlike an implicit rowid), the FTS row keys on
    rowid = samples.id and the per-path refresh deletes by rowid in O(log n).

    The samples table is rebuilt (you cannot ALTER a column into the primary key),
    so this is the expensive step of the migration: one full copy of the table.
    It runs inside the caller's single transaction, so an interruption rolls back
    cleanly and re-opens at v2."""
    cols = ",".join(SAMPLE_COLUMNS)
    cur.execute(f"CREATE TABLE samples_new (\n{_SAMPLES_TABLE_COLS}\n)")
    cur.execute(f"INSERT INTO samples_new ({cols}) SELECT {cols} FROM samples")
    cur.execute("DROP TABLE samples")
    cur.execute("ALTER TABLE samples_new RENAME TO samples")
    _create_sample_indexes(cur)
    # rowid must now equal samples.id, so rebuild the FTS mirror from scratch.
    cur.execute("DROP TABLE IF EXISTS samples_fts")
    cur.execute(_SAMPLES_FTS_DDL)
    for r in cur.execute("SELECT path FROM samples").fetchall():
        rebuild_fts_for_path(conn, r["path"])
    # features gains a packed float32 similarity vector; backfill it from the
    # existing JSON (parse once, no librosa re-extraction) so old rows are
    # searchable immediately. ADD COLUMN is guarded for a legacy partial state.
    from acidcat.core import features as feat
    cur.execute(
        "CREATE TABLE IF NOT EXISTS features (path TEXT PRIMARY KEY, "
        "features_json TEXT, feature_vec BLOB, features_version INTEGER, "
        "extracted_at REAL)")
    have = {r["name"] for r in cur.execute("PRAGMA table_info(features)")}
    if "feature_vec" not in have:
        cur.execute("ALTER TABLE features ADD COLUMN feature_vec BLOB")
    for r in cur.execute(
            "SELECT path, features_json FROM features "
            "WHERE feature_vec IS NULL AND features_json IS NOT NULL").fetchall():
        try:
            vec = feat.vector_from_features(json.loads(r["features_json"]))
        except (ValueError, TypeError):
            continue
        blob = pack_vector(vec)
        if blob is not None:
            cur.execute("UPDATE features SET feature_vec = ? WHERE path = ?",
                        (blob, r["path"]))


_SAMPLES_FTS_DDL = """
    CREATE VIRTUAL TABLE IF NOT EXISTS samples_fts USING fts5(
        path, title, artist, album, genre, comment, description, tags,
        preset_name, device, product, creator, category,
        tokenize='porter'
    )
"""

# column definitions shared by fresh creation and the v3 table rebuild. `id` is
# an explicit INTEGER PRIMARY KEY (a rowid alias that VACUUM keeps stable) so the
# FTS mirror can key on it; path keeps its uniqueness for upsert ON CONFLICT.
_SAMPLES_TABLE_COLS = """
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
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
    device TEXT,
    product TEXT,
    creator TEXT,
    category TEXT,
    preset_name TEXT,
    indexed_at REAL,
    last_seen_at REAL
"""


def _create_sample_indexes(cur):
    """(Re)create every index on `samples`. Shared by fresh creation and the v3
    rebuild, which drops the table and must restore its indexes."""
    for col in ("bpm", "key", "duration", "format", "scan_root",
                "device", "category", "creator", "product"):
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_samples_{col} "
                    f"ON samples({col})")
    ensure_query_indexes(cur)


def _create_tables(cur):
    cur.execute(f"CREATE TABLE IF NOT EXISTS samples (\n{_SAMPLES_TABLE_COLS}\n)")
    _create_sample_indexes(cur)

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

    cur.execute(_SAMPLES_FTS_DDL)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS features (
            path TEXT PRIMARY KEY,
            features_json TEXT,
            feature_vec BLOB,          -- packed float32 similarity vector (schema v3)
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


def pack_vector(vec):
    """Pack a list of floats into a little-endian float32 BLOB (stdlib struct, so
    this stays importable without numpy). Returns None for an empty/None vec."""
    if not vec:
        return None
    return struct.pack("<%df" % len(vec), *(float(x) for x in vec))


def unpack_vector(blob, dims=None):
    """Inverse of pack_vector: little-endian float32 BLOB -> list of floats
    (stdlib only). Returns None for an empty blob or a length mismatch against
    `dims`, so a stale-dimension vector is skipped rather than mis-scored."""
    if not blob:
        return None
    n = len(blob) // 4
    if n == 0 or (dims is not None and n != dims):
        return None
    return list(struct.unpack("<%df" % n, blob))


def upsert_features(conn, path, features, version=1):
    """Store librosa features as a JSON blob plus the packed similarity vector
    (core.features.FEATURE_KEYS order). The JSON keeps the full dict for display
    and re-derivation; the BLOB is what find_similar unpacks for fast, numpy
    vectorized scoring."""
    from acidcat.core import features as feat
    payload = json.dumps(features, default=str)
    vec_blob = pack_vector(feat.vector_from_features(features))
    conn.execute(
        "INSERT INTO features (path, features_json, feature_vec, features_version, "
        "extracted_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET features_json=excluded.features_json, "
        "feature_vec=excluded.feature_vec, features_version=excluded.features_version, "
        "extracted_at=excluded.extracted_at",
        (path, payload, vec_blob, version, time.time()),
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

    Caller owns the transaction. Do NOT wrap this body in `with conn:` --
    Python's sqlite3 connection context manager commits the active
    transaction on normal exit, which defeated the deliberate
    `_COMMIT_EVERY_N_FILES` batching in _walk_and_upsert (every indexed
    file paid a full commit + fsync).

    Atomicity: callers that need the DELETE + INSERT pair to land
    together with the samples row that triggered the rebuild already
    open their own transaction boundary (or rely on the implicit
    transaction from the preceding upsert). If the samples row is
    missing we leave the FTS row deleted, which is the correct end
    state.

    The FTS row keys on rowid = samples.id, so the refresh deletes by rowid (a
    single index lookup) instead of scanning the FTS index for a path-column
    match. Callers that delete a sample outright (remove_root, prune_missing)
    delete the FTS row by that same id themselves, since once the samples row is
    gone we can no longer map its path back to an id here.
    """
    sample = conn.execute(
        "SELECT id, title, artist, album, genre, comment, "
        "preset_name, device, product, creator, category "
        "FROM samples WHERE path = ?",
        (path,),
    ).fetchone()
    if sample is None:
        return
    sid = sample["id"]
    conn.execute("DELETE FROM samples_fts WHERE rowid = ?", (sid,))
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
        "(rowid, path, title, artist, album, genre, comment, description, tags, "
        "preset_name, device, product, creator, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sid,
            path,
            sample["title"] or "",
            sample["artist"] or "",
            sample["album"] or "",
            sample["genre"] or "",
            sample["comment"] or "",
            description or "",
            tags_text,
            sample["preset_name"] or "",
            sample["device"] or "",
            sample["product"] or "",
            sample["creator"] or "",
            sample["category"] or "",
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
    like = escape_like(root.rstrip("/")) + "/%"
    rows = conn.execute(
        "SELECT id, path FROM samples WHERE scan_root = ? "
        "OR path LIKE ? ESCAPE '\\'",
        (root, like),
    ).fetchall()
    for r in rows:
        conn.execute("DELETE FROM samples_fts WHERE rowid = ?", (r["id"],))
        conn.execute("DELETE FROM samples WHERE id = ?", (r["id"],))
        conn.execute("DELETE FROM tags WHERE path = ?", (r["path"],))
        conn.execute("DELETE FROM descriptions WHERE path = ?", (r["path"],))
        conn.execute("DELETE FROM features WHERE path = ?", (r["path"],))
    conn.execute("DELETE FROM scan_roots WHERE path = ?", (root,))
    return len(rows)


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
    rows = conn.execute(
        "SELECT id, path FROM samples WHERE scan_root = ? AND "
        "(last_seen_at IS NULL OR last_seen_at < ?)",
        (scan_root, before_ts),
    ).fetchall()
    for r in rows:
        conn.execute("DELETE FROM samples_fts WHERE rowid = ?", (r["id"],))
        conn.execute("DELETE FROM samples WHERE id = ?", (r["id"],))
        conn.execute("DELETE FROM tags WHERE path = ?", (r["path"],))
        conn.execute("DELETE FROM descriptions WHERE path = ?", (r["path"],))
        conn.execute("DELETE FROM features WHERE path = ?", (r["path"],))
    return len(rows)


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
