"""The index-backed `acidcat similar` CLI verb and the shared
core.search.find_similar it calls (also used by the MCP find_similar tool)."""

import os
import time

import pytest

from acidcat.core import index as idx
from acidcat.core import paths as acidpaths
from acidcat.core import registry as reg
from acidcat.core import search
from acidcat.commands import similar


@pytest.fixture
def indexed_library(tmp_path, monkeypatch):
    """A registered library with three feature-vector'd samples: two bright
    one-shots (a hat and a click, close in timbre) and one dark loop."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    registry_path = str(tmp_path / "registry.db")
    monkeypatch.setenv("ACIDCAT_REGISTRY", registry_path)

    root_dir = tmp_path / "lib"
    root_dir.mkdir()
    # real files so os.path.exists(target) passes in the CLI verb
    for name in ("hat.wav", "click.wav", "pad.wav"):
        (root_dir / name).write_bytes(b"RIFF")
    root = acidpaths.normalize(str(root_dir))
    p_hat = root + "/hat.wav"
    p_click = root + "/click.wav"
    p_pad = root + "/pad.wav"

    db = acidpaths.central_db_path_for(root, "lib")
    rconn = reg.open_registry(registry_path)
    try:
        reg.register_library(rconn, root, label="lib", db_path=db)
    finally:
        rconn.close()

    now = time.time()
    conn = idx.open_db(db)
    try:
        for p, dur, beats in ((p_hat, 0.4, None), (p_click, 0.3, None),
                              (p_pad, 6.0, None)):
            conn.execute(
                "INSERT INTO samples (path, scan_root, format, duration, "
                "acid_beats, mtime, size, indexed_at, last_seen_at) "
                "VALUES (?, ?, 'wav', ?, ?, ?, 100, ?, ?)",
                (p, root, dur, beats, now, now, now))
        # bright one-shots close together; the pad is dark and long
        idx.upsert_features(conn, p_hat, {
            "spectral_centroid_mean": 6000.0, "rms_mean": 0.10,
            "duration_sec": 0.4})
        idx.upsert_features(conn, p_click, {
            "spectral_centroid_mean": 6200.0, "rms_mean": 0.11,
            "duration_sec": 0.3})
        idx.upsert_features(conn, p_pad, {
            "spectral_centroid_mean": 300.0, "rms_mean": 0.5,
            "duration_sec": 6.0})
        conn.commit()
    finally:
        conn.close()

    return {"registry": registry_path, "root": root,
            "hat": p_hat, "click": p_click, "pad": p_pad}


def _libs(registry_path):
    rconn = reg.open_registry(registry_path)
    try:
        return reg.list_libraries(rconn, only_existing=True)
    finally:
        rconn.close()


def test_core_find_similar_ranks_and_excludes_target(indexed_library):
    libs = _libs(indexed_library["registry"])
    feats, meta = search.resolve_target_features(indexed_library["hat"], libs)
    assert feats is not None
    result = search.find_similar(libs, feats, meta, n=5,
                                 exclude_path=indexed_library["hat"])
    paths = [r["path"] for r in result["results"]]
    assert indexed_library["hat"] not in paths            # target excluded
    # the click is the nearest one-shot; default kind filter drops the loop
    assert result["target_kind"] == "one_shot"
    assert indexed_library["click"] in paths
    assert indexed_library["pad"] not in paths            # loop filtered out


def test_core_find_similar_kind_filter_off_includes_loop(indexed_library):
    libs = _libs(indexed_library["registry"])
    feats, meta = search.resolve_target_features(indexed_library["hat"], libs)
    result = search.find_similar(libs, feats, meta, n=5, kind_filter=False,
                                 exclude_path=indexed_library["hat"])
    assert result["filter_kind"] == "any"
    paths = [r["path"] for r in result["results"]]
    assert indexed_library["pad"] in paths


def test_core_find_similar_results_carry_population_stats(indexed_library):
    libs = _libs(indexed_library["registry"])
    feats, meta = search.resolve_target_features(indexed_library["hat"], libs)
    result = search.find_similar(libs, feats, meta, n=5, kind="any",
                                 exclude_path=indexed_library["hat"])
    for r in result["results"]:
        assert "similarity" in r
        assert "percentile_rank" in r
        assert "similarity_above_mean" in r


def test_cli_similar_table(indexed_library, capsys):
    rc = similar.run(_args(indexed_library, indexed_library["hat"]))
    assert rc == 0
    out = capsys.readouterr().out
    assert "click.wav" in out
    assert "pad.wav" not in out          # loop filtered from a one-shot target


def test_cli_similar_paths_only(indexed_library, capsys):
    rc = similar.run(_args(indexed_library, indexed_library["hat"],
                           paths_only=True, kind="any"))
    assert rc == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert indexed_library["hat"] not in lines
    assert any("click.wav" in ln for ln in lines)


def test_cli_similar_missing_file(indexed_library, capsys):
    rc = similar.run(_args(indexed_library, "/no/such/file.wav"))
    assert rc == 1
    assert "file not found" in capsys.readouterr().err


def test_cli_matches_mcp(indexed_library):
    """The CLI verb and the MCP tool resolve to the same core ranking."""
    from acidcat import mcp_server
    libs = _libs(indexed_library["registry"])
    feats, meta = search.resolve_target_features(indexed_library["click"], libs)
    core = search.find_similar(libs, feats, meta, n=5, kind="any",
                               exclude_path=indexed_library["click"])
    core_paths = [r["path"] for r in core["results"]]
    # the hat is the click's nearest neighbour by timbre
    assert core_paths and core_paths[0] == indexed_library["hat"]


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _args(lib, target, *, paths_only=False, kind=None, kind_filter=True):
    return _Args(target=target, num=5, kind=kind, kind_filter=kind_filter,
                 registry=lib["registry"], output_format="table",
                 output=None, paths_only=paths_only)
