"""Tests for the MCP tool surface (per-library + registry layout)."""

import json
import os
import time

import pytest

from acidcat import mcp_server
from acidcat.core import index as idx
from acidcat.core import paths as acidpaths
from acidcat.core import registry as reg


@pytest.fixture
def two_lib_setup(tmp_path, monkeypatch):
    """Build two real registered libraries with seeded sample rows.

    Sets _REGISTRY_PATH on mcp_server so handlers see this isolated
    registry. Returns paths used by the assertions.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    registry_path = str(tmp_path / "registry.db")
    monkeypatch.setattr(mcp_server, "_REGISTRY_PATH", registry_path)

    lib_a_root_dir = tmp_path / "libA"
    lib_a_root_dir.mkdir()
    lib_b_root_dir = tmp_path / "libB"
    lib_b_root_dir.mkdir()

    a_root = acidpaths.normalize(str(lib_a_root_dir))
    b_root = acidpaths.normalize(str(lib_b_root_dir))
    p_kick = a_root + "/kick_120.wav"
    p_hat = a_root + "/hat_128.wav"
    p_synth = b_root + "/synth_124.flac"

    rconn = reg.open_registry(registry_path)
    try:
        db_a = acidpaths.central_db_path_for(a_root, "A")
        db_b = acidpaths.central_db_path_for(b_root, "B")
        reg.register_library(rconn, a_root, label="A", db_path=db_a)
        reg.register_library(rconn, b_root, label="B", db_path=db_b)
    finally:
        rconn.close()

    now = time.time()
    a_rows = [
        {"path": p_kick, "scan_root": a_root, "format": "wav",
         "duration": 1.2, "bpm": 120.0, "key": "C",
         "title": "kick one", "comment": "booming sub",
         "mtime": now, "size": 100, "indexed_at": now, "last_seen_at": now,
         "chunks": "acid,smpl", "acid_beats": 4, "root_note": 60,
         "sample_rate": 44100, "channels": 1, "bits_per_sample": 16,
         "artist": None, "album": None, "genre": None},
        {"path": p_hat, "scan_root": a_root, "format": "wav",
         "duration": 0.5, "bpm": 128.0, "key": "Am",
         "title": "closed hat", "mtime": now, "size": 100,
         "indexed_at": now, "last_seen_at": now,
         "artist": None, "album": None, "genre": None, "comment": None,
         "chunks": None, "acid_beats": None, "root_note": None,
         "sample_rate": None, "channels": None, "bits_per_sample": None},
    ]
    b_rows = [
        {"path": p_synth, "scan_root": b_root, "format": "flac",
         "duration": 8.0, "bpm": 124.0, "key": "Em",
         "title": "dusty synth", "artist": "someone",
         "album": "dust pack", "genre": "electronic",
         "comment": "warm and lofi",
         "mtime": now, "size": 200, "indexed_at": now, "last_seen_at": now,
         "chunks": None, "acid_beats": None, "root_note": None,
         "sample_rate": 44100, "channels": 2, "bits_per_sample": 16},
    ]

    conn_a = idx.open_db(db_a)
    try:
        for r in a_rows:
            idx.upsert_sample(conn_a, r)
        idx.upsert_tags(conn_a, p_kick, ["drums", "kick", "punchy"])
        idx.upsert_tags(conn_a, p_hat, ["drums", "hat"])
        conn_a.commit()
    finally:
        conn_a.close()

    conn_b = idx.open_db(db_b)
    try:
        for r in b_rows:
            idx.upsert_sample(conn_b, r)
        idx.upsert_tags(conn_b, p_synth, ["synth", "pad"])
        idx.upsert_description(conn_b, p_synth, "moody analog pad")
        conn_b.commit()
    finally:
        conn_b.close()

    rconn = reg.open_registry(registry_path)
    try:
        reg.update_stats(rconn, a_root, sample_count=2)
        reg.update_stats(rconn, b_root, sample_count=1)
    finally:
        rconn.close()

    return {
        "registry_path": registry_path,
        "lib_a_root": a_root,
        "lib_b_root": b_root,
        "P_KICK": p_kick,
        "P_HAT": p_hat,
        "P_SYNTH": p_synth,
    }


class TestToolsRegistered:
    def test_expected_tools(self):
        names = {t["name"] for t in mcp_server.TOOLS}
        expected = {
            "search_samples", "get_sample", "locate_sample",
            "list_libraries", "list_tags", "list_keys", "list_formats",
            "index_stats", "find_compatible",
            "find_similar", "analyze_sample", "detect_bpm_key",
            "reindex", "reindex_features",
            "register_library", "forget_library",
            "tag_sample", "describe_sample",
        }
        assert expected.issubset(names)
        assert "list_roots" not in names

    def test_fast_tools_have_fast_prefix(self):
        fast = {"search_samples", "get_sample", "locate_sample",
                "list_libraries", "list_tags", "list_keys", "list_formats",
                "index_stats", "find_compatible"}
        for t in mcp_server.TOOLS:
            if t["name"] in fast:
                assert t["description"].startswith("Fast."), t["name"]

    def test_destructive_tools_marked(self):
        destructive = {"register_library", "forget_library",
                       "tag_sample", "describe_sample"}
        for t in mcp_server.TOOLS:
            if t["name"] in destructive:
                assert t["annotations"]["destructiveHint"] is True


class TestSearchSamplesFanOut:
    def test_no_filter_returns_all(self, two_lib_setup):
        r = mcp_server.dispatch("search_samples", {})
        paths = {s["path"] for s in r["samples"]}
        assert two_lib_setup["P_KICK"] in paths
        assert two_lib_setup["P_HAT"] in paths
        assert two_lib_setup["P_SYNTH"] in paths
        assert r["count"] == 3

    def test_bpm_range_across_libs(self, two_lib_setup):
        r = mcp_server.dispatch("search_samples",
                                {"bpm_min": 120, "bpm_max": 125})
        paths = {s["path"] for s in r["samples"]}
        assert paths == {two_lib_setup["P_KICK"], two_lib_setup["P_SYNTH"]}

    def test_text_fts_finds_in_one_lib(self, two_lib_setup):
        r = mcp_server.dispatch("search_samples", {"text": "dusty"})
        assert r["count"] == 1
        assert r["samples"][0]["path"] == two_lib_setup["P_SYNTH"]
        assert r["samples"][0]["library_label"] == "B"

    def test_root_label_scope(self, two_lib_setup):
        r = mcp_server.dispatch("search_samples", {"root": "A"})
        paths = {s["path"] for s in r["samples"]}
        assert two_lib_setup["P_SYNTH"] not in paths

    def test_root_path_scope(self, two_lib_setup):
        r = mcp_server.dispatch("search_samples",
                                {"root": two_lib_setup["lib_b_root"]})
        paths = {s["path"] for s in r["samples"]}
        assert paths == {two_lib_setup["P_SYNTH"]}


class TestGetSample:
    def test_returns_full_record(self, two_lib_setup):
        r = mcp_server.dispatch("get_sample",
                                {"path": two_lib_setup["P_SYNTH"]})
        assert r["path"] == two_lib_setup["P_SYNTH"]
        assert r["tags"] == ["pad", "synth"]
        assert r["description"] == "moody analog pad"
        assert r["library_label"] == "B"

    def test_unknown_raises(self, two_lib_setup):
        with pytest.raises(mcp_server.ToolError):
            mcp_server.dispatch("get_sample", {"path": "/nope.wav"})


class TestLocateSample:
    def test_finds_by_substring(self, two_lib_setup):
        r = mcp_server.dispatch("locate_sample", {"name": "synth"})
        assert r["count"] == 1
        assert r["samples"][0]["library_label"] == "B"

    def test_substring_matches_mid_filename(self, two_lib_setup):
        # Regression: "_120" sits mid-filename in kick_120.wav. The old
        # "%/<name>%" pattern silently failed for this case because nothing
        # in the path is `/120...`. Substring match must work anywhere.
        r = mcp_server.dispatch("locate_sample", {"name": "_120"})
        assert r["count"] == 1
        assert r["samples"][0]["path"] == two_lib_setup["P_KICK"]


class TestListLibraries:
    def test_returns_both(self, two_lib_setup):
        r = mcp_server.dispatch("list_libraries", {})
        labels = {lib["label"] for lib in r["libraries"]}
        assert labels == {"A", "B"}
        assert r["available"] == 2
        assert r["unavailable"] == 0


class TestListTagsKeysFormats:
    def test_list_tags_sums_across_libs(self, two_lib_setup):
        r = mcp_server.dispatch("list_tags", {})
        tag_map = {t["tag"]: t["count"] for t in r["tags"]}
        assert tag_map["drums"] == 2
        assert tag_map["synth"] == 1

    def test_list_keys(self, two_lib_setup):
        r = mcp_server.dispatch("list_keys", {})
        keys = {k["key"] for k in r["keys"]}
        assert keys == {"C", "Am", "Em"}

    def test_list_formats(self, two_lib_setup):
        r = mcp_server.dispatch("list_formats", {})
        formats = {f["format"] for f in r["formats"]}
        assert formats == {"wav", "flac"}


class TestIndexStats:
    def test_rolls_up(self, two_lib_setup):
        r = mcp_server.dispatch("index_stats", {})
        assert r["total_samples"] == 3
        assert r["available_libraries"] == 2
        assert r["unavailable_libraries"] == 0


class TestFindCompatible:
    def test_finds_compatible_across_libs(self, two_lib_setup):
        # P_HAT is Am (8A) at 128 bpm, one_shot. With kind=any and a wider
        # tolerance, P_SYNTH (Em loop at 124) should appear.
        r = mcp_server.dispatch("find_compatible", {
            "path": two_lib_setup["P_HAT"],
            "kind": "any",
            "bpm_tolerance_pct": 10,
        })
        paths = {s["path"] for s in r["samples"]}
        assert two_lib_setup["P_SYNTH"] in paths


class TestFindSimilar:
    def _seed_features(self, two_lib_setup):
        """Add stub feature vectors so find_similar has something to score."""
        from acidcat.core import paths as acidpaths

        # P_KICK: loop (acid_beats=4 in the seed). P_HAT: one_shot.
        # P_SYNTH: loop (8s, no acid_beats but duration >= 2.0).
        feats_a_db = acidpaths.central_db_path_for(
            two_lib_setup["lib_a_root"], "A"
        )
        feats_b_db = acidpaths.central_db_path_for(
            two_lib_setup["lib_b_root"], "B"
        )

        feats_a = idx.open_db(feats_a_db)
        try:
            idx.upsert_features(feats_a, two_lib_setup["P_KICK"], {
                "spectral_centroid_mean": 200.0, "rms_mean": 0.5,
                "duration_sec": 1.2,
            })
            idx.upsert_features(feats_a, two_lib_setup["P_HAT"], {
                "spectral_centroid_mean": 6000.0, "rms_mean": 0.1,
                "duration_sec": 0.5,
            })
            feats_a.commit()
        finally:
            feats_a.close()
        feats_b = idx.open_db(feats_b_db)
        try:
            idx.upsert_features(feats_b, two_lib_setup["P_SYNTH"], {
                "spectral_centroid_mean": 250.0, "rms_mean": 0.4,
                "duration_sec": 8.0,
            })
            feats_b.commit()
        finally:
            feats_b.close()

    def test_kind_filter_default_excludes_other_kind(self, two_lib_setup):
        """A 0.5s one-shot target should not surface 8s loops by default."""
        self._seed_features(two_lib_setup)
        r = mcp_server.dispatch("find_similar", {
            "path": two_lib_setup["P_HAT"], "n": 5,
        })
        assert r["target_kind"] == "one_shot"
        assert r["filter_kind"] == "one_shot"
        paths = {res["path"] for res in r["results"]}
        assert two_lib_setup["P_KICK"] not in paths
        assert two_lib_setup["P_SYNTH"] not in paths

    def test_kind_filter_false_disables_filtering(self, two_lib_setup):
        self._seed_features(two_lib_setup)
        r = mcp_server.dispatch("find_similar", {
            "path": two_lib_setup["P_HAT"], "n": 5,
            "kind_filter": False,
        })
        assert r["filter_kind"] == "any"
        paths = {res["path"] for res in r["results"]}
        # with filtering off, at least one loop should reappear
        assert (two_lib_setup["P_KICK"] in paths
                or two_lib_setup["P_SYNTH"] in paths)

    def test_explicit_kind_overrides_default(self, two_lib_setup):
        self._seed_features(two_lib_setup)
        r = mcp_server.dispatch("find_similar", {
            "path": two_lib_setup["P_HAT"], "n": 5,
            "kind": "loop",
        })
        assert r["filter_kind"] == "loop"
        # the one_shot target itself must not appear in its own loop search
        for res in r["results"]:
            assert res["path"] != two_lib_setup["P_HAT"]

    def test_results_carry_percentile_and_relative_scores(self, two_lib_setup):
        self._seed_features(two_lib_setup)
        r = mcp_server.dispatch("find_similar", {
            "path": two_lib_setup["P_KICK"], "n": 5,
            "kind_filter": False,
        })
        assert r["results"], "expected at least one result"
        for res in r["results"]:
            assert "percentile_rank" in res
            assert 0.0 <= res["percentile_rank"] <= 100.0
            assert "similarity_above_mean" in res
            assert isinstance(res["similarity_above_mean"], float)


class TestRegisterAndForget:
    def test_register_creates_db_and_registry_row(self, two_lib_setup, tmp_path):
        new_root = tmp_path / "newlib"
        new_root.mkdir()
        r = mcp_server.dispatch("register_library", {
            "root": str(new_root), "label": "newlib",
        })
        assert r["label"] == "newlib"
        assert os.path.isfile(r["db_path"])
        listed = mcp_server.dispatch("list_libraries", {})
        labels = {lib["label"] for lib in listed["libraries"]}
        assert labels == {"A", "B", "newlib"}

    def test_register_overlap_rejected(self, two_lib_setup, tmp_path):
        sub = tmp_path / "libA" / "sub"
        sub.mkdir()
        with pytest.raises(mcp_server.ToolError) as excinfo:
            mcp_server.dispatch("register_library", {
                "root": str(sub), "label": "sub",
            })
        assert "'A'" in str(excinfo.value)

    def test_forget_removes_from_registry(self, two_lib_setup):
        r = mcp_server.dispatch("forget_library", {"label": "A"})
        assert r["count"] == 1
        listed = mcp_server.dispatch("list_libraries", {})
        labels = {lib["label"] for lib in listed["libraries"]}
        assert "A" not in labels


class TestTagSample:
    def test_add_remove_round_trip(self, two_lib_setup):
        r = mcp_server.dispatch("tag_sample", {
            "path": two_lib_setup["P_HAT"],
            "add_tags": ["bright"],
        })
        assert "bright" in r["tags"]
        assert r["library_label"] == "A"
        r2 = mcp_server.dispatch("tag_sample", {
            "path": two_lib_setup["P_HAT"],
            "remove_tags": ["bright"],
        })
        assert "bright" not in r2["tags"]


class TestDescribeSample:
    def test_set_description(self, two_lib_setup):
        r = mcp_server.dispatch("describe_sample", {
            "path": two_lib_setup["P_HAT"],
            "description": "crispy hat",
        })
        assert r["description"] == "crispy hat"
        got = mcp_server.dispatch("get_sample", {
            "path": two_lib_setup["P_HAT"],
        })
        assert got["description"] == "crispy hat"


class TestAnalysisDegrades:
    def test_analyze_sample_no_librosa(self, two_lib_setup, monkeypatch):
        monkeypatch.setattr(mcp_server, "_librosa_available", lambda: False)
        r = mcp_server.dispatch("analyze_sample",
                                {"path": two_lib_setup["P_HAT"]})
        assert "error" in r

    def test_detect_bpm_key_no_librosa(self, two_lib_setup, monkeypatch):
        monkeypatch.setattr(mcp_server, "_librosa_available", lambda: False)
        r = mcp_server.dispatch("detect_bpm_key",
                                {"path": two_lib_setup["P_HAT"]})
        assert "error" in r


class TestUnknownTool:
    def test_dispatch_raises(self):
        with pytest.raises(mcp_server.ToolError):
            mcp_server.dispatch("nope", {})


class TestInferKindHelper:
    def test_one_shot(self):
        assert mcp_server.infer_kind(0.5, 0) == "one_shot"
        assert mcp_server.infer_kind(0.5, None) == "one_shot"

    def test_loop(self):
        assert mcp_server.infer_kind(8.0, 0) == "loop"
        assert mcp_server.infer_kind(1.2, 4) == "loop"

    def test_ambiguous(self):
        assert mcp_server.infer_kind(1.5, 0) == "any"
