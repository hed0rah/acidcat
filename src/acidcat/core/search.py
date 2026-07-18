"""Compatible-sample search: find samples that mix with a reference, by harmonic
key (Camelot wheel: same / relative / perfect fourth / fifth) and compatible
tempo (a percent window, plus half- and double-time). Fanned out across
registered libraries. Shared by the MCP `find_compatible` tool and the CLI
`query --compatible-with`, so the harmonic/kind logic lives once in core.

Design notes (from the architecture review):
- Key matching runs in Python, normalizing each candidate's key via camelot, so
  it is robust to spelling ('Am' / 'A minor' / 'Amin'). The SQL prefilter only
  does the BPM windows + kind + a key-presence guard.
- BPM windows are OR'd exact ranges (same / half / double), and the per-DB fetch
  over-fetches then Python scores+sorts, so a distant half-time match is never
  truncated behind an in-band non-match.
- A keyless reference (drum loops) matches only other keyless samples; matching
  keyed samples on BPM alone is musically meaningless.
"""

import json
import math
import os

from acidcat.core import camelot
from acidcat.core import index as idx
from acidcat.core import paths as acidpaths

_KEY_RELATIONS = ("same key", "relative", "perfect fourth", "perfect fifth")
_KEY_RANK = {"same key": 0, "relative": 1, "perfect fifth": 2, "perfect fourth": 3}
_BPM_RANK = {"same tempo": 0, "half-time": 1, "double-time": 1}


def infer_kind(duration, acid_beats):
    """Classify a sample as 'loop' / 'one_shot' / 'any' from length + beats."""
    d = duration or 0.0
    b = acid_beats or 0
    if b > 0 or d >= 2.0:
        return "loop"
    if d < 1.0 and b <= 0:
        return "one_shot"
    return "any"


def resolve_reference(path, libs):
    """(row_dict, source, library_label) for a reference file: from the index if
    indexed, else parsed on the fly. row_dict carries at least
    key/bpm/duration/acid_beats. Returns (None, 'unresolved', None) if neither
    works."""
    canon = acidpaths.normalize(path)
    for lib in libs:
        try:
            conn = idx.open_db(lib["db_path"])
        except Exception:
            continue
        try:
            row = conn.execute("SELECT * FROM samples WHERE path = ?",
                               (canon,)).fetchone()
        except Exception:
            row = None
        finally:
            conn.close()
        if row:
            return dict(row), "index", lib["label"]
    try:                                        # not indexed: parse it directly
        from acidcat.core import indexing
        st = os.stat(path)
        r = indexing._extract_for_index(path, os.path.dirname(path) or ".",
                                        st.st_mtime, st.st_size, 0.0, quiet=True)
        if r:
            return r, "parsed", None
    except Exception:
        pass
    return None, "unresolved", None


def _bpm_windows(bpm, tol, half_double):
    if not bpm or bpm <= 0:
        return []
    centers = [(bpm, "same tempo")]
    if half_double:
        centers += [(bpm / 2, "half-time"), (bpm * 2, "double-time")]
    return [(c * (1 - tol), c * (1 + tol), label) for c, label in centers]


def compatible_codes(key, include_relative=True, same_key_only=False):
    """{camelot_code: relation label} for a key, or {} if unparseable."""
    code = camelot.key_to_camelot(key) if key else None
    if not code:
        return {}
    neighbours = camelot.camelot_neighbors(code)
    if same_key_only:
        return {code: "same key"}
    out = {}
    for i, c in enumerate(neighbours):
        label = _KEY_RELATIONS[i] if i < len(_KEY_RELATIONS) else "compatible"
        if not include_relative and label == "relative":
            continue
        out[c] = label
    return out


def find_compatible(libs, *, key=None, bpm=None, kind="any", bpm_tol=0.06,
                    half_double=True, same_key_only=False, include_relative=True,
                    min_duration=None, limit=50, exclude_path=None):
    """Rows compatible with (key, bpm), each with a 'compatibility' note and
    'library_label', sorted closest first. A missing key/bpm dimension is not
    required. kind ('loop'/'one_shot'/'any') filters candidate length/beats."""
    codes = compatible_codes(key, include_relative, same_key_only)
    windows = _bpm_windows(bpm, bpm_tol, half_double)
    keyless_ref = not codes
    eff_kind = (kind or "any").lower()
    exclude = acidpaths.normalize(exclude_path) if exclude_path else None

    where, params = [], []
    if exclude:
        where.append("s.path != ?")
        params.append(exclude)
    if windows:
        where.append("(" + " OR ".join("s.bpm BETWEEN ? AND ?" for _ in windows) + ")")
        for lo, hi, _ in windows:
            params += [lo, hi]
    if keyless_ref:
        where.append("(s.key IS NULL OR s.key = '')")
    else:
        where.append("s.key IS NOT NULL AND s.key != ''")
    if eff_kind == "loop":
        where.append("(s.acid_beats > 0 OR s.duration >= 2.0)")
    elif eff_kind == "one_shot":
        where.append("((s.acid_beats IS NULL OR s.acid_beats = 0) "
                     "AND (s.duration IS NULL OR s.duration < 1.0))")
    if min_duration is not None:
        where.append("s.duration >= ?")
        params.append(float(min_duration))

    sql = "SELECT s.* FROM samples s"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" LIMIT {max(limit * 4, 200)}"       # over-fetch; Python scores + sorts

    def evaluate(rkey, rbpm):
        key_rel = None
        if not keyless_ref:
            key_rel = codes.get(camelot.key_to_camelot(rkey) if rkey else None)
            if key_rel is None:
                return None
        bpm_rel, bpm_dist = None, 0.0
        if windows:
            best = None
            for lo, hi, label in windows:
                if rbpm is not None and lo <= rbpm <= hi:
                    d = abs(rbpm - (lo + hi) / 2)
                    if best is None or d < best[1]:
                        best = (label, d)
            if best is None:
                return None
            bpm_rel, bpm_dist = best
        return key_rel, bpm_rel, bpm_dist

    scored, seen = [], set()
    for lib in libs:
        try:
            conn = idx.open_db(lib["db_path"])
        except Exception:
            continue
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()
        for r in rows:
            d = dict(r)
            d.pop("id", None)         # internal rowid alias, not part of the result
            p = d.get("path")
            if p in seen:
                continue
            ev = evaluate(d.get("key"), d.get("bpm"))
            if ev is None:
                continue
            seen.add(p)
            key_rel, bpm_rel, bpm_dist = ev
            d["compatibility"] = ", ".join(x for x in (key_rel, bpm_rel) if x) or "compatible"
            d["library_label"] = lib["label"]
            rank = _KEY_RANK.get(key_rel, 4) * 3 + _BPM_RANK.get(bpm_rel, 2)
            scored.append((rank, bpm_dist, p or "", d))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return [d for _, _, _, d in scored[:limit]]


# ── similarity search (feature-vector cosine, index-backed) ──────────────────
# Shared by the MCP `find_similar` tool and the CLI `acidcat similar` verb, so
# the fan-out + standardized-cosine scoring lives once in core (the no-drift
# rule). The caller resolves the target's features (from the index or a live
# librosa extract) and hands them in; this module does the fan-out and ranking.


def resolve_target_features(path, libs):
    """(features_dict, meta) for a reference file's stored features, or
    (None, None) if it is not indexed anywhere. meta carries duration +
    acid_beats for kind inference. Mirrors resolve_reference: index-first, and
    the caller decides whether to fall back to a live extract."""
    for cand in (acidpaths.normalize(path), path):
        for lib in libs:
            try:
                conn = idx.open_db(lib["db_path"])
            except Exception:
                continue
            try:
                feats = idx.get_features(conn, cand)
                if feats is None:
                    continue
                row = conn.execute(
                    "SELECT duration, acid_beats FROM samples WHERE path = ?",
                    (cand,)).fetchone()
            except Exception:
                feats, row = None, None
            finally:
                conn.close()
            if feats is not None:
                meta = ({"duration": row["duration"],
                         "acid_beats": row["acid_beats"]} if row is not None
                        else {"duration": None, "acid_beats": None})
                return feats, meta
    return None, None


def find_similar(libs, target_features, target_meta=None, *, n=5, kind=None,
                 kind_filter=True, exclude_path=None):
    """Rank indexed samples by z-standardized cosine over the feature vector.

    ``target_features`` is the reference's features dict (index or live extract).
    ``kind`` (loop/one_shot/any) overrides the inferred target kind; when kind is
    None and kind_filter is True the target's own kind filters candidates.
    Returns {target_kind, filter_kind, population, results} where each result
    carries path/bpm/key/duration/format/library_label/similarity plus population
    stats (percentile_rank, similarity_above_mean). Raises ValueError when the
    target has no usable vector or kind is invalid."""
    from acidcat.core import features as feat

    target_meta = target_meta or {}
    target_kind = infer_kind(target_meta.get("duration"),
                             target_meta.get("acid_beats"))
    if kind and kind not in ("loop", "one_shot", "any"):
        raise ValueError(f"kind must be loop, one_shot, or any (got {kind!r})")
    effective_kind = kind or (target_kind if kind_filter else "any")

    tv = feat.vector_from_features(target_features)
    if not tv or not any(tv):
        raise ValueError("no usable features for target")
    dims = len(tv)

    exclude = acidpaths.normalize(exclude_path) if exclude_path else None
    kind_where = ""
    if effective_kind == "loop":
        kind_where = " WHERE (s.acid_beats > 0 OR s.duration >= 2.0)"
    elif effective_kind == "one_shot":
        kind_where = (" WHERE ((s.acid_beats IS NULL OR s.acid_beats = 0) "
                      "AND (s.duration IS NULL OR s.duration < 1.0))")
    sql = ("SELECT f.path, f.feature_vec, f.features_json, s.bpm, s.key, "
           "s.duration, s.format, s.acid_beats "
           "FROM features f JOIN samples s ON s.path = f.path" + kind_where)

    # deepest-root-first so dedup keeps the most-specific library's row
    # (libs may be sqlite3.Row, which has no .get())
    def _root_len(lib):
        try:
            return len(lib["root_path"] or "")
        except (KeyError, IndexError):
            return 0
    libs = sorted(libs, key=lambda r: -_root_len(r))
    vecs, meta = [], []
    for lib in libs:
        try:
            conn = idx.open_db(lib["db_path"])
        except Exception:
            continue
        try:
            rows = conn.execute(sql).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()
        for r in rows:
            if exclude and r["path"] == exclude:
                continue
            v = idx.unpack_vector(r["feature_vec"], dims)
            if v is None:                    # older row without a packed vector
                try:
                    v = feat.vector_from_features(json.loads(r["features_json"]))
                except (ValueError, TypeError):
                    v = None
                if v is not None and len(v) != dims:
                    v = None
            if v is None:
                continue
            vecs.append(v)
            meta.append((r["path"], r["bpm"], r["key"], r["duration"],
                         r["format"], lib["label"]))

    population = len(vecs)
    scored = []
    if population:
        sims = _standardized_cosine(tv, vecs)
        for i, (p, bpm, key, dur, fmt, label) in enumerate(meta):
            scored.append({
                "path": p, "bpm": bpm, "key": key, "duration": dur,
                "format": fmt, "library_label": label,
                "similarity": round(float(sims[i]), 6),
            })
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    seen, deduped = set(), []
    for row in scored:
        if row["path"] in seen:
            continue
        seen.add(row["path"])
        deduped.append(row)
    scored = deduped

    # population stats over the full candidate set: percentile rank and distance
    # from the mean, which separate results inside the tight 0.99x clusters that
    # same-pack samples produce
    pop_n = len(scored)
    if pop_n:
        allsims = [s["similarity"] for s in scored]
        pop_mean = sum(allsims) / pop_n
        for item in scored:
            below = sum(1 for s in allsims if s < item["similarity"])
            item["percentile_rank"] = round(100.0 * below / pop_n, 1)
            item["similarity_above_mean"] = round(
                item["similarity"] - pop_mean, 6)

    return {
        "target_kind": target_kind,
        "filter_kind": effective_kind,
        "population": population,
        "results": scored[:n],
    }


def _standardized_cosine(target, cands):
    """Cosine similarity between `target` and each vector in `cands`, after
    per-dimension z-standardization across candidates + target. Standardizing is
    the correctness fix: the raw spectral dims (10^3-10^6) otherwise dominate the
    cosine and pin every score near 0.99, so ranking reflects scale, not timbre.

    Uses numpy when importable (vectorized); otherwise a pure-Python fallback
    with identical math (population std, ddof=0), since a shared index may be
    scored where the analysis extra is not installed."""
    try:
        import numpy as np
    except ImportError:
        return _standardized_cosine_py(target, cands)
    M = np.asarray(cands, dtype=np.float64)
    t = np.asarray(target, dtype=np.float64)
    allv = np.vstack([M, t])
    mu = allv.mean(axis=0)
    sd = allv.std(axis=0)
    sd[sd == 0] = 1.0
    Mz = (M - mu) / sd
    tz = (t - mu) / sd
    denom = np.linalg.norm(Mz, axis=1) * np.linalg.norm(tz)
    denom[denom == 0] = np.inf
    return list((Mz @ tz) / denom)


def _standardized_cosine_py(target, cands):
    """Pure-Python twin of _standardized_cosine (no numpy). See that docstring."""
    dims = len(target)
    cnt = len(cands) + 1
    mu = list(target)
    for v in cands:
        for j in range(dims):
            mu[j] += v[j]
    mu = [s / cnt for s in mu]
    var = [(target[j] - mu[j]) ** 2 for j in range(dims)]
    for v in cands:
        for j in range(dims):
            d = v[j] - mu[j]
            var[j] += d * d
    sd = [math.sqrt(x / cnt) if x > 0 else 1.0 for x in var]
    tz = [(target[j] - mu[j]) / sd[j] for j in range(dims)]
    tn = math.sqrt(sum(x * x for x in tz))
    out = []
    for v in cands:
        vz = [(v[j] - mu[j]) / sd[j] for j in range(dims)]
        vn = math.sqrt(sum(x * x for x in vz))
        denom = vn * tn
        out.append(sum(a * b for a, b in zip(vz, tz)) / denom if denom else 0.0)
    return out
