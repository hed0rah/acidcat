"""
acidcat-mcp -- stdio MCP server over the global SQLite sample index.

Register all tools up front even when optional deps (librosa) are missing;
the slow ones return a structured error describing the install step. This
way the LLM discovers what's possible and can surface the fix to the user.
"""

import argparse
import importlib.util
import json
import math
import os
import sys
import time

from acidcat import __version__
from acidcat.core import index as idx
from acidcat.core import camelot


# ── server config ─────────────────────────────────────────────────

_DB_PATH = None


def _get_conn():
    """Open a fresh connection per tool call -- cheap for SQLite."""
    return idx.open_db(_DB_PATH)


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


# ── helpers ───────────────────────────────────────────────────────


def _require_path(args, field="path"):
    v = args.get(field)
    if not v:
        raise ToolError(f"{field} is required")
    return v


def _resolve_stored_path(conn, user_path):
    """Find the canonical stored path for a user-provided path.

    Tries the normalized form first, then the raw form. Returns the stored
    path, or None if the sample is not indexed.
    """
    norm = idx.normalize_path(user_path)
    for candidate in (norm, user_path):
        row = conn.execute(
            "SELECT path FROM samples WHERE path = ?", (candidate,)
        ).fetchone()
        if row is not None:
            return row["path"]
    return None


def _librosa_available():
    """Cheap check: does Python see librosa + numpy on the import path?

    Uses find_spec so this never actually imports librosa (which would
    cold-start the numba JIT chain and add tens of seconds to the first
    call of any tool that probes availability).
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
    conn = _get_conn()
    try:
        where, params, joins = [], [], []

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
        if args.get("root"):
            root = idx.normalize_path(args["root"])
            where.append("(s.scan_root = ? OR s.path LIKE ?)")
            params.append(root)
            params.append(root.rstrip("/") + "/%")

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
        params.append(limit)

        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return {"count": len(rows), "samples": rows}
    finally:
        conn.close()


def get_sample(args):
    path = _require_path(args)
    conn = _get_conn()
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
        return out
    finally:
        conn.close()


def locate_sample(args):
    name = args.get("name")
    if not name:
        raise ToolError("name is required")
    limit = int(args.get("limit") or 10)
    conn = _get_conn()
    try:
        like = "%/" + name + "%"
        rows = conn.execute(
            "SELECT path, scan_root, format, bpm, key, duration "
            "FROM samples WHERE path LIKE ? ORDER BY path LIMIT ?",
            (like, limit),
        ).fetchall()
        return {"count": len(rows), "samples": [dict(r) for r in rows]}
    finally:
        conn.close()


def list_roots(_args):
    conn = _get_conn()
    try:
        return {"roots": idx.list_roots(conn)}
    finally:
        conn.close()


def list_tags(args):
    conn = _get_conn()
    try:
        prefix = args.get("prefix") or ""
        if prefix:
            rows = conn.execute(
                "SELECT tag, COUNT(*) AS count FROM tags "
                "WHERE tag LIKE ? GROUP BY tag ORDER BY count DESC, tag",
                (prefix + "%",),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT tag, COUNT(*) AS count FROM tags "
                "GROUP BY tag ORDER BY count DESC, tag"
            ).fetchall()
        return {"tags": [dict(r) for r in rows]}
    finally:
        conn.close()


def list_keys(_args):
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT key, COUNT(*) AS count FROM samples "
            "WHERE key IS NOT NULL GROUP BY key ORDER BY count DESC, key"
        ).fetchall()
        return {"keys": [dict(r) for r in rows]}
    finally:
        conn.close()


def list_formats(_args):
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT format, COUNT(*) AS count FROM samples "
            "WHERE format IS NOT NULL GROUP BY format ORDER BY count DESC"
        ).fetchall()
        return {"formats": [dict(r) for r in rows]}
    finally:
        conn.close()


def index_stats(_args):
    conn = _get_conn()
    try:
        stats = idx.index_stats(conn)
        stats["analysis_available"] = _librosa_available()
        stats["db_path"] = _DB_PATH
        return stats
    finally:
        conn.close()


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

    conn = _get_conn()
    try:
        resolved = _resolve_stored_path(conn, path)
        if resolved is None:
            raise ToolError(f"sample not indexed: {path}")
        target = conn.execute(
            "SELECT * FROM samples WHERE path = ?", (resolved,)
        ).fetchone()

        target_bpm = target["bpm"]
        target_key = target["key"]
        target_kind = infer_kind(target["duration"], target["acid_beats"])

        # default kind: match the target's own classification (so loops return
        # loops, one-shots return one-shots). 'any' skips kind filtering.
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

        # expand each compatible key to all enharmonic spellings so a DB row
        # stored as "Cb" still matches when target implies "B", etc.
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
        params.append(target_bpm or 0)
        params.append(limit)

        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return {
            "target": {
                "path": resolved,
                "bpm": target_bpm,
                "key": target_key,
                "camelot": camelot.key_to_camelot(target_key) if target_key else None,
                "kind": target_kind,
            },
            "compatible_keys": sorted(compat_keys),
            "filter_kind": effective_kind,
            "count": len(rows),
            "samples": rows,
        }
    finally:
        conn.close()


# ── analysis tools (slow) ─────────────────────────────────────────


def find_similar(args):
    path = _require_path(args)
    n = int(args.get("n") or 5)

    conn = _get_conn()
    try:
        norm = idx.normalize_path(path)
        target_feats = idx.get_features(conn, norm) or idx.get_features(conn, path)

        if target_feats is None:
            if not _librosa_available():
                return _analysis_unavailable()
            from acidcat.core.features import extract_audio_features
            target_feats = extract_audio_features(path)
            if target_feats is None:
                raise ToolError(f"could not extract features from {path}")

        feature_keys = sorted(
            k for k, v in target_feats.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        )
        if not feature_keys:
            raise ToolError("no numeric features available")

        tv = [float(target_feats[k]) for k in feature_keys]

        rows = conn.execute(
            "SELECT f.path, f.features_json, s.bpm, s.key, s.duration, s.format "
            "FROM features f JOIN samples s ON s.path = f.path"
        ).fetchall()

        scored = []
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
                "similarity": round(sim, 6),
            })
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        # filter out the target itself
        target_path = idx.normalize_path(path)
        scored = [s for s in scored if s["path"] != target_path][:n]
        return {
            "target": path,
            "population": len(rows),
            "results": scored,
        }
    finally:
        conn.close()


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
    path = _require_path(args)
    with_features = bool(args.get("with_features", False))
    if not os.path.isdir(path):
        raise ToolError(f"not a directory: {path}")

    from acidcat.commands.index import _walk_and_upsert

    conn = _get_conn()
    try:
        scan_root = idx.normalize_path(path)
        counts = _walk_and_upsert(
            conn, scan_root,
            do_features=with_features,
            do_deep=False,
            quiet=True,
        )
        conn.commit()
        return {"root": scan_root, "counts": counts}
    finally:
        conn.close()


def reindex_features(args):
    limit = args.get("limit")
    if not _librosa_available():
        return _analysis_unavailable()
    from acidcat.core.features import extract_audio_features

    conn = _get_conn()
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

        processed = failed = 0
        for r in rows:
            path = r["path"]
            if not os.path.isfile(path):
                failed += 1
                continue
            feats = extract_audio_features(path)
            if feats is None:
                failed += 1
                continue
            idx.upsert_features(conn, path, feats, version=1)
            processed += 1
        conn.commit()
        return {"processed": processed, "failed": failed,
                "remaining_unprocessed": len(rows) - processed - failed}
    finally:
        conn.close()


# ── write tools ───────────────────────────────────────────────────


def tag_sample(args):
    path = _require_path(args)
    add = args.get("add_tags") or []
    remove = args.get("remove_tags") or []
    if not add and not remove:
        raise ToolError("provide add_tags and/or remove_tags")

    conn = _get_conn()
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
        return {"path": resolved, "tags": tags}
    finally:
        conn.close()


def describe_sample(args):
    path = _require_path(args)
    description = args.get("description")
    conn = _get_conn()
    try:
        resolved = _resolve_stored_path(conn, path)
        if resolved is None:
            raise ToolError(f"sample not indexed: {path}")
        idx.upsert_description(conn, resolved, description or "")
        conn.commit()
        return {"path": resolved, "description": description or ""}
    finally:
        conn.close()


# ── tool registration ─────────────────────────────────────────────


def _register_all():
    # fast
    _tool(
        "search_samples",
        "Fast. Filter the sample index by bpm/key/duration/tags/text/format/root. "
        "Prefer this over analysis tools for any discovery query.",
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
                         "description": "Scope to samples under this scan root."},
                "limit": {"type": "integer", "default": 50},
            },
        },
        search_samples,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "get_sample",
        "Fast. Full metadata for one sample path, including tags and description.",
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
        "Fast. Find a sample by filename substring across all indexed roots. "
        "Use this to answer 'where is X?' questions.",
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
        "list_roots",
        "Fast. Indexed scan roots with file counts and last-indexed times.",
        {"type": "object", "properties": {}},
        list_roots,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "list_tags",
        "Fast. Distinct tags with counts. Use 'prefix' to narrow.",
        {
            "type": "object",
            "properties": {"prefix": {"type": "string"}},
        },
        list_tags,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "list_keys",
        "Fast. Distinct musical keys with counts.",
        {"type": "object", "properties": {}},
        list_keys,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "list_formats",
        "Fast. Distinct file formats with counts.",
        {"type": "object", "properties": {}},
        list_formats,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "index_stats",
        "Fast. Totals, per-format counts, analysis availability, DB path.",
        {"type": "object", "properties": {}},
        index_stats,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "find_compatible",
        "Fast. Harmonically and rhythmically compatible samples via Camelot + "
        "BPM tolerance. By default filters to the target's own kind (loops "
        "match loops, one-shots match one-shots) so a kalimba loop query "
        "does not return kalimba one-shots. No audio analysis; metadata-only.",
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
                        "target (loop vs one-shot based on acid_beats+duration).",
                },
                "min_duration": {
                    "type": "number",
                    "description":
                        "Optional seconds floor. Overrides/augments kind "
                        "filter when you want a specific minimum length.",
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
        "acidcat[analysis]. Nearest neighbors by librosa feature cosine. "
        "Only use when metadata-based tools (search_samples, find_compatible) "
        "cannot answer.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "n": {"type": "integer", "default": 5},
            },
            "required": ["path"],
        },
        find_similar,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "analyze_sample",
        "SLOW (~1-10s). Requires acidcat[analysis]. On-the-fly librosa feature "
        "extraction for an unindexed file. Prefer get_sample for indexed files.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "deep": {"type": "boolean", "default": False},
            },
            "required": ["path"],
        },
        analyze_sample,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )
    _tool(
        "detect_bpm_key",
        "SLOW (~0.5-2s). Requires acidcat[analysis]. BPM + key estimation only. "
        "Cheaper than analyze_sample. Prefer get_sample when possible.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        detect_bpm_key,
        {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    )

    # reindex (operational, slow)
    _tool(
        "reindex",
        "SLOW. Only call when the user explicitly asks to refresh the index. "
        "Walks a directory and upserts samples incrementally.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "with_features": {"type": "boolean", "default": False},
            },
            "required": ["path"],
        },
        reindex,
        {"readOnlyHint": False, "destructiveHint": False,
         "idempotentHint": True, "openWorldHint": False},
    )
    _tool(
        "reindex_features",
        "VERY SLOW. Requires acidcat[analysis]. Extracts librosa features for "
        "any indexed samples that do not have them yet. Only call when the "
        "user explicitly asks.",
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer",
                          "description": "Max files to process this call."},
            },
        },
        reindex_features,
        {"readOnlyHint": False, "destructiveHint": False,
         "idempotentHint": True, "openWorldHint": False},
    )

    # write tools
    _tool(
        "tag_sample",
        "Modify the index. Add/remove tags on a sample. Confirm with the user "
        "before calling.",
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
        "Modify the index. Set or clear the free-text description for a sample. "
        "Confirm with the user before calling.",
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
    """Call a tool by name with a dict of arguments. Raises ToolError or returns dict."""
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


def main(argv=None):
    global _DB_PATH
    parser = argparse.ArgumentParser(
        prog="acidcat-mcp",
        description="MCP server exposing the acidcat sample index over stdio.",
    )
    parser.add_argument("--db", help="Override DB path (default: "
                                     "$ACIDCAT_DB or ~/.acidcat/index.db).")
    parser.add_argument("--version", action="version",
                        version=f"acidcat-mcp {__version__}")
    args = parser.parse_args(argv)
    _DB_PATH = idx.resolve_db_path(args.db)

    import asyncio
    asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
