"""
acidcat-mcp -- stdio MCP server over the per-library sample index layout.

Default behavior fans out across every library registered in
`~/.acidcat/registry.db` so the LLM sees one logical sample pool made up
of many per-library DBs. Pass `--registry PATH` (or set
ACIDCAT_REGISTRY) to use a different registry, e.g. for project
isolation.

Tools register up front even when optional deps are missing; the SLOW
ones return a structured error describing the install step. Stdout is
strictly the JSON-RPC channel; the server logs warnings to stderr.
"""

import argparse
import importlib.util
import json
import math
import os
import sqlite3
import sys
import time

from acidcat import __version__
from acidcat.core import camelot
from acidcat.core import index as idx
from acidcat.core import paths as acidpaths
from acidcat.core import registry as reg


# ── server config ─────────────────────────────────────────────────


_REGISTRY_PATH = None  # set once in main() before _run_stdio() starts; treat as set-once after that


# ── library access helpers ────────────────────────────────────────


def _open_all_libraries():
    """Return [(lib_row, conn), ...] for every existing registered library.

    Sorted deepest-root-first so dedup-by-path consistently picks the
    most-specific library when paths overlap (which we forbid at register
    time, but a stale registry could still produce briefly).
    """
    rconn = reg.open_registry(_REGISTRY_PATH)
    try:
        libs = reg.list_libraries(rconn, only_existing=True)
    finally:
        rconn.close()
    libs = sorted(libs, key=lambda r: -len(r["root_path"] or ""))
    pairs = []
    for lib in libs:
        try:
            pairs.append((lib, idx.open_db(lib["db_path"])))
        except Exception:
            # corrupt/locked DB: skip silently, the orphan check already
            # filtered missing files
            continue
    return pairs


def _close_all(pairs):
    for _, c in pairs:
        try:
            c.close()
        except Exception:
            pass


def _scope_libraries(pairs, scope_arg):
    """Filter (lib, conn) pairs by --root-style scope arg.

    `scope_arg` is None, a single label/path, or a comma-separated list.
    Matches by exact label or by root-path prefix overlap.
    """
    if not scope_arg:
        return pairs
    scopes = [s.strip() for s in str(scope_arg).split(",") if s.strip()]
    if not scopes:
        return pairs
    keep = []
    for lib, conn in pairs:
        for s in scopes:
            if lib["label"] == s:
                keep.append((lib, conn))
                break
            if os.path.exists(s):
                norm = acidpaths.normalize(s)
                root = lib["root_path"]
                if root == norm or root.startswith(norm + "/") \
                        or norm.startswith(root + "/"):
                    keep.append((lib, conn))
                    break
    # close the libraries we are filtering out
    keep_ids = {id(c) for _, c in keep}
    for _, c in pairs:
        if id(c) not in keep_ids:
            try:
                c.close()
            except Exception:
                pass
    return keep


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
        hit = conn.execute(
            "SELECT 1 FROM samples WHERE path = ? LIMIT 1", (norm,)
        ).fetchone()
        if hit is None and norm != path:
            hit = conn.execute(
                "SELECT 1 FROM samples WHERE path = ? LIMIT 1", (path,)
            ).fetchone()
        if hit is not None:
            return cand, conn
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


# ── tool registry ─────────────────────────────────────────────────


class ToolError(Exception):
    """Signalled back to the LLM as an error content block."""


TOOLS = []


def _tool(name, description, input_schema, handler, annotations):
    TOOLS.append({
        "name": name,
        "description": description,
        "input_schema": input_schema,
        "handler": handler,
        "annotations": annotations,
    })


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
    where = []
    params = []
    joins = []

    if args.get("bpm_min") is not None:
        where.append("s.bpm >= ?")
        params.append(float(args["bpm_min"]))
    if args.get("bpm_max") is not None:
        where.append("s.bpm <= ?")
        params.append(float(args["bpm_max"]))
    if args.get("duration_min") is not None:
        where.append("s.duration >= ?")
        params.append(float(args["duration_min"]))
    if args.get("duration_max") is not None:
        where.append("s.duration <= ?")
        params.append(float(args["duration_max"]))
    if args.get("key"):
        where.append("LOWER(s.key) = LOWER(?)")
        params.append(args["key"])
    if args.get("format"):
        where.append("LOWER(s.format) = LOWER(?)")
        params.append(args["format"])

    tags = args.get("tags") or []
    if tags:
        placeholders = ",".join("?" for _ in tags)
        where.append(
            f"s.path IN (SELECT path FROM tags WHERE tag IN ({placeholders}) "
            f"GROUP BY path HAVING COUNT(DISTINCT tag) = ?)"
        )
        params.extend(tags)
        params.append(len(tags))

    if args.get("text"):
        joins.append("JOIN samples_fts fts ON fts.path = s.path")
        where.append("samples_fts MATCH ?")
        params.append(args["text"])

    limit = int(args.get("limit") or 50)
    sql = "SELECT s.* FROM samples s " + " ".join(joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.path LIMIT ?"

    pairs = _scope_libraries(_open_all_libraries(), args.get("root"))
    try:
        merged = []
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
                        f"invalid search text: {args.get('text')!r}. "
                        f"FTS5 special chars (* \" ( ) NOT AND OR) "
                        f"need to be quoted as a literal phrase."
                    )
                raise
            for r in rows:
                d = dict(r)
                d["library_label"] = lib["label"]
                merged.append(d)
    finally:
        _close_all(pairs)

    merged = _dedup_by_path(merged)
    merged.sort(key=lambda r: r.get("path") or "")
    merged = merged[:limit]
    return {"count": len(merged), "samples": merged}


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
        out["tags"] = [
            r["tag"] for r in conn.execute(
                "SELECT tag FROM tags WHERE path = ? ORDER BY tag", (resolved,)
            ).fetchall()
        ]
        desc = conn.execute(
            "SELECT description FROM descriptions WHERE path = ?", (resolved,)
        ).fetchone()
        out["description"] = desc["description"] if desc else None
        out["has_features"] = bool(
            conn.execute(
                "SELECT 1 FROM features WHERE path = ?", (resolved,)
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
    limit = int(args.get("limit") or 10)
    # substring match anywhere in the path. The previous "%/<name>%" pattern
    # required `name` to start at a path-component boundary, which silently
    # failed for searches that landed mid-filename (e.g. "Kick_Wet" inside
    # "PL_Hypnotize_03_126_Kick_Wet.wav").
    like = "%" + name + "%"

    pairs = _open_all_libraries()
    merged = []
    try:
        for lib, conn in pairs:
            rows = conn.execute(
                "SELECT path, scan_root, format, bpm, key, duration "
                "FROM samples WHERE path LIKE ? ORDER BY path LIMIT ?",
                (like, limit),
            ).fetchall()
            for r in rows:
                d = dict(r)
                d["library_label"] = lib["label"]
                merged.append(d)
    finally:
        _close_all(pairs)

    merged = _dedup_by_path(merged)
    merged.sort(key=lambda r: r.get("path") or "")
    merged = merged[:limit]
    return {"count": len(merged), "samples": merged}


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
    pairs = _open_all_libraries()
    counts = {}
    try:
        if prefix:
            sql = ("SELECT tag, COUNT(*) AS c FROM tags WHERE tag LIKE ? "
                   "GROUP BY tag")
            params = (prefix + "%",)
        else:
            sql = "SELECT tag, COUNT(*) AS c FROM tags GROUP BY tag"
            params = ()
        for _, conn in pairs:
            for r in conn.execute(sql, params).fetchall():
                counts[r["tag"]] = counts.get(r["tag"], 0) + r["c"]
    finally:
        _close_all(pairs)
    tags_out = sorted(
        ({"tag": t, "count": c} for t, c in counts.items()),
        key=lambda d: (-d["count"], d["tag"]),
    )
    return {"tags": tags_out}


def list_keys(_args):
    pairs = _open_all_libraries()
    counts = {}
    try:
        for _, conn in pairs:
            for r in conn.execute(
                "SELECT key, COUNT(*) AS c FROM samples "
                "WHERE key IS NOT NULL GROUP BY key"
            ).fetchall():
                counts[r["key"]] = counts.get(r["key"], 0) + r["c"]
    finally:
        _close_all(pairs)
    out = sorted(
        ({"key": k, "count": c} for k, c in counts.items()),
        key=lambda d: (-d["count"], d["key"]),
    )
    return {"keys": out}


def list_formats(_args):
    pairs = _open_all_libraries()
    counts = {}
    try:
        for _, conn in pairs:
            for r in conn.execute(
                "SELECT format, COUNT(*) AS c FROM samples "
                "WHERE format IS NOT NULL GROUP BY format"
            ).fetchall():
                counts[r["format"]] = counts.get(r["format"], 0) + r["c"]
    finally:
        _close_all(pairs)
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


def infer_kind(duration, acid_beats):
    """Classify a sample as 'loop' / 'one_shot' / 'any' from length + beats.

    acid_beats > 0 OR duration >= 2.0 -> loop
    duration < 1.0 AND (acid_beats is None or 0) -> one_shot
    otherwise (1.0 <= duration < 2.0 without beats) -> any
    """
    d = duration or 0.0
    b = acid_beats or 0
    if b > 0 or d >= 2.0:
        return "loop"
    if d < 1.0 and b <= 0:
        return "one_shot"
    return "any"


def find_compatible(args):
    path = _require_path(args)
    tol = float(args.get("bpm_tolerance_pct") or 6) / 100.0
    limit = int(args.get("limit") or 20)
    include_relative = args.get("include_relative", True)
    kind_arg = (args.get("kind") or "").lower() or None
    min_duration = args.get("min_duration")

    # find target's metadata in its owning library
    lib, conn = _open_owning_library(path)
    if conn is None:
        raise ToolError(f"sample not indexed: {path}")
    try:
        resolved = _resolve_stored_path(conn, path)
        target = conn.execute(
            "SELECT * FROM samples WHERE path = ?", (resolved,)
        ).fetchone()
    finally:
        conn.close()

    target_bpm = target["bpm"]
    target_key = target["key"]
    target_kind = infer_kind(target["duration"], target["acid_beats"])

    effective_kind = kind_arg or target_kind
    if effective_kind not in ("loop", "one_shot", "any"):
        raise ToolError(
            f"kind must be loop, one_shot, or any (got {effective_kind!r})"
        )

    compat_keys = camelot.compatible_keys(target_key) if target_key else set()
    if not include_relative and target_key:
        base = camelot.key_to_camelot(target_key)
        if base:
            keep = {c for c in camelot.camelot_neighbors(base)
                    if not c.endswith(("A",) if base.endswith("B") else ("B",))}
            compat_keys = {
                k for k in compat_keys
                if camelot.key_to_camelot(k) in keep
            }

    sql_keys = set()
    for k in compat_keys:
        sql_keys.update(camelot.enharmonic_spellings(k))

    where = ["s.path != ?"]
    params = [resolved]

    if target_bpm is not None:
        lo = target_bpm * (1 - tol)
        hi = target_bpm * (1 + tol)
        where.append("s.bpm BETWEEN ? AND ?")
        params.extend([lo, hi])

    if sql_keys:
        placeholders = ",".join("?" for _ in sql_keys)
        where.append(f"LOWER(s.key) IN ({placeholders})")
        params.extend(k.lower() for k in sql_keys)

    if effective_kind == "loop":
        where.append("(s.acid_beats > 0 OR s.duration >= 2.0)")
    elif effective_kind == "one_shot":
        where.append(
            "((s.acid_beats IS NULL OR s.acid_beats = 0) AND "
            "(s.duration IS NULL OR s.duration < 1.0))"
        )

    if min_duration is not None:
        where.append("s.duration >= ?")
        params.append(float(min_duration))

    sql = (
        "SELECT s.* FROM samples s WHERE "
        + " AND ".join(where)
        + " ORDER BY s.bpm IS NULL, ABS(s.bpm - ?) LIMIT ?"
    )

    # fan out across every library, applying the same WHERE
    pairs = _open_all_libraries()
    merged = []
    try:
        for cand_lib, cand_conn in pairs:
            rows = cand_conn.execute(
                sql, params + [target_bpm or 0, limit]
            ).fetchall()
            for r in rows:
                d = dict(r)
                d["library_label"] = cand_lib["label"]
                merged.append(d)
    finally:
        _close_all(pairs)

    merged = _dedup_by_path(merged)
    merged.sort(
        key=lambda r: (
            0 if r.get("bpm") is not None else 1,
            abs((r.get("bpm") or 0) - (target_bpm or 0)),
            r.get("path") or "",
        )
    )
    merged = merged[:limit]
    return {
        "target": {
            "path": resolved,
            "bpm": target_bpm,
            "key": target_key,
            "camelot": camelot.key_to_camelot(target_key) if target_key else None,
            "kind": target_kind,
            "library_label": lib["label"] if lib else None,
        },
        "compatible_keys": sorted(compat_keys),
        "filter_kind": effective_kind,
        "count": len(merged),
        "samples": merged,
    }


# ── analysis tools (slow) ─────────────────────────────────────────


def find_similar(args):
    path = _require_path(args)
    n = int(args.get("n") or 5)
    kind_arg = (args.get("kind") or "").lower() or None
    kind_filter_enabled = bool(args.get("kind_filter", True))

    # try each library for the target's stored features AND metadata first.
    # we need duration + acid_beats to infer target kind for the filter.
    target_feats = None
    target_meta = None
    pairs = _open_all_libraries()
    try:
        for _, conn in pairs:
            for candidate in (acidpaths.normalize(path), path):
                feats = idx.get_features(conn, candidate)
                if feats is None:
                    continue
                row = conn.execute(
                    "SELECT duration, acid_beats FROM samples WHERE path = ?",
                    (candidate,),
                ).fetchone()
                target_feats = feats
                if row is not None:
                    target_meta = {
                        "duration": row["duration"],
                        "acid_beats": row["acid_beats"],
                    }
                break
            if target_feats is not None:
                break
    finally:
        _close_all(pairs)

    if target_feats is None:
        if not _librosa_available():
            return _analysis_unavailable()
        from acidcat.core.features import extract_audio_features
        target_feats = extract_audio_features(path)
        if target_feats is None:
            raise ToolError(f"could not extract features from {path}")
        # no row to read acid_beats from; fall back to features dict's
        # duration_sec, acid_beats unknown
        target_meta = {
            "duration": target_feats.get("duration_sec"),
            "acid_beats": None,
        }

    target_kind = infer_kind(
        (target_meta or {}).get("duration"),
        (target_meta or {}).get("acid_beats"),
    )
    if kind_arg and kind_arg not in ("loop", "one_shot", "any"):
        raise ToolError(
            f"kind must be loop, one_shot, or any (got {kind_arg!r})"
        )
    effective_kind = (
        kind_arg
        if kind_arg
        else (target_kind if kind_filter_enabled else "any")
    )

    feature_keys = sorted(
        k for k, v in target_feats.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    )
    if not feature_keys:
        raise ToolError("no numeric features available")
    tv = [float(target_feats[k]) for k in feature_keys]

    target_norm = acidpaths.normalize(path)
    scored = []
    pairs = _open_all_libraries()
    population = 0
    try:
        for lib, conn in pairs:
            sql = (
                "SELECT f.path, f.features_json, "
                "s.bpm, s.key, s.duration, s.format, s.acid_beats "
                "FROM features f JOIN samples s ON s.path = f.path"
            )
            params = []
            if effective_kind == "loop":
                sql += (" WHERE (s.acid_beats > 0 OR s.duration >= 2.0)")
            elif effective_kind == "one_shot":
                sql += (
                    " WHERE ((s.acid_beats IS NULL OR s.acid_beats = 0) "
                    "AND (s.duration IS NULL OR s.duration < 1.0))"
                )
            rows = conn.execute(sql, params).fetchall()
            population += len(rows)
            for r in rows:
                try:
                    feats = json.loads(r["features_json"])
                    v = [float(feats.get(k, 0.0) or 0.0) for k in feature_keys]
                except Exception:
                    continue
                sim = _cosine(tv, v)
                scored.append({
                    "path": r["path"],
                    "bpm": r["bpm"],
                    "key": r["key"],
                    "duration": r["duration"],
                    "format": r["format"],
                    "library_label": lib["label"],
                    "similarity": round(sim, 6),
                })
    finally:
        _close_all(pairs)

    scored = [s for s in scored if s["path"] != target_norm]
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    scored = _dedup_by_path(scored)

    # population stats across the full filtered candidate set, used to
    # surface percentile rank and relative-to-mean similarity. Helps users
    # distinguish results inside the very-tight 0.99x clusters that pure
    # cosine produces on same-pack samples processed identically.
    pop_n = len(scored)
    if pop_n > 0:
        sims = [s["similarity"] for s in scored]
        pop_mean = sum(sims) / pop_n
        for rank, item in enumerate(scored):
            # 100th percentile = top, 0th = bottom. Stable definition: the
            # fraction of the population with a STRICTLY LOWER similarity.
            below = sum(1 for sim in sims if sim < item["similarity"])
            item["percentile_rank"] = round(100.0 * below / pop_n, 1)
            item["similarity_above_mean"] = round(
                item["similarity"] - pop_mean, 6
            )

    scored = scored[:n]
    return {
        "target": path,
        "target_kind": target_kind,
        "filter_kind": effective_kind,
        "population": population,
        "results": scored,
    }


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def analyze_sample(args):
    path = _require_path(args)
    if not _librosa_available():
        return _analysis_unavailable()
    from acidcat.core.features import extract_audio_features
    feats = extract_audio_features(path)
    if feats is None:
        raise ToolError(f"could not extract features from {path}")
    return {"path": path, "features": feats}


def detect_bpm_key(args):
    path = _require_path(args)
    if not _librosa_available():
        return _analysis_unavailable()
    from acidcat.core.detect import estimate_librosa_metadata
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
    target = args.get("path") or args.get("label")
    if not target:
        raise ToolError("path or label is required")
    with_features = bool(args.get("with_features", False))

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

    from acidcat.commands.index import _walk_and_upsert

    if not os.path.isdir(lib["root_path"]):
        raise ToolError(
            f"library root {lib['root_path']} does not exist on disk"
        )

    conn = idx.open_db(lib["db_path"])
    try:
        counts = _walk_and_upsert(
            conn, lib["root_path"],
            do_features=with_features,
            do_deep=False,
            quiet=True,
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
    if not _librosa_available():
        return _analysis_unavailable()
    from acidcat.core.features import extract_audio_features

    limit = args.get("limit")
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
            sql = (
                "SELECT s.path FROM samples s "
                "LEFT JOIN features f ON f.path = s.path "
                "WHERE f.path IS NULL"
            )
            if limit:
                sql += " LIMIT ?"
                rows = conn.execute(sql, (int(limit),)).fetchall()
            else:
                rows = conn.execute(sql).fetchall()
            for r in rows:
                p = r["path"]
                if not os.path.isfile(p):
                    failed += 1
                    continue
                feats = extract_audio_features(p)
                if feats is None:
                    failed += 1
                    continue
                idx.upsert_features(conn, p, feats, version=1)
                processed += 1
            conn.commit()
            remaining += conn.execute(
                "SELECT COUNT(*) AS c FROM samples s "
                "LEFT JOIN features f ON f.path = s.path "
                "WHERE f.path IS NULL"
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


def describe_sample(args):
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
    from acidcat.commands import index as index_cmd

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

    candidate_summaries = []
    for c in candidates:
        count = index_cmd._count_audio_in_subtree(c, max_depth=max_depth)
        candidate_summaries.append({
            "root": c,
            "label": (label_prefix or "") + os.path.basename(c),
            "audio_count": count,
        })

    if dry_run:
        return {
            "dry_run": True,
            "root": norm_root,
            "candidate_count": len(candidates),
            "candidates": candidate_summaries,
        }

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
                rconn, base_label, parent, used_labels,
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
        from acidcat.commands.index import _walk_and_upsert
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
                _walk_and_upsert(
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


# ── tool registration ─────────────────────────────────────────────


def _register_all():
    # fast (read-only)
    _tool(
        "search_samples",
        "Fast. Filter samples across all registered libraries by "
        "bpm/key/duration/tags/text/format. Use 'root' to scope to one or "
        "more libraries by label or path. Prefer this over analysis tools "
        "for any discovery query.",
        {
            "type": "object",
            "properties": {
                "bpm_min": {"type": "number"},
                "bpm_max": {"type": "number"},
                "key": {"type": "string",
                        "description": "Exact key (e.g. 'Am', 'C#')."},
                "duration_min": {"type": "number"},
                "duration_max": {"type": "number"},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "AND semantics across tags."},
                "text": {"type": "string",
                         "description": "FTS across title/artist/album/"
                         "genre/comment/description/tags/path."},
                "format": {"type": "string",
                           "description": "wav, mp3, flac, midi, serum, ..."},
                "root": {"type": "string",
                         "description": "Library label or path. "
                         "Comma-separated for multiple."},
                "limit": {"type": "integer", "default": 50},
            },
        },
        search_samples,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "get_sample",
        "Fast. Full metadata for one sample path, including tags, "
        "description, and which library it belongs to.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        get_sample,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "locate_sample",
        "Fast. Find samples by filename substring across every registered "
        "library. Use this to answer 'where is X?' questions.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["name"],
        },
        locate_sample,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "list_libraries",
        "Fast. Every library registered with acidcat: label, root path, "
        "sample/feature counts, in-tree vs central, last indexed at, "
        "and whether the DB file is currently available on disk.",
        {"type": "object", "properties": {}},
        list_libraries,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "list_tags",
        "Fast. Distinct tags with counts, summed across all libraries. "
        "Use 'prefix' to narrow.",
        {
            "type": "object",
            "properties": {"prefix": {"type": "string"}},
        },
        list_tags,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "list_keys",
        "Fast. Distinct musical keys with counts across all libraries.",
        {"type": "object", "properties": {}},
        list_keys,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "list_formats",
        "Fast. Distinct file formats with counts across all libraries.",
        {"type": "object", "properties": {}},
        list_formats,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "index_stats",
        "Fast. Roll-up counts across every library: total samples, "
        "feature coverage, format breakdown, available vs unavailable "
        "library count, analysis-tool availability, registry path.",
        {"type": "object", "properties": {}},
        index_stats,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "find_compatible",
        "Fast. Harmonically and rhythmically compatible samples via Camelot "
        "+ BPM tolerance. Fans out across libraries. By default filters to "
        "the target's own kind (loops match loops, one-shots match "
        "one-shots) so a kalimba loop query does not return kalimba "
        "one-shots. No audio analysis; metadata-only.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "bpm_tolerance_pct": {"type": "number", "default": 6},
                "include_relative": {"type": "boolean", "default": True},
                "kind": {
                    "type": "string",
                    "enum": ["loop", "one_shot", "any"],
                    "description":
                        "Filter by sample kind. Default: auto-infer from "
                        "target.",
                },
                "min_duration": {
                    "type": "number",
                    "description":
                        "Optional seconds floor. Overrides/augments kind "
                        "filter for length-specific queries.",
                },
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["path"],
        },
        find_compatible,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )

    # analysis (slow)
    _tool(
        "find_similar",
        "SLOW if features are not indexed, fast if they are. Requires "
        "acidcat[analysis]. Nearest neighbors by librosa feature cosine, "
        "fanned out across all libraries. By default filters to the target's "
        "own kind (loops match loops, one-shots match one-shots) so a "
        "0.4s 808 query does not surface a 7s drum build-up that happens to "
        "share spectral tilt. Each result also reports percentile_rank and "
        "similarity_above_mean to help distinguish ranks inside the tight "
        "0.99x clusters that same-pack samples produce. Only use when "
        "metadata-based tools (search_samples, find_compatible) cannot answer.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "n": {"type": "integer", "default": 5},
                "kind": {
                    "type": "string",
                    "enum": ["loop", "one_shot", "any"],
                    "description":
                        "Force a specific kind filter. Default: auto-infer "
                        "from target via duration + acid_beats.",
                },
                "kind_filter": {
                    "type": "boolean",
                    "default": True,
                    "description":
                        "If true (default), filter results to the target's "
                        "inferred kind. Set false to disable filtering "
                        "without forcing a specific kind.",
                },
            },
            "required": ["path"],
        },
        find_similar,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "analyze_sample",
        "SLOW (~1-10s after warm-up; first call ~30-60s due to librosa "
        "import). Requires acidcat[analysis]. On-the-fly librosa feature "
        "extraction for an unindexed file. Prefer get_sample for indexed "
        "files.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
        analyze_sample,
        {"readOnlyHint": True, "destructiveHint": False,
         "idempotentHint": False, "openWorldHint": False},
    )
    _tool(
        "detect_bpm_key",
        "SLOW (~0.5-2s). Requires acidcat[analysis]. BPM + key estimation "
        "only. Cheaper than analyze_sample. Prefer get_sample when the "
        "file is already indexed.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        detect_bpm_key,
        {"readOnlyHint": True, "destructiveHint": False,
         "idempotentHint": False, "openWorldHint": False},
    )

    # index management
    _tool(
        "reindex",
        "SLOW. Re-walk a registered library and refresh its DB. Identify "
        "the library by label or root path. Only call when the user "
        "explicitly asks to refresh.",
        {
            "type": "object",
            "properties": {
                "label": {"type": "string",
                          "description": "Library label (preferred)."},
                "path": {"type": "string",
                         "description": "Library root path (alternative)."},
                "with_features": {"type": "boolean", "default": False},
            },
        },
        reindex,
        {"readOnlyHint": False, "destructiveHint": False,
         "idempotentHint": True, "openWorldHint": False},
    )
    _tool(
        "reindex_features",
        "VERY SLOW. Requires acidcat[analysis]. Extracts librosa features "
        "for any indexed samples that lack them, across libraries (or one "
        "library if 'library' arg provided). Only call when explicitly "
        "asked.",
        {
            "type": "object",
            "properties": {
                "library": {"type": "string",
                            "description": "Optional: scope to one library "
                                           "by label or root path."},
                "limit": {"type": "integer",
                          "description": "Max files per library this call."},
            },
        },
        reindex_features,
        {"readOnlyHint": False, "destructiveHint": False,
         "idempotentHint": True, "openWorldHint": False},
    )

    # registry mutations
    _tool(
        "register_library",
        "Destructive. Register a new library so it becomes part of "
        "fan-out queries. Creates the DB but does NOT populate it (call "
        "reindex afterwards). Default storage is central "
        "(~/.acidcat/libraries/<label>_<hash>.db); pass in_tree=true to "
        "store the DB inside the library's own directory.",
        {
            "type": "object",
            "properties": {
                "root": {"type": "string",
                         "description": "Absolute path to the library root."},
                "label": {"type": "string",
                          "description":
                              "Friendly label (default: basename of root)."},
                "in_tree": {"type": "boolean", "default": False},
            },
            "required": ["root"],
        },
        register_library,
        {"readOnlyHint": False, "destructiveHint": True,
         "idempotentHint": True, "openWorldHint": False},
    )
    _tool(
        "forget_library",
        "Destructive. Remove a library from the registry. Does "
        "NOT delete its DB file; rerunning register_library on the same "
        "root re-attaches it. Confirm with the user before calling.",
        {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "root": {"type": "string"},
            },
        },
        forget_library,
        {"readOnlyHint": False, "destructiveHint": True,
         "idempotentHint": False, "openWorldHint": False},
    )
    _tool(
        "discover_libraries",
        "SLOW. Walk a directory tree and register every qualifying "
        "subfolder as its own library. A folder qualifies if its subtree "
        "(within max_depth) holds at least min_samples audio files. "
        "Recurses into folders that don't qualify on their own to find "
        "qualifying grandchildren. Always call once with dry_run=true "
        "first to preview the candidates, then again with dry_run=false "
        "after the user confirms.",
        {
            "type": "object",
            "properties": {
                "root": {"type": "string",
                         "description":
                             "Container directory to walk. acidcat refuses "
                             "to discover at the user's home dir."},
                "min_samples": {"type": "integer", "default": 20,
                                "description":
                                    "Minimum audio files in a subtree for "
                                    "it to qualify as a library."},
                "max_depth": {"type": "integer", "default": 3,
                              "description":
                                  "How many levels into the tree to walk."},
                "label_prefix": {"type": "string", "default": "",
                                 "description":
                                     "Prefix every auto-derived label "
                                     "with this string. Useful for "
                                     "namespacing scattered collections."},
                "dry_run": {"type": "boolean", "default": True,
                            "description":
                                "Return the candidate list without "
                                "writing to the registry. Defaults to "
                                "true; pass false explicitly to commit."},
                "with_features": {"type": "boolean", "default": False,
                                  "description":
                                      "Also walk + extract librosa features "
                                      "for each registered library. VERY "
                                      "SLOW; defer unless explicitly asked."},
            },
            "required": ["root"],
        },
        discover_libraries,
        {"readOnlyHint": False, "destructiveHint": True,
         "idempotentHint": True, "openWorldHint": False},
    )

    # write tools (sample-level)
    _tool(
        "tag_sample",
        "Destructive. Add or remove tags on a sample. Confirm with "
        "the user before calling.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "add_tags": {"type": "array", "items": {"type": "string"}},
                "remove_tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["path"],
        },
        tag_sample,
        {"readOnlyHint": False, "destructiveHint": True,
         "idempotentHint": False, "openWorldHint": False},
    )
    _tool(
        "describe_sample",
        "Destructive. Set or clear the free-text description on a "
        "sample. Confirm with the user before calling.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["path"],
        },
        describe_sample,
        {"readOnlyHint": False, "destructiveHint": True,
         "idempotentHint": True, "openWorldHint": False},
    )


_register_all()


def dispatch(name, arguments):
    """Call a tool by name with a dict of arguments. Raises ToolError or
    returns dict."""
    for t in TOOLS:
        if t["name"] == name:
            return t["handler"](arguments or {})
    raise ToolError(f"unknown tool: {name}")


# ── stdio entrypoint ──────────────────────────────────────────────


async def _run_stdio():
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        import mcp.types as mcp_types
    except ImportError:
        print("acidcat-mcp: the mcp package is not installed. "
              "Install with: pip install acidcat[mcp]", file=sys.stderr)
        sys.exit(1)

    app = Server("acidcat")

    @app.list_tools()
    async def _list_tools():
        out = []
        for t in TOOLS:
            ann_kwargs = {}
            for k, v in t["annotations"].items():
                ann_kwargs[k] = v
            try:
                annotations = mcp_types.ToolAnnotations(**ann_kwargs)
            except TypeError:
                annotations = None
            tool_kwargs = {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["input_schema"],
            }
            if annotations is not None:
                tool_kwargs["annotations"] = annotations
            out.append(mcp_types.Tool(**tool_kwargs))
        return out

    @app.call_tool()
    async def _call_tool(name, arguments):
        try:
            result = dispatch(name, arguments or {})
            text = json.dumps(result, default=str, indent=2)
            return [mcp_types.TextContent(type="text", text=text)]
        except ToolError as e:
            payload = {"error": str(e)}
            return [mcp_types.TextContent(type="text", text=json.dumps(payload))]
        except Exception as e:
            payload = {"error": f"internal: {e.__class__.__name__}: {e}"}
            return [mcp_types.TextContent(type="text", text=json.dumps(payload))]

    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def _warn_legacy_db():
    legacy = acidpaths.legacy_global_db_path()
    if os.path.isfile(legacy):
        print(f"acidcat-mcp: legacy v0.4 DB at {legacy} is ignored. "
              f"Remove with: rm {legacy}*", file=sys.stderr)


def main(argv=None):
    global _REGISTRY_PATH
    parser = argparse.ArgumentParser(
        prog="acidcat-mcp",
        description="MCP server exposing the acidcat per-library index over stdio.",
    )
    parser.add_argument("--registry",
                        help="Override registry DB path "
                             "(default: $ACIDCAT_REGISTRY or "
                             "~/.acidcat/registry.db).")
    parser.add_argument("--version", action="version",
                        version=f"acidcat-mcp {__version__}")
    args = parser.parse_args(argv)
    _REGISTRY_PATH = args.registry  # None means: use defaults

    _warn_legacy_db()

    import asyncio
    asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
