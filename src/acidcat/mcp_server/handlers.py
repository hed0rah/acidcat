"""acidcat-mcp tool handlers + per-library index data access.

The MCP tool implementations (search/get/locate/list/find/analyze/index/write)
and the cached fan-out connection layer they read through. Registration lives in
schema.py, the JSON-RPC transports in transport.py. ``_REGISTRY_PATH`` is the one
piece of shared state -- a module global set once by transport.main().
"""

import contextlib
import importlib.util
import os
import sqlite3
import threading
import time

from acidcat.core.analysis import camelot
from acidcat.core.catalogue import index as idx
from acidcat.core.catalogue import paths as acidpaths
from acidcat.core.catalogue import query_sql
from acidcat.core.catalogue import registry as reg
from acidcat.core.catalogue import search


# ── server config ─────────────────────────────────────────────────


_REGISTRY_PATH = None  # set once in main() before _run_stdio() starts; treat as set-once after that


# ── library access helpers ────────────────────────────────────────


# Process-lifetime read-connection cache for the long-lived server, so a
# fan-out query does not re-connect + re-apply pragmas to every library DB on
# every call. Keyed by db_path. Connections are opened check_same_thread=False
# and every use is serialized under _CACHE_LOCK, so this stays correct if tool
# dispatch ever moves off the single event-loop thread (anyio.to_thread). WAL
# means these readers see the write path's committed changes without eviction;
# only a change to the set of libraries (register/forget) or a reindex evicts.
_CONN_CACHE = {}
_CACHE_LOCK = threading.RLock()



# Result-count limits arrive from a model, so they get clamped rather than
# trusted. Two ways the raw value bites:
#   * SQLite reads `LIMIT -5` as NO limit, and `merged[:-5]` then just drops the
#     tail -- so a negative limit fans out over every registered library. On a
#     1.2M-sample corpus `limit: -1` pulls every row into memory and into the
#     model's context.
#   * `int(args.get("limit") or 50)` turns an explicit 0 into 50, because
#     `0 or 50` is 50.
_MAX_LIMIT = 10_000


def _limit(args, key, default):
    """A sane row limit from tool arguments."""
    raw = args.get(key)
    if raw is None or raw == "":
        return default
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ToolError(f"{key} must be a whole number, got {raw!r}")
    if n < 0:
        raise ToolError(f"{key} must be zero or positive, got {n}")
    return min(n, _MAX_LIMIT)


def _cached_conn(db_path):
    """A cached read connection for db_path, revalidated on borrow (a DB file
    swapped/removed out of band is evicted and reopened). Call under _CACHE_LOCK."""
    conn = _CONN_CACHE.get(db_path)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.DatabaseError:
            _evict(db_path)
    conn = idx.open_db(db_path, check_same_thread=False)
    _CONN_CACHE[db_path] = conn
    return conn


def _evict(db_path=None):
    """Drop and close cached connections (one db_path, or all). Called on the
    lib set changing (register/forget) and after a reindex."""
    with _CACHE_LOCK:
        for p in ([db_path] if db_path else list(_CONN_CACHE)):
            c = _CONN_CACHE.pop(p, None)
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass


def _scope_rows(libs, scope_arg):
    """Filter library ROWS by a --root-style scope BEFORE opening any DB, so a
    scoped query never touches out-of-scope libraries. None / label / path /
    comma-list; matches by exact label or root-path prefix overlap."""
    if not scope_arg:
        return libs
    scopes = [s.strip() for s in str(scope_arg).split(",") if s.strip()]
    if not scopes:
        return libs
    keep = []
    for lib in libs:
        for s in scopes:
            if lib["label"] == s:
                keep.append(lib)
                break
            if os.path.exists(s):
                norm = acidpaths.compare_path(acidpaths.normalize(s))
                root = acidpaths.compare_path(lib["root_path"])
                if root == norm or root.startswith(norm + "/") \
                        or norm.startswith(root + "/"):
                    keep.append(lib)
                    break
    return keep


@contextlib.contextmanager
def _fanout_conns(scope=None):
    """Cached read connections for the (optionally scoped) libraries, sorted
    deepest-root-first so dedup-by-path picks the most-specific library. Holds
    _CACHE_LOCK for the whole block, so the shared connections are never used
    concurrently. Connections are cached, not closed on exit."""
    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        libs = reg.list_libraries(rconn, only_existing=True)
    finally:
        rconn.close()
    libs = sorted(libs, key=lambda r: -len(r["root_path"] or ""))
    libs = _scope_rows(libs, scope)
    with _CACHE_LOCK:
        pairs = []
        for lib in libs:
            try:
                pairs.append((lib, _cached_conn(lib["db_path"])))
            except Exception:
                continue          # corrupt/locked DB: skip, orphan check filtered missing
        yield pairs


def _open_owning_library(path):
    """Return (lib_row, conn) for the library that contains `path`.

    Walks the registry to find the registered root that contains `path`.
    Falls back to scanning every library's samples table if the
    canonical mapping misses (e.g. symlinks, normalization edge cases).
    Returns (None, None) if the path is not indexed anywhere. The caller
    must close the returned conn.
    """
    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        lib = reg.find_library_for_path(rconn, path)
        all_libs = reg.list_libraries(rconn, only_existing=True)
    finally:
        rconn.close()

    if lib is not None and os.path.isfile(lib["db_path"]):
        try:
            return lib, idx.open_db(lib["db_path"])
        except Exception:
            pass

    # fallback: scan every library for this path (rare; covers symlinks
    # and odd normalization mismatches)
    norm = acidpaths.normalize(path)
    for cand in all_libs:
        try:
            conn = idx.open_db(cand["db_path"])
        except Exception:
            continue
        # the SELECTs were unguarded, so a DB that locks or corrupts partway
        # through this loop leaked its connection -- the caller only ever closes
        # the one that is RETURNED
        try:
            hit = conn.execute(
                "SELECT 1 FROM samples WHERE path = ? LIMIT 1", (norm,)
            ).fetchone()
            if hit is None and norm != path:
                hit = conn.execute(
                    "SELECT 1 FROM samples WHERE path = ? LIMIT 1", (path,)
                ).fetchone()
        except Exception:
            conn.close()
            continue                      # unreadable library: skip, do not leak
        if hit is not None:
            return cand, conn             # ownership passes to the caller
        conn.close()
    return None, None


def _dedup_by_path(rows):
    """Keep the first row per `path`. Used after fan-out merge."""
    seen = set()
    out = []
    for r in rows:
        p = r.get("path")
        if p in seen:
            continue
        seen.add(p)
        out.append(r)
    return out


def _resolve_stored_path(conn, user_path):
    """Within a single library DB, resolve a user-supplied path to its
    canonical stored form. Returns None if not present."""
    norm = acidpaths.normalize(user_path)
    for candidate in (norm, user_path):
        row = conn.execute(
            "SELECT path FROM samples WHERE path = ?", (candidate,)
        ).fetchone()
        if row is not None:
            return row["path"]
    return None


# -- tool infra (shared by the handlers) --------------------------------------


class ToolError(Exception):
    """Signalled back to the LLM as an error content block."""


def _require_path(args, field="path"):
    v = args.get(field)
    if not v:
        raise ToolError(f"{field} is required")
    return v


def _librosa_available():
    """Cheap check: does Python see librosa + numpy on the import path?

    Uses find_spec so this never imports librosa (which would cold-start
    numba and add tens of seconds to the first call probing availability).
    """
    return (importlib.util.find_spec("librosa") is not None
            and importlib.util.find_spec("numpy") is not None)


def _analysis_unavailable():
    return {
        "error": "analysis dependencies not installed",
        "fix": "pip install acidcat[analysis]",
    }


# ── fast tools ────────────────────────────────────────────────────


def search_samples(args):
    try:
        where, params, joins = query_sql.build_filter(
            bpm_min=args.get("bpm_min"), bpm_max=args.get("bpm_max"),
            duration_min=args.get("duration_min"),
            duration_max=args.get("duration_max"),
            key=args.get("key"), file_format=args.get("format"),
            device=args.get("device"), category=args.get("category"),
            creator=args.get("creator"), product=args.get("product"),
            tags=args.get("tags"), text=args.get("text"),
            note=args.get("note"), tunability_min=args.get("tunability_min"),
            envelope=_as_list(args.get("envelope")), band=_as_list(args.get("band")),
            centroid_min=args.get("centroid_min"),
            centroid_max=args.get("centroid_max"),
            wetness_min=args.get("wetness_min"),
            wetness_max=args.get("wetness_max"))
    except ValueError as e:
        raise ToolError(str(e))
    limit = _limit(args, "limit", 50)
    sql = query_sql.assemble(where, joins, order="s.path", limit_placeholder=True)
    count_sql = query_sql.assemble_count(where, joins)

    merged = []
    matched = 0
    counted_all = True
    with _fanout_conns(args.get("root")) as pairs:
        for lib, conn in pairs:
            try:
                rows = conn.execute(sql, params + [limit]).fetchall()
            except sqlite3.OperationalError as e:
                # FTS5 metacharacters (* " ( ) NOT AND OR) in user text
                # bubble up as OperationalError. surface as a clean
                # ToolError instead of leaking SQL details through the
                # catch-all dispatcher.
                if args.get("text"):
                    raise ToolError(
                        idx.fts5_syntax_message(args.get("text"))
                    )
                raise
            try:
                # Unbounded COUNT, because the SELECT above already carries
                # LIMIT: counting `merged` would be counting the truncation,
                # which is the defect being fixed rather than the fix.
                matched += conn.execute(count_sql, params).fetchone()[0]
            except Exception:
                counted_all = False
            for r in rows:
                d = _with_pitch_name(dict(r))
                d.pop("id", None)     # internal rowid alias, not part of the API
                d["library_label"] = lib["label"]
                merged.append(d)

    merged = _dedup_by_path(merged)
    merged.sort(key=lambda r: r.get("path") or "")
    merged = merged[:limit]
    if not counted_all:
        matched = None
    # "count" was len() of the ALREADY-TRUNCATED list, so a model asking "where
    # is X?" got 10 of 120 and was told count: 10, while list_formats on the
    # same server truthfully reported 126 -- the server contradicting itself.
    # A caller here has no stderr to read a note from, so the truncation has to
    # travel in the payload. Adding keys is backward compatible; changing what
    # "count" means would not be, so it still counts what was returned.
    return {"count": len(merged), "total_matched": matched,
            "truncated": (matched is not None and matched > len(merged)),
            "samples": merged}


def _as_list(v):
    """A tool argument that may be one name or a list of them."""
    if v is None:
        return ()
    return [v] if isinstance(v, str) else list(v)


def _with_pitch_name(d):
    """Name the MIDI pitch_note ("F#1") so a reader need not do the sum."""
    if d.get("pitch_note") is not None:
        from acidcat.core.analysis.pitch import note_name
        d["pitch_name"] = note_name(d["pitch_note"])
    return d


def get_sample(args):
    path = _require_path(args)
    lib, conn = _open_owning_library(path)
    if conn is None:
        raise ToolError(f"sample not indexed: {path}")
    try:
        resolved = _resolve_stored_path(conn, path)
        if resolved is None:
            raise ToolError(f"sample not indexed: {path}")
        row = conn.execute(
            "SELECT * FROM samples WHERE path = ?", (resolved,)
        ).fetchone()
        out = dict(row)
        out.pop("id", None)          # internal rowid alias, not part of the API
        out["tags"] = [
            r["tag"] for r in conn.execute(
                "SELECT tag FROM tags WHERE path = ? ORDER BY tag", (resolved,)
            ).fetchall()
        ]
        desc = conn.execute(
            "SELECT description FROM descriptions WHERE path = ?", (resolved,)
        ).fetchone()
        out["description"] = desc["description"] if desc else None
        pitch = conn.execute(
            "SELECT pitch_hz, pitch_note, pitch_cents, tunability, glide_cents "
            "FROM pitch WHERE path = ?", (resolved,)
        ).fetchone()
        if pitch is not None:
            out.update(_with_pitch_name(dict(pitch)))
        prof = conn.execute(
            "SELECT envelope, attack_ms, decay_ms, sustain, reattacks, centroid_hz, "
            "band, band_sub, band_low, band_lowmid, band_mid, band_high, wetness "
            "FROM profile WHERE path = ?", (resolved,)
        ).fetchone()
        if prof is not None:
            out.update(dict(prof))
        # "has features" has to mean "features find_similar can actually use".
        # An unversioned check reported True for stale rows that find_similar
        # filters out, so get_sample said "has features" about the same file
        # find_similar reported a population of 0 for -- two tools contradicting
        # each other is worse for a model than either answer alone.
        from acidcat.core.analysis.features import FEATURE_SET_VERSION
        out["has_features"] = bool(
            conn.execute(
                "SELECT 1 FROM features WHERE path = ? AND features_version = ?",
                (resolved, FEATURE_SET_VERSION),
            ).fetchone()
        )
        if lib is not None:
            out["library_label"] = lib["label"]
            out["library_root"] = lib["root_path"]
        return out
    finally:
        conn.close()


def locate_sample(args):
    name = args.get("name")
    if not name:
        raise ToolError("name is required")
    limit = _limit(args, "limit", 10)
    # substring match anywhere in the path. The previous "%/<name>%" pattern
    # required `name` to start at a path-component boundary, which silently
    # failed for searches that landed mid-filename (e.g. "Kick_Wet" inside
    # "PL_Hypnotize_03_126_Kick_Wet.wav").
    like = "%" + idx.escape_like(name) + "%"

    merged = []
    matched = 0
    counted_all = True
    with _fanout_conns() as pairs:
        for lib, conn in pairs:
            rows = conn.execute(
                "SELECT path, scan_root, format, bpm, key, duration "
                "FROM samples WHERE path LIKE ? ESCAPE '\\' "
                "ORDER BY path LIMIT ?",
                (like, limit),
            ).fetchall()
            try:
                # Unbounded, because the SELECT above is limited. This tool's
                # description promises "every registered library", and a model
                # asking "where is X?" has no way to see that it got a page.
                matched += conn.execute(
                    "SELECT COUNT(*) FROM samples WHERE path LIKE ? ESCAPE '\\'",
                    (like,),
                ).fetchone()[0]
            except Exception:
                counted_all = False
            for r in rows:
                d = dict(r)
                d["library_label"] = lib["label"]
                merged.append(d)

    merged = _dedup_by_path(merged)
    merged.sort(key=lambda r: r.get("path") or "")
    merged = merged[:limit]
    if not counted_all:
        matched = None
    # "count" was len() of the ALREADY-TRUNCATED list, so a model asking "where
    # is X?" got 10 of 120 and was told count: 10, while list_formats on the
    # same server truthfully reported 126 -- the server contradicting itself.
    # A caller here has no stderr to read a note from, so the truncation has to
    # travel in the payload. Adding keys is backward compatible; changing what
    # "count" means would not be, so it still counts what was returned.
    return {"count": len(merged), "total_matched": matched,
            "truncated": (matched is not None and matched > len(merged)),
            "samples": merged}


def list_libraries(_args):
    """Roll up the registry into a single response."""
    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        rows = reg.list_libraries(rconn)
    finally:
        rconn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["available"] = bool(os.path.isfile(d["db_path"]))
        out.append(d)
    available = sum(1 for r in out if r["available"])
    return {
        "count": len(out),
        "available": available,
        "unavailable": len(out) - available,
        "libraries": out,
    }


def list_tags(args):
    prefix = args.get("prefix") or ""
    counts = {}
    if prefix:
        sql = ("SELECT tag, COUNT(*) AS c FROM tags "
               "WHERE tag LIKE ? ESCAPE '\\' GROUP BY tag")
        params = (idx.escape_like(prefix) + "%",)
    else:
        sql = "SELECT tag, COUNT(*) AS c FROM tags GROUP BY tag"
        params = ()
    with _fanout_conns() as pairs:
        for _, conn in pairs:
            for r in conn.execute(sql, params).fetchall():
                counts[r["tag"]] = counts.get(r["tag"], 0) + r["c"]
    tags_out = sorted(
        ({"tag": t, "count": c} for t, c in counts.items()),
        key=lambda d: (-d["count"], d["tag"]),
    )
    return {"tags": tags_out}


def list_keys(_args):
    # "A" is stored for A major and for a root of A whose mode nobody knows;
    # key_mode tells them apart, so they are counted apart
    counts = {}
    with _fanout_conns() as pairs:
        for _, conn in pairs:
            for r in conn.execute(
                "SELECT key, key_mode, COUNT(*) AS c FROM samples "
                "WHERE key IS NOT NULL GROUP BY key, key_mode"
            ).fetchall():
                mode = r["key_mode"] or ("minor" if r["key"].endswith("m") else None)
                k = (r["key"], mode)
                counts[k] = counts.get(k, 0) + r["c"]
    out = sorted(
        ({"key": k, "mode": m, "count": c} for (k, m), c in counts.items()),
        key=lambda d: (-d["count"], d["key"], d["mode"] or ""),
    )
    return {"keys": out}


def list_formats(_args):
    counts = {}
    with _fanout_conns() as pairs:
        for _, conn in pairs:
            for r in conn.execute(
                "SELECT format, COUNT(*) AS c FROM samples "
                "WHERE format IS NOT NULL GROUP BY format"
            ).fetchall():
                counts[r["format"]] = counts.get(r["format"], 0) + r["c"]
    out = sorted(
        ({"format": f, "count": c} for f, c in counts.items()),
        key=lambda d: (-d["count"], d["format"]),
    )
    return {"formats": out}


def index_stats(_args):
    """Roll up stats across every library."""
    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        all_rows = reg.list_libraries(rconn)
    finally:
        rconn.close()

    available = [r for r in all_rows if os.path.isfile(r["db_path"])]
    unavailable = [r for r in all_rows if not os.path.isfile(r["db_path"])]

    total_samples = 0
    with_features = 0
    with_descriptions = 0
    by_format = {}
    last_indexed_at = None

    for lib in available:
        try:
            conn = idx.open_db(lib["db_path"])
        except Exception:
            continue
        try:
            total_samples += conn.execute(
                "SELECT COUNT(*) AS c FROM samples"
            ).fetchone()["c"]
            with_features += conn.execute(
                "SELECT COUNT(*) AS c FROM features"
            ).fetchone()["c"]
            with_descriptions += conn.execute(
                "SELECT COUNT(*) AS c FROM descriptions"
            ).fetchone()["c"]
            for row in conn.execute(
                "SELECT format, COUNT(*) AS c FROM samples "
                "WHERE format IS NOT NULL GROUP BY format"
            ).fetchall():
                by_format[row["format"]] = by_format.get(row["format"], 0) \
                    + row["c"]
            li = lib["last_indexed_at"]
            if li is not None and (last_indexed_at is None or li > last_indexed_at):
                last_indexed_at = li
        finally:
            conn.close()

    return {
        "total_samples": total_samples,
        "with_features": with_features,
        "with_descriptions": with_descriptions,
        "by_format": sorted(
            ({"format": f, "count": c} for f, c in by_format.items()),
            key=lambda d: -d["count"],
        ),
        "available_libraries": len(available),
        "unavailable_libraries": len(unavailable),
        "last_indexed_at": last_indexed_at,
        "analysis_available": _librosa_available(),
        "registry_path": acidpaths.resolve_registry_path(_REGISTRY_PATH),
    }


infer_kind = search.infer_kind   # logic lives in core.search now; kept as an alias


def find_compatible(args):
    """Find samples that mix with a reference: harmonic key (Camelot neighbours)
    + compatible tempo (incl. half/double-time). Reads the reference from the
    index, or parses it if unindexed. Thin adapter over core.search."""
    path = _require_path(args)
    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        libs = reg.list_libraries(rconn, only_existing=True)
    finally:
        rconn.close()
    ref, source, ref_lib = search.resolve_reference(path, libs)
    if ref is None:
        raise ToolError(f"sample not indexed and could not be parsed: {path}")

    key, bpm = ref.get("key"), ref.get("bpm")
    target_kind = search.infer_kind(ref.get("duration"), ref.get("acid_beats"))
    kind_arg = (args.get("kind") or "").lower() or None
    effective_kind = kind_arg or target_kind
    if effective_kind not in ("loop", "one_shot", "any"):
        raise ToolError(f"kind must be loop, one_shot, or any (got {effective_kind!r})")
    include_relative = args.get("include_relative", True)

    rows = search.find_compatible(
        libs, key=key, bpm=bpm, kind=effective_kind,
        bpm_tol=float(args.get("bpm_tolerance_pct") or 6) / 100.0,
        half_double=bool(args.get("half_double", True)),
        include_relative=include_relative,
        min_duration=args.get("min_duration"),
        limit=_limit(args, "limit", 20),
        exclude_path=path,
    )
    compat_pretty = sorted(camelot.compatible_keys(key)) if key else []
    if key and not include_relative:
        keep = search.compatible_codes(key, include_relative=False)
        compat_pretty = sorted(k for k in compat_pretty
                               if camelot.key_to_camelot(k) in keep)
    return {
        "target": {
            "path": ref.get("path", path),
            "bpm": bpm, "key": key,
            "camelot": camelot.key_to_camelot(key) if key else None,
            "kind": target_kind,
            "library_label": ref_lib,
            "source": source,
        },
        "compatible_keys": compat_pretty,
        "filter_kind": effective_kind,
        "count": len(rows),
        "samples": rows,
    }


# ── analysis tools (slow) ─────────────────────────────────────────


def find_similar(args):
    """Rank indexed samples by feature-vector similarity to a reference. Thin
    adapter over core.search.find_similar (shared with the CLI `similar` verb):
    resolve the target's features from the index or a live extract, then fan out
    and score in core."""
    path = _require_path(args)
    n = _limit(args, "n", 5)
    kind_arg = (args.get("kind") or "").lower() or None
    kind_filter_enabled = bool(args.get("kind_filter", True))

    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        libs = reg.list_libraries(rconn, only_existing=True)
    finally:
        rconn.close()

    target_feats, target_meta = search.resolve_target_features(path, libs)
    if target_feats is None:                      # not indexed: live extract
        if not _librosa_available():
            return _analysis_unavailable()
        from acidcat.core.analysis.features import extract_audio_features
        target_feats = extract_audio_features(path)
        if target_feats is None:
            raise ToolError(f"could not extract features from {path}")
        target_meta = {"duration": target_feats.get("duration_sec"),
                       "acid_beats": None}

    try:
        result = search.find_similar(
            libs, target_feats, target_meta, n=n, kind=kind_arg,
            kind_filter=kind_filter_enabled, exclude_path=path)
    except ValueError as e:
        raise ToolError(str(e))
    return {"target": path, **result}


def analyze_sample(args):
    path = _require_path(args)
    if not _librosa_available():
        return _analysis_unavailable()
    from acidcat.core.analysis.features import extract_audio_features
    feats = extract_audio_features(path)
    if feats is None:
        raise ToolError(f"could not extract features from {path}")
    return {"path": path, "features": feats}


def detect_bpm_key(args):
    path = _require_path(args)
    if not _librosa_available():
        return _analysis_unavailable()
    from acidcat.core.analysis.detect import estimate_librosa_metadata
    est = estimate_librosa_metadata(path) or {}
    return {
        "path": path,
        "bpm": est.get("estimated_bpm"),
        "key": est.get("estimated_key"),
        "bpm_source": est.get("bpm_source"),
        "key_source": est.get("key_source"),
        "duration": est.get("duration_sec"),
    }


# ── index management ──────────────────────────────────────────────


def reindex(args):
    """Rebuild a registered library by label or path.

    Walks the library's root and refreshes its DB. Updates the registry.
    """
    _evict()                                    # cached readers rebuild after the reindex
    target = args.get("path") or args.get("label")
    if not target:
        raise ToolError("path or label is required")
    with_features = bool(args.get("with_features", False))
    with_pitch = bool(args.get("with_pitch", False))
    with_profile = bool(args.get("with_profile", False))
    force = bool(args.get("force", False))

    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        lib = reg.get_library(rconn, target)
    finally:
        rconn.close()
    if lib is None:
        raise ToolError(
            f"no registered library matches '{target}'. "
            f"register it first via register_library."
        )

    from acidcat.core.catalogue.indexing import walk_and_upsert

    if not os.path.isdir(lib["root_path"]):
        raise ToolError(
            f"library root {lib['root_path']} does not exist on disk"
        )

    conn = idx.open_db(lib["db_path"])
    try:
        counts = walk_and_upsert(
            conn, lib["root_path"],
            do_features=with_features,
            do_deep=False,
            quiet=True,
            force=force,
            do_pitch=with_pitch,
            do_profile=with_profile,
        )
        conn.commit()
        sample_count = conn.execute(
            "SELECT COUNT(*) AS c FROM samples"
        ).fetchone()["c"]
        feature_count = conn.execute(
            "SELECT COUNT(*) AS c FROM features"
        ).fetchone()["c"]
    finally:
        conn.close()

    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        reg.update_stats(
            rconn, lib["root_path"],
            sample_count=sample_count,
            feature_count=feature_count,
            last_indexed_at=time.time(),
            schema_version=idx.SCHEMA_VERSION,
        )
    finally:
        rconn.close()

    return {
        "library_label": lib["label"],
        "library_root": lib["root_path"],
        "counts": counts,
        "sample_count": sample_count,
        "feature_count": feature_count,
    }


def reindex_features(args):
    """Extract librosa features for samples that lack them, across libraries."""
    _evict()                                    # cached readers rebuild after the write
    if not _librosa_available():
        return _analysis_unavailable()
    from acidcat.core.analysis.features import (extract_audio_features,
                                               FEATURE_SET_VERSION)

    # `if limit:` treated 0 as "no limit" here, the exact opposite of every
    # other tool, where 0 fell through to a default. A model passing the falsy
    # value it thinks is a no-op turned the slowest tool in the set into an
    # unbounded multi-hour run. 0 now means 0.
    limit = _limit(args, "limit", 0)
    target_label = args.get("library")  # optional: scope to one library

    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        if target_label:
            lib = reg.get_library(rconn, target_label)
            if lib is None:
                raise ToolError(f"no library matches '{target_label}'")
            libs = [lib] if os.path.isfile(lib["db_path"]) else []
        else:
            libs = reg.list_libraries(rconn, only_existing=True)
    finally:
        rconn.close()

    processed = failed = 0
    remaining = 0
    for lib in libs:
        try:
            conn = idx.open_db(lib["db_path"])
        except Exception:
            continue
        try:
            # stale-version rows count as needing work, not as done: a row
            # written by the old version=1 path is non-null, so this used to
            # skip it forever and the library could never repair itself
            sql = (
                "SELECT s.path FROM samples s "
                "LEFT JOIN features f ON f.path = s.path "
                "WHERE f.path IS NULL OR f.features_version != ?"
            )
            if limit > 0:
                rows = conn.execute(sql + " LIMIT ?",
                                    (FEATURE_SET_VERSION, limit)).fetchall()
            else:
                rows = conn.execute(sql, (FEATURE_SET_VERSION,)).fetchall()
            for r in rows:
                p = r["path"]
                if not os.path.isfile(p):
                    failed += 1
                    continue
                feats = extract_audio_features(p)
                if feats is None:
                    failed += 1
                    continue
                # No explicit version. This was `version=1` -- the only caller
                # in the tree that overrode the default, and 1 means
                # "pre-vector JSON only". find_similar filters on
                # features_version = FEATURE_SET_VERSION (3), so every vector
                # this tool wrote was invisible to the one tool that consumes
                # them: reindex_features reported {"processed": 2, "failed": 0}
                # and find_similar then returned population 0 on the same
                # library. Expensive work, confidently reported complete,
                # silently unusable.
                idx.upsert_features(conn, p, feats)
                processed += 1
            conn.commit()
            # Count what still needs CURRENT-version features, not merely what
            # has no features row at all. The stale version-1 rows are non-null,
            # so "remaining" read 0 and a re-run skipped them forever -- the
            # library could not repair itself without the CLI's --force.
            remaining += conn.execute(
                "SELECT COUNT(*) AS c FROM samples s "
                "LEFT JOIN features f ON f.path = s.path "
                "WHERE f.path IS NULL OR f.features_version != ?",
                (FEATURE_SET_VERSION,),
            ).fetchone()["c"]
        finally:
            conn.close()

    return {
        "processed": processed,
        "failed": failed,
        "remaining_unprocessed": remaining,
    }


# ── write tools ───────────────────────────────────────────────────


def tag_sample(args):
    path = _require_path(args)
    add = args.get("add_tags") or []
    remove = args.get("remove_tags") or []
    if not add and not remove:
        raise ToolError("provide add_tags and/or remove_tags")

    lib, conn = _open_owning_library(path)
    if conn is None:
        raise ToolError(f"sample not indexed: {path}")
    try:
        resolved = _resolve_stored_path(conn, path)
        if resolved is None:
            raise ToolError(f"sample not indexed: {path}")
        if add:
            idx.upsert_tags(conn, resolved, add)
        if remove:
            idx.remove_tags(conn, resolved, remove)
        conn.commit()
        tags = [
            r["tag"] for r in conn.execute(
                "SELECT tag FROM tags WHERE path = ? ORDER BY tag", (resolved,)
            ).fetchall()
        ]
        return {
            "path": resolved,
            "library_label": lib["label"] if lib else None,
            "tags": tags,
        }
    finally:
        conn.close()


def set_sample_description(args):
    path = _require_path(args)
    description = args.get("description")
    lib, conn = _open_owning_library(path)
    if conn is None:
        raise ToolError(f"sample not indexed: {path}")
    try:
        resolved = _resolve_stored_path(conn, path)
        if resolved is None:
            raise ToolError(f"sample not indexed: {path}")
        idx.upsert_description(conn, resolved, description or "")
        conn.commit()
        return {
            "path": resolved,
            "library_label": lib["label"] if lib else None,
            "description": description or "",
        }
    finally:
        conn.close()


def register_library(args):
    """Register a new library and create its DB. The user must run reindex
    afterwards (or call this after `acidcat index DIR --label NAME` from
    the CLI). This MCP tool is for letting an LLM expose the option."""
    _evict()                                    # the library set changed
    root = _require_path(args, field="root")
    label = args.get("label") or os.path.basename(acidpaths.normalize(root)) \
        or "library"
    in_tree = bool(args.get("in_tree", False))

    if not os.path.isdir(root):
        raise ToolError(f"not a directory: {root}")

    db_path = (acidpaths.in_tree_db_path_for(root) if in_tree
               else acidpaths.central_db_path_for(root, label))

    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        try:
            reg.register_library(
                rconn, root, label=label, db_path=db_path,
                in_tree=in_tree, schema_version=idx.SCHEMA_VERSION,
            )
        except reg.OverlapError as e:
            raise ToolError(str(e))
    finally:
        rconn.close()

    # create the DB so the registry's only_existing filter sees it
    conn = idx.open_db(db_path)
    conn.close()

    return {
        "label": label,
        "root": acidpaths.normalize(root),
        "db_path": db_path,
        "in_tree": in_tree,
        "next_step": "call reindex with this library's label to populate it",
    }


def forget_library(args):
    """Drop a library from the registry. Does NOT delete its DB file."""
    _evict()                                    # the library set changed
    target = args.get("label") or args.get("root")
    if not target:
        raise ToolError("label or root is required")
    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        n = reg.forget_library(rconn, target)
    finally:
        rconn.close()
    if n == 0:
        raise ToolError(f"no library matches '{target}'")
    return {"forgot": target, "count": n}


def discover_libraries(args):
    """Walk a directory tree and register every qualifying subfolder.

    Wraps the same _cmd_discover helper that the CLI uses. Always recommend
    the LLM call this with dry_run=true first to preview, then false to
    actually register.
    """
    from acidcat.core.catalogue import indexing as index_cmd

    root = _require_path(args, field="root")
    min_samples = int(args.get("min_samples") or 20)
    max_depth = int(args.get("max_depth") or 3)
    label_prefix = args.get("label_prefix") or ""
    dry_run = bool(args.get("dry_run", True))
    with_features = bool(args.get("with_features", False))

    if not os.path.isdir(root):
        raise ToolError(f"not a directory: {root}")
    if index_cmd._refuses_as_root(root):
        raise ToolError(
            f"refusing to discover at {root!r}; pick a more specific "
            f"samples directory."
        )

    norm_root = acidpaths.normalize(root)

    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        registered_roots = {
            r["root_path"] for r in reg.list_libraries(rconn)
        }
    finally:
        rconn.close()

    candidates = index_cmd._discover_candidates(
        norm_root, registered_roots, min_samples, max_depth,
    )

    from acidcat.core.catalogue.indexing import _count_audio_deep

    candidate_summaries = []
    any_truncated = False
    for c in candidates:
        count, truncated = _count_audio_deep(c, max_depth=max_depth)
        any_truncated = any_truncated or truncated
        summary = {
            "root": c,
            "label": (label_prefix or "") + os.path.basename(c),
            "audio_count": count,
        }
        # only on the candidates it actually bit, so the field means something
        if truncated:
            summary["audio_count_is_a_floor"] = True
        candidate_summaries.append(summary)

    if dry_run:
        out = {
            "dry_run": True,
            "root": norm_root,
            "candidate_count": len(candidates),
            "max_depth": max_depth,
            "candidates": candidate_summaries,
        }
        if any_truncated:
            out["note"] = (
                f"one or more counts are floors, not totals: audio exists "
                f"below max_depth={max_depth} and was not counted. Re-run with "
                f"a larger max_depth for true counts.")
        return out

    registered = []
    skipped = []
    used_labels = set()
    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        for cand in candidates:
            base = os.path.basename(cand) or "library"
            base_label = (label_prefix or "") + base
            parent = os.path.basename(os.path.dirname(cand))
            label = index_cmd._resolve_unique_label(
                rconn, base_label, parent, used_labels, root=cand,
            )
            db_path = acidpaths.central_db_path_for(cand, label)
            try:
                reg.register_library(
                    rconn, cand, label=label, db_path=db_path,
                    in_tree=False, schema_version=idx.SCHEMA_VERSION,
                )
                registered.append({"label": label, "root": cand})
            except reg.OverlapError as e:
                skipped.append({"root": cand, "reason": str(e)})
    finally:
        rconn.close()

    # optionally walk + extract features per registered library
    if with_features:
        from acidcat.core.catalogue.indexing import walk_and_upsert
        for entry in registered:
            cand = entry["root"]
            rconn = reg.open_registry(_REGISTRY_PATH)
            try:
                row = reg.get_library(rconn, cand)
                if row is None:
                    continue
                db_path = row["db_path"]
            finally:
                rconn.close()
            conn = idx.open_db(db_path)
            try:
                walk_and_upsert(
                    conn, cand,
                    do_features=True, do_deep=False, quiet=True,
                )
                sample_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM samples"
                ).fetchone()["c"]
                feature_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM features"
                ).fetchone()["c"]
            finally:
                conn.close()
            rconn = reg.open_registry(_REGISTRY_PATH)
            try:
                reg.update_stats(
                    rconn, cand,
                    sample_count=sample_count,
                    feature_count=feature_count,
                    last_indexed_at=time.time(),
                    schema_version=idx.SCHEMA_VERSION,
                )
            finally:
                rconn.close()

    return {
        "dry_run": False,
        "root": norm_root,
        "registered_count": len(registered),
        "skipped_count": len(skipped),
        "registered": registered,
        "skipped": skipped,
    }

