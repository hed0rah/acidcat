"""Global registry of acidcat libraries.

The registry is a small SQLite at `~/.acidcat/registry.db` (overridable
via `--registry` or `ACIDCAT_REGISTRY`). It tracks every known library:
its root path, its db path, a human label, the storage mode (in-tree or
central), and cached counts so `acidcat index --list` is fast without
opening every per-lib DB.

Policy decisions baked into this module:

- `label` is mandatory. Auto-derive from `os.path.basename(root)` at the
  caller; this module enforces NOT NULL.
- No nested libraries. `register_library` rejects a root that is `==`,
  parent of, or child of any already-registered root. Avoids dedup
  ambiguity at query time.
- The registry never writes to a per-lib DB. It only stores pointers and
  cached counts. Callers update counts via `update_stats` after each
  index walk.
"""

import os
import sqlite3
import time

from acidcat.core import paths


REGISTRY_SCHEMA_VERSION = 1


def open_registry(registry_path=None):
    """Open (or create) the registry DB. Applies schema if new."""
    path = paths.resolve_registry_path(registry_path)
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
            (str(REGISTRY_SCHEMA_VERSION),),
        )
        conn.commit()
        return
    # future: handle migrations when REGISTRY_SCHEMA_VERSION bumps


def _create_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS libraries (
            db_path           TEXT PRIMARY KEY,
            root_path         TEXT UNIQUE NOT NULL,
            label             TEXT NOT NULL,
            in_tree           INTEGER NOT NULL DEFAULT 0,
            sample_count      INTEGER,
            feature_count     INTEGER,
            last_indexed_at   REAL,
            last_seen_at      REAL,
            schema_version    INTEGER,
            created_at        REAL NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_libraries_root ON libraries(root_path)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_libraries_label ON libraries(label)")


class OverlapError(ValueError):
    """Raised when a register call would overlap an existing registered root.

    Carries the conflicting library row so the caller can surface a useful
    message ("path already covered by library X").
    """

    def __init__(self, message, conflict_row):
        super().__init__(message)
        self.conflict = conflict_row


def _assert_no_overlap(conn, root):
    """Reject roots that overlap any registered library.

    Three failure modes (all rejected):
        a) root is already registered
        b) some registered library is a parent of root
        c) some registered library is a child of root

    Re-registering the exact same root is not an overlap; callers that
    want idempotent re-register pass through register_library which
    handles that path via UPSERT.
    """
    norm = paths.normalize(root)
    rows = conn.execute(
        "SELECT db_path, root_path, label FROM libraries"
    ).fetchall()
    for r in rows:
        other = r["root_path"]
        if other == norm:
            # exact match: not an overlap (re-register is allowed)
            continue
        if norm.startswith(other + "/"):
            raise OverlapError(
                f"path is already covered by library '{r['label']}' at {other}; "
                f"forget that library first if you want to split",
                conflict_row=r,
            )
        if other.startswith(norm + "/"):
            raise OverlapError(
                f"library '{r['label']}' at {other} sits inside this path; "
                f"forget it first if you want to register a parent",
                conflict_row=r,
            )


def register_library(conn, root, label, db_path, in_tree=False,
                     schema_version=None):
    """Register (or refresh) a library.

    Idempotent on re-register: same `root` upserts metadata, preserves
    `created_at`. Overlapping (but non-equal) roots raise OverlapError.

    If the target db_path file already exists on disk (re-attach scenario:
    user forgot a library and is re-registering it), inspect that DB for
    sample_count / feature_count / last_indexed_at and populate the
    registry entry from there. Without this, list_libraries would report
    sample_count=NULL until the user runs reindex, which is misleading.

    Returns the canonical db_path stored in the registry.
    """
    if not label:
        raise ValueError("label is required")
    norm_root = paths.normalize(root)
    norm_db = paths.normalize(db_path)
    now = time.time()

    _assert_no_overlap(conn, norm_root)

    existing = conn.execute(
        "SELECT created_at FROM libraries WHERE root_path = ?", (norm_root,)
    ).fetchone()
    created_at = existing["created_at"] if existing else now

    conn.execute(
        """
        INSERT INTO libraries (
            db_path, root_path, label, in_tree,
            schema_version, created_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(db_path) DO UPDATE SET
            root_path     = excluded.root_path,
            label         = excluded.label,
            in_tree       = excluded.in_tree,
            schema_version= COALESCE(excluded.schema_version, libraries.schema_version),
            last_seen_at  = excluded.last_seen_at
        """,
        (norm_db, norm_root, label, 1 if in_tree else 0,
         schema_version, created_at, now),
    )
    conn.commit()

    # Re-attach: if the per-lib DB already exists on disk, populate the
    # cached counts from it so list_libraries returns useful numbers
    # without waiting for a reindex.
    if os.path.isfile(norm_db):
        _refresh_stats_from_db(conn, norm_root, norm_db)

    return norm_db


def _refresh_stats_from_db(conn, root, db_path):
    """Open `db_path`, read sample/feature counts and last_indexed_at, then
    push those values into the registry row for `root`. Best-effort: silently
    skips if the DB cannot be opened or doesn't have the expected schema.
    """
    import sqlite3

    try:
        # short-lived read-only connection. Avoid open_db (circular import via
        # acidcat.core.index) and avoid PRAGMA writes against a foreign DB.
        sub = sqlite3.connect(db_path)
        sub.row_factory = sqlite3.Row
    except sqlite3.DatabaseError:
        return

    try:
        try:
            sample_count = sub.execute(
                "SELECT COUNT(*) AS c FROM samples"
            ).fetchone()["c"]
            feature_count = sub.execute(
                "SELECT COUNT(*) AS c FROM features"
            ).fetchone()["c"]
        except sqlite3.OperationalError:
            # DB exists but schema isn't an acidcat sample DB; skip
            return

        last_indexed_at = None
        try:
            row = sub.execute(
                "SELECT MAX(last_indexed_at) AS t FROM scan_roots"
            ).fetchone()
            if row is not None:
                last_indexed_at = row["t"]
        except sqlite3.OperationalError:
            pass
    finally:
        sub.close()

    update_stats(
        conn, root,
        sample_count=sample_count,
        feature_count=feature_count,
        last_indexed_at=last_indexed_at,
    )


def update_stats(conn, root, sample_count=None, feature_count=None,
                 last_indexed_at=None, schema_version=None):
    """Refresh cached counts and timestamps for a library by root path.

    All numeric args are optional; only provided fields are updated.
    Always bumps last_seen_at to now.
    """
    norm = paths.normalize(root)
    fields = ["last_seen_at = ?"]
    values = [time.time()]
    if sample_count is not None:
        fields.append("sample_count = ?")
        values.append(sample_count)
    if feature_count is not None:
        fields.append("feature_count = ?")
        values.append(feature_count)
    if last_indexed_at is not None:
        fields.append("last_indexed_at = ?")
        values.append(last_indexed_at)
    if schema_version is not None:
        fields.append("schema_version = ?")
        values.append(schema_version)
    values.append(norm)
    conn.execute(
        f"UPDATE libraries SET {', '.join(fields)} WHERE root_path = ?",
        values,
    )
    conn.commit()


def forget_library(conn, label_or_root):
    """Remove a library from the registry. Does NOT touch the per-lib DB.

    Accepts either a label or a root path. Returns the number of rows
    removed (0 if the library was not registered).
    """
    norm = paths.normalize(label_or_root) if os.path.exists(label_or_root) else None
    cur = conn.cursor()
    if norm:
        cur.execute(
            "DELETE FROM libraries WHERE root_path = ? OR db_path = ?",
            (norm, norm),
        )
        if cur.rowcount > 0:
            conn.commit()
            return cur.rowcount
    cur.execute("DELETE FROM libraries WHERE label = ?", (label_or_root,))
    conn.commit()
    return cur.rowcount


def get_library(conn, label_or_root):
    """Look up a single library by label or by absolute root path. None on miss."""
    if os.path.exists(label_or_root):
        norm = paths.normalize(label_or_root)
        row = conn.execute(
            "SELECT * FROM libraries WHERE root_path = ? OR db_path = ?",
            (norm, norm),
        ).fetchone()
        if row:
            return row
    return conn.execute(
        "SELECT * FROM libraries WHERE label = ?", (label_or_root,)
    ).fetchone()


def list_libraries(conn, only_existing=False):
    """All registered libraries, sorted by last_seen_at desc.

    If `only_existing` is True, filters out libraries whose db_path is
    no longer present on disk (drive unmounted, file deleted by hand).
    Used by fan-out paths to silently skip orphans.
    """
    rows = conn.execute(
        "SELECT * FROM libraries ORDER BY last_seen_at DESC, label"
    ).fetchall()
    if not only_existing:
        return list(rows)
    return [r for r in rows if os.path.isfile(r["db_path"])]


def find_orphans(conn):
    """Inverse of list_libraries(only_existing=True): rows whose DB is gone."""
    return [
        r for r in list_libraries(conn)
        if not os.path.isfile(r["db_path"])
    ]


def find_library_for_path(conn, sample_path):
    """Return the registered library that contains `sample_path`, or None.

    Picks the longest matching root so a sample inside a nested layout
    resolves to the most-specific library. (We forbid nested registration,
    but a stale registry or a reorg could still produce overlapping rows
    until the user runs --forget.)
    """
    p = paths.normalize(sample_path)
    if os.path.isfile(p):
        p = os.path.dirname(p)
    rows = list_libraries(conn)
    rows = sorted(rows, key=lambda r: -len(r["root_path"] or ""))
    for r in rows:
        root = r["root_path"]
        if not root:
            continue
        if p == root or p.startswith(root + "/"):
            return r
    return None
