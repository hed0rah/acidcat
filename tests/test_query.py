"""Tests for `acidcat query` over the per-library + registry layout."""

import io
import json
import time

import pytest

from acidcat.commands import query as query_cmd
from acidcat.core import index as idx
from acidcat.core import paths as acidpaths
from acidcat.core import registry as reg


class _Args:
    def __init__(self, **kw):
        defaults = {
            "registry": None, "bpm": None, "key": None, "duration": None,
            "tag": [], "file_format": None, "text": None, "root": None,
            "limit": 50, "output_format": "json", "output": None,
            "paths_only": False, "verbose": False,
        }
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


@pytest.fixture
def two_library_setup(tmp_path, monkeypatch):
    """Build two real libraries on disk with seeded sample rows.

    Returns dict with: registry_path, lib_a_root, lib_b_root, paths.
    """
    monkeypatch.setenv("ACIDCAT_REGISTRY",
                       str(tmp_path / "registry.db"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))

    lib_a_root = tmp_path / "libA"
    lib_a_root.mkdir()
    lib_b_root = tmp_path / "libB"
    lib_b_root.mkdir()

    a_root_str = acidpaths.normalize(str(lib_a_root))
    b_root_str = acidpaths.normalize(str(lib_b_root))
    p_kick = a_root_str + "/kick_120.wav"
    p_hat = a_root_str + "/hat_128.wav"
    p_synth = b_root_str + "/synth_124.flac"

    # register both libraries
    rconn = reg.open_registry()
    try:
        db_a = acidpaths.central_db_path_for(a_root_str, "A")
        db_b = acidpaths.central_db_path_for(b_root_str, "B")
        reg.register_library(rconn, a_root_str, label="A", db_path=db_a)
        reg.register_library(rconn, b_root_str, label="B", db_path=db_b)
    finally:
        rconn.close()

    # seed each library's DB
    now = time.time()
    a_rows = [
        {"path": p_kick, "scan_root": a_root_str, "format": "wav",
         "duration": 1.2, "bpm": 120.0, "key": "C",
         "title": "kick one", "comment": "booming sub",
         "mtime": now, "size": 100, "indexed_at": now, "last_seen_at": now,
         "chunks": "acid,smpl", "acid_beats": 4, "root_note": 60,
         "sample_rate": 44100, "channels": 1, "bits_per_sample": 16,
         "artist": None, "album": None, "genre": None},
        {"path": p_hat, "scan_root": a_root_str, "format": "wav",
         "duration": 0.5, "bpm": 128.0, "key": "Am",
         "title": "closed hat", "mtime": now, "size": 100,
         "indexed_at": now, "last_seen_at": now,
         "artist": None, "album": None, "genre": None, "comment": None,
         "chunks": None, "acid_beats": None, "root_note": None,
         "sample_rate": None, "channels": None, "bits_per_sample": None},
    ]
    b_rows = [
        {"path": p_synth, "scan_root": b_root_str, "format": "flac",
         "duration": 8.0, "bpm": 124.0, "key": "Fm",
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
        conn_b.commit()
    finally:
        conn_b.close()

    return {
        "lib_a_root": a_root_str,
        "lib_b_root": b_root_str,
        "P_KICK": p_kick,
        "P_HAT": p_hat,
        "P_SYNTH": p_synth,
    }


def _run(args):
    """Run the query command capturing stdout to JSON."""
    args.output_format = "json"
    args.output = "/tmp/_acidcat_query_test.json"  # any tmp path
    import os
    if os.path.isfile(args.output):
        os.remove(args.output)
    rc = query_cmd.run(args)
    assert rc == 0
    with open(args.output, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]


class TestFanOut:
    def test_no_filter_returns_all(self, two_library_setup, tmp_path):
        out_file = str(tmp_path / "out.json")
        rows = _run(_Args(output=out_file))
        paths = {r["path"] for r in rows}
        assert two_library_setup["P_KICK"] in paths
        assert two_library_setup["P_HAT"] in paths
        assert two_library_setup["P_SYNTH"] in paths

    def test_bpm_range_across_libs(self, two_library_setup, tmp_path):
        out_file = str(tmp_path / "out.json")
        rows = _run(_Args(bpm="120:125", output=out_file))
        paths = {r["path"] for r in rows}
        assert paths == {two_library_setup["P_KICK"],
                          two_library_setup["P_SYNTH"]}

    def test_text_fts_finds_in_one_lib(self, two_library_setup, tmp_path):
        out_file = str(tmp_path / "out.json")
        rows = _run(_Args(text="dusty", output=out_file))
        assert len(rows) == 1
        assert rows[0]["path"] == two_library_setup["P_SYNTH"]


class TestRootScoping:
    def test_root_by_label(self, two_library_setup, tmp_path):
        out_file = str(tmp_path / "out.json")
        rows = _run(_Args(root="A", output=out_file))
        paths = {r["path"] for r in rows}
        assert two_library_setup["P_SYNTH"] not in paths
        assert two_library_setup["P_KICK"] in paths

    def test_root_by_path(self, two_library_setup, tmp_path):
        out_file = str(tmp_path / "out.json")
        rows = _run(_Args(root=two_library_setup["lib_b_root"],
                          output=out_file))
        paths = {r["path"] for r in rows}
        assert paths == {two_library_setup["P_SYNTH"]}

    def test_root_csv_multi(self, two_library_setup, tmp_path):
        out_file = str(tmp_path / "out.json")
        rows = _run(_Args(root="A,B", output=out_file))
        assert len(rows) == 3

    def test_root_unknown_label_returns_error(self, two_library_setup):
        rc = query_cmd.run(_Args(root="ZZZZ"))
        assert rc == 1


class TestTagFilter:
    def test_single_tag_in_one_lib(self, two_library_setup, tmp_path):
        out_file = str(tmp_path / "out.json")
        rows = _run(_Args(tag=["synth"], output=out_file))
        paths = {r["path"] for r in rows}
        assert paths == {two_library_setup["P_SYNTH"]}

    def test_and_semantics(self, two_library_setup, tmp_path):
        out_file = str(tmp_path / "out.json")
        rows = _run(_Args(tag=["drums", "kick"], output=out_file))
        assert len(rows) == 1
        assert rows[0]["path"] == two_library_setup["P_KICK"]


class TestNoLibraries:
    def test_no_libs_registered_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACIDCAT_REGISTRY", str(tmp_path / "empty.db"))
        rc = query_cmd.run(_Args())
        assert rc == 1


class TestLimit:
    def test_global_limit_applied(self, two_library_setup, tmp_path):
        out_file = str(tmp_path / "out.json")
        rows = _run(_Args(limit=2, output=out_file))
        assert len(rows) == 2
