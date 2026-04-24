"""Tests for the MCP tool surface (direct handler calls, bypass stdio)."""

import json
import time

import pytest

from acidcat import mcp_server
from acidcat.core import index as idx


ROOT_A = idx.normalize_path("/tmp/mcp/a")
ROOT_B = idx.normalize_path("/tmp/mcp/b")
P_KICK = ROOT_A + "/kick_120.wav"
P_HAT = ROOT_A + "/hat_128.wav"
P_SYNTH = ROOT_B + "/synth_124.flac"


def _seed(db_path):
    conn = idx.open_db(db_path)
    now = time.time()
    rows = [
        {"path": P_KICK, "scan_root": ROOT_A, "format": "wav",
         "duration": 1.2, "bpm": 120.0, "key": "C",
         "title": "kick one", "comment": "booming sub",
         "mtime": now, "size": 100, "indexed_at": now, "last_seen_at": now,
         "chunks": "acid,smpl", "acid_beats": 4, "root_note": 60,
         "sample_rate": 44100, "channels": 1, "bits_per_sample": 16,
         "artist": None, "album": None, "genre": None},
        {"path": P_HAT, "scan_root": ROOT_A, "format": "wav",
         "duration": 0.5, "bpm": 128.0, "key": "Am",
         "title": "closed hat", "mtime": now, "size": 100,
         "indexed_at": now, "last_seen_at": now,
         "artist": None, "album": None, "genre": None, "comment": None,
         "chunks": None, "acid_beats": None, "root_note": None,
         "sample_rate": None, "channels": None, "bits_per_sample": None},
        {"path": P_SYNTH, "scan_root": ROOT_B, "format": "flac",
         "duration": 8.0, "bpm": 124.0, "key": "Em",
         "title": "dusty synth", "artist": "someone",
         "album": "dust pack", "genre": "electronic",
         "comment": "warm and lofi",
         "mtime": now, "size": 200, "indexed_at": now, "last_seen_at": now,
         "chunks": None, "acid_beats": None, "root_note": None,
         "sample_rate": 44100, "channels": 2, "bits_per_sample": 16},
    ]
    for r in rows:
        idx.upsert_sample(conn, r)
    idx.upsert_tags(conn, P_KICK, ["drums", "kick", "punchy"])
    idx.upsert_tags(conn, P_HAT, ["drums", "hat"])
    idx.upsert_tags(conn, P_SYNTH, ["synth", "pad"])
    idx.upsert_description(conn, P_SYNTH, "moody analog pad")
    idx.record_scan_root(conn, ROOT_A, 2, now)
    idx.record_scan_root(conn, ROOT_B, 1, now)
    conn.commit()
    conn.close()


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    db = tmp_path / "mcp.db"
    _seed(str(db))
    monkeypatch.setattr(mcp_server, "_DB_PATH", str(db))
    return str(db)


def test_tools_registered():
    names = {t["name"] for t in mcp_server.TOOLS}
    expected = {
        "search_samples", "get_sample", "locate_sample", "list_roots",
        "list_tags", "list_keys", "list_formats", "index_stats",
        "find_compatible",
        "find_similar", "analyze_sample", "detect_bpm_key",
        "reindex", "reindex_features",
        "tag_sample", "describe_sample",
    }
    assert expected.issubset(names)


def test_fast_tools_have_fast_prefix():
    fast_names = {"search_samples", "get_sample", "locate_sample",
                  "list_roots", "list_tags", "list_keys", "list_formats",
                  "index_stats", "find_compatible"}
    for t in mcp_server.TOOLS:
        if t["name"] in fast_names:
            assert t["description"].startswith("Fast."), t["name"]


def test_slow_tools_have_slow_prefix():
    slow_names = {"find_similar", "analyze_sample", "detect_bpm_key",
                  "reindex", "reindex_features"}
    for t in mcp_server.TOOLS:
        if t["name"] in slow_names:
            assert t["description"].startswith(("SLOW", "VERY SLOW")), t["name"]


def test_write_tools_marked_destructive():
    for t in mcp_server.TOOLS:
        if t["name"] in ("tag_sample", "describe_sample"):
            assert t["annotations"]["destructiveHint"] is True
            assert t["annotations"]["readOnlyHint"] is False


def test_fast_tools_read_only():
    fast = {"search_samples", "get_sample", "locate_sample", "list_roots",
            "list_tags", "list_keys", "list_formats", "index_stats",
            "find_compatible"}
    for t in mcp_server.TOOLS:
        if t["name"] in fast:
            assert t["annotations"]["readOnlyHint"] is True
            assert t["annotations"]["destructiveHint"] is False


def test_search_samples(seeded_db):
    r = mcp_server.dispatch("search_samples", {"bpm_min": 120, "bpm_max": 125})
    paths = {s["path"] for s in r["samples"]}
    assert paths == {P_KICK, P_SYNTH}


def test_search_samples_text(seeded_db):
    r = mcp_server.dispatch("search_samples", {"text": "dusty"})
    assert r["count"] == 1
    assert r["samples"][0]["path"] == P_SYNTH


def test_search_samples_tags_and(seeded_db):
    r = mcp_server.dispatch("search_samples", {"tags": ["drums", "kick"]})
    assert r["count"] == 1
    assert r["samples"][0]["path"] == P_KICK


def test_get_sample(seeded_db):
    r = mcp_server.dispatch("get_sample", {"path": P_SYNTH})
    assert r["path"] == P_SYNTH
    assert r["tags"] == ["pad", "synth"]
    assert r["description"] == "moody analog pad"
    assert r["has_features"] is False


def test_get_sample_not_indexed(seeded_db):
    with pytest.raises(mcp_server.ToolError):
        mcp_server.dispatch("get_sample", {"path": "/nope.wav"})


def test_locate_sample(seeded_db):
    r = mcp_server.dispatch("locate_sample", {"name": "synth"})
    assert r["count"] == 1
    assert r["samples"][0]["path"] == P_SYNTH


def test_list_roots(seeded_db):
    r = mcp_server.dispatch("list_roots", {})
    paths = {root["path"] for root in r["roots"]}
    assert paths == {ROOT_A, ROOT_B}


def test_list_tags(seeded_db):
    r = mcp_server.dispatch("list_tags", {})
    tag_names = [t["tag"] for t in r["tags"]]
    assert "drums" in tag_names
    # drums count is 2 (kick + hat)
    tag_map = {t["tag"]: t["count"] for t in r["tags"]}
    assert tag_map["drums"] == 2


def test_list_tags_prefix(seeded_db):
    r = mcp_server.dispatch("list_tags", {"prefix": "dr"})
    tag_names = [t["tag"] for t in r["tags"]]
    assert tag_names == ["drums"]


def test_list_keys(seeded_db):
    r = mcp_server.dispatch("list_keys", {})
    keys = {k["key"] for k in r["keys"]}
    assert keys == {"C", "Am", "Em"}


def test_list_formats(seeded_db):
    r = mcp_server.dispatch("list_formats", {})
    formats = {f["format"] for f in r["formats"]}
    assert formats == {"wav", "flac"}


def test_index_stats(seeded_db):
    r = mcp_server.dispatch("index_stats", {})
    assert r["total_samples"] == 3
    assert "analysis_available" in r
    assert r["db_path"] == seeded_db


def test_find_compatible_loops_match_loops(seeded_db):
    # P_KICK: duration 1.2, acid_beats 4 -> loop (acid_beats > 0)
    # P_SYNTH: duration 8.0, no acid_beats -> loop (duration >= 2.0)
    # P_HAT:   duration 0.5, no acid_beats -> one_shot
    # P_KICK is key C (8B); compatible with Am, Em, F, G. None of seeds match.
    # Run with wider tolerance and check synth (Em) loop shows up.
    r = mcp_server.dispatch("find_compatible", {
        "path": P_SYNTH, "bpm_tolerance_pct": 20,
    })
    assert r["target"]["kind"] == "loop"
    assert r["filter_kind"] == "loop"
    # P_KICK and P_HAT are both compatible by key (Em -> compatible with C and G);
    # only the loop (P_KICK) should come back.
    paths = {s["path"] for s in r["samples"]}
    # C is in compatible_keys for Em target (Em=9A -> 9B=G, 10A=Bm, 8A=Am)...
    # so the exact membership depends on the Camelot wheel. What matters:
    # the one_shot P_HAT MUST NOT appear.
    assert P_HAT not in paths


def test_find_compatible_default_excludes_cross_kind(seeded_db):
    # Target is a one_shot (P_HAT). Default kind filter should exclude loops.
    r = mcp_server.dispatch("find_compatible", {"path": P_HAT})
    assert r["target"]["kind"] == "one_shot"
    assert r["filter_kind"] == "one_shot"
    paths = {s["path"] for s in r["samples"]}
    # P_SYNTH is a loop, P_KICK is a loop (acid_beats=4); both excluded.
    assert P_SYNTH not in paths
    assert P_KICK not in paths


def test_find_compatible_kind_any_overrides(seeded_db):
    # With kind="any", the old behavior is back: a one_shot target finds loops.
    r = mcp_server.dispatch("find_compatible", {
        "path": P_HAT, "kind": "any",
    })
    assert r["filter_kind"] == "any"
    paths = {s["path"] for s in r["samples"]}
    assert P_SYNTH in paths


def test_find_compatible_min_duration_override(seeded_db):
    r = mcp_server.dispatch("find_compatible", {
        "path": P_HAT, "kind": "any", "min_duration": 2.0,
    })
    paths = {s["path"] for s in r["samples"]}
    # P_KICK (1.2s) excluded, P_SYNTH (8s) kept
    assert P_KICK not in paths
    assert P_SYNTH in paths


def test_find_compatible_infer_kind_helper():
    assert mcp_server.infer_kind(0.5, 0) == "one_shot"
    assert mcp_server.infer_kind(0.5, None) == "one_shot"
    assert mcp_server.infer_kind(8.0, 0) == "loop"
    assert mcp_server.infer_kind(1.2, 4) == "loop"
    # 1.0 to 2.0 with no beats is genuinely ambiguous
    assert mcp_server.infer_kind(1.5, 0) == "any"


def test_tag_sample(seeded_db):
    r = mcp_server.dispatch("tag_sample", {
        "path": P_HAT, "add_tags": ["bright", "tight"],
    })
    assert "bright" in r["tags"]
    assert "tight" in r["tags"]

    r2 = mcp_server.dispatch("tag_sample", {
        "path": P_HAT, "remove_tags": ["bright"],
    })
    assert "bright" not in r2["tags"]
    assert "tight" in r2["tags"]


def test_describe_sample(seeded_db):
    r = mcp_server.dispatch("describe_sample", {
        "path": P_HAT, "description": "crispy closed hat",
    })
    assert r["description"] == "crispy closed hat"
    got = mcp_server.dispatch("get_sample", {"path": P_HAT})
    assert got["description"] == "crispy closed hat"


def test_analysis_tools_degrade_without_librosa(seeded_db, monkeypatch):
    monkeypatch.setattr(mcp_server, "_librosa_available", lambda: False)

    r = mcp_server.dispatch("analyze_sample", {"path": P_HAT})
    assert "error" in r
    assert "acidcat[analysis]" in r["fix"]

    r = mcp_server.dispatch("detect_bpm_key", {"path": P_HAT})
    assert "error" in r

    r = mcp_server.dispatch("reindex_features", {})
    assert "error" in r


def test_find_similar_no_features_no_librosa(seeded_db, monkeypatch):
    monkeypatch.setattr(mcp_server, "_librosa_available", lambda: False)
    r = mcp_server.dispatch("find_similar", {"path": P_HAT})
    assert "error" in r


def test_unknown_tool(seeded_db):
    with pytest.raises(mcp_server.ToolError):
        mcp_server.dispatch("nope", {})
