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
