"""Tests for `acidcat index` (per-library + registry layout)."""

import json
import os
import struct
import time

import pytest

from acidcat.commands import index as index_cmd
from acidcat.core import index as idx
from acidcat.core import paths as acidpaths
from acidcat.core import registry as reg


def _make_riff_wav(sample_rate=44100, channels=1, bits=16, num_samples=4,
                   smpl_root_key=None):
    block_align = channels * bits // 8
    byte_rate = sample_rate * block_align
    audio_data = b"\x00" * (num_samples * block_align)
    fmt = struct.pack(
        "<HHIIHH", 1, channels, sample_rate, byte_rate, block_align, bits,
    )
    fmt_chunk = b"fmt " + struct.pack("<I", 16) + fmt
    data_chunk = b"data" + struct.pack("<I", len(audio_data)) + audio_data
    smpl_chunk = b""
    if smpl_root_key is not None:
        smpl_body = struct.pack(
            "<IIIIIIiiI",
            0, 0, 0, smpl_root_key, 0, 0, 0, 0, 0,
        )
        smpl_chunk = b"smpl" + struct.pack("<I", len(smpl_body)) + smpl_body
    riff_body = b"WAVE" + fmt_chunk + data_chunk + smpl_chunk
    return b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body


class _Args:
    def __init__(self, **kw):
        defaults = {
            "target": None, "label": None, "in_tree": False,
            "rebuild": False, "features": False, "deep": False,
            "import_tags": None, "registry": None,
            "list_libs": False, "orphans": False,
            "stats_target": None, "forget": None, "remove": None,
            "quiet": True, "verbose": False,
        }
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


@pytest.fixture
def registry_path(tmp_path, monkeypatch):
    """Sandbox the registry path for every test in this module."""
    p = str(tmp_path / "registry.db")
    monkeypatch.setenv("ACIDCAT_REGISTRY", p)
    return p


@pytest.fixture
def central_root(tmp_path, monkeypatch):
    """Pin the central libraries dir under tmp_path so tests don't write
    to the user's real ~/.acidcat/."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def wav_bytes():
    return _make_riff_wav()


def _library(tmp_path, wav_bytes, name="lib"):
    lib = tmp_path / name
    lib.mkdir()
    (lib / "kick.wav").write_bytes(wav_bytes)
    (lib / "snare.wav").write_bytes(wav_bytes)
    sub = lib / "sub"
    sub.mkdir()
    (sub / "hat.wav").write_bytes(wav_bytes)
    return lib


class TestIndexCreatesLibrary:
    def test_central_db_created_and_registered(self, tmp_path, central_root,
                                                registry_path, wav_bytes):
        lib = _library(tmp_path, wav_bytes)
        rc = index_cmd.run(_Args(target=str(lib), label="testlib",
                                 registry=registry_path))
        assert rc == 0

        # registry has the library
        rconn = reg.open_registry(registry_path)
        try:
            rows = reg.list_libraries(rconn)
            assert len(rows) == 1
            assert rows[0]["label"] == "testlib"
            assert rows[0]["sample_count"] == 3
            assert rows[0]["in_tree"] == 0
        finally:
            rconn.close()

        # the central DB file actually exists
        assert os.path.isfile(rows[0]["db_path"])

    def test_label_defaults_to_basename(self, tmp_path, central_root,
                                         registry_path, wav_bytes):
        lib = _library(tmp_path, wav_bytes, name="MyPack")
        index_cmd.run(_Args(target=str(lib), registry=registry_path))
        rconn = reg.open_registry(registry_path)
        try:
            rows = reg.list_libraries(rconn)
            assert rows[0]["label"] == "MyPack"
        finally:
            rconn.close()

    def test_in_tree_mode(self, tmp_path, central_root, registry_path, wav_bytes):
        lib = _library(tmp_path, wav_bytes)
        rc = index_cmd.run(_Args(target=str(lib), label="intree",
                                 in_tree=True, registry=registry_path))
        assert rc == 0
        expected_db = acidpaths.in_tree_db_path_for(str(lib))
        assert os.path.isfile(expected_db)


class TestNoOverlap:
    def test_child_of_existing_rejected(self, tmp_path, central_root,
                                         registry_path, wav_bytes):
        parent = _library(tmp_path, wav_bytes, name="parent")
        index_cmd.run(_Args(target=str(parent), label="parent",
                            registry=registry_path))
        child = parent / "sub"
        rc = index_cmd.run(_Args(target=str(child), label="child",
                                 registry=registry_path))
        assert rc == 1


class TestIncrementalReindex:
    def test_skips_unchanged(self, tmp_path, central_root, registry_path,
                              wav_bytes):
        lib = _library(tmp_path, wav_bytes)
        index_cmd.run(_Args(target=str(lib), label="x",
                            registry=registry_path))
        # second pass should not change indexed_at on existing rows
        rconn = reg.open_registry(registry_path)
        try:
            db_path = reg.get_library(rconn, "x")["db_path"]
        finally:
            rconn.close()

        conn = idx.open_db(db_path)
        try:
            first_indexed = {
                r["path"]: r["indexed_at"]
                for r in conn.execute("SELECT path, indexed_at FROM samples")
            }
        finally:
            conn.close()

        time.sleep(0.05)
        index_cmd.run(_Args(target=str(lib), label="x",
                            registry=registry_path))

        conn = idx.open_db(db_path)
        try:
            second = {
                r["path"]: r["indexed_at"]
                for r in conn.execute("SELECT path, indexed_at FROM samples")
            }
        finally:
            conn.close()
        assert first_indexed == second


class TestRebuild:
    def test_rebuild_clears_db(self, tmp_path, central_root, registry_path,
                                wav_bytes):
        lib = _library(tmp_path, wav_bytes)
        index_cmd.run(_Args(target=str(lib), label="x",
                            registry=registry_path))
        # remove a file from disk; --rebuild should produce a fresh DB
        # without the row
        (lib / "kick.wav").unlink()

        rc = index_cmd.run(_Args(target=str(lib), label="x",
                                 rebuild=True, registry=registry_path))
        assert rc == 0
        rconn = reg.open_registry(registry_path)
        try:
            db_path = reg.get_library(rconn, "x")["db_path"]
        finally:
            rconn.close()
        conn = idx.open_db(db_path)
        try:
            count = conn.execute("SELECT COUNT(*) AS c FROM samples").fetchone()["c"]
        finally:
            conn.close()
        assert count == 2


class TestPruneMissing:
    def test_missing_files_pruned(self, tmp_path, central_root, registry_path,
                                   wav_bytes):
        lib = _library(tmp_path, wav_bytes)
        index_cmd.run(_Args(target=str(lib), label="x",
                            registry=registry_path))
        (lib / "kick.wav").unlink()
        index_cmd.run(_Args(target=str(lib), label="x",
                            registry=registry_path))

        rconn = reg.open_registry(registry_path)
        try:
            db_path = reg.get_library(rconn, "x")["db_path"]
        finally:
            rconn.close()
        conn = idx.open_db(db_path)
        try:
            paths_in_db = [
                r["path"] for r in conn.execute("SELECT path FROM samples")
            ]
        finally:
            conn.close()
        assert not any(p.endswith("kick.wav") for p in paths_in_db)
        assert len(paths_in_db) == 2


class TestForgetVsRemove:
    def test_forget_keeps_db_file(self, tmp_path, central_root, registry_path,
                                   wav_bytes):
        lib = _library(tmp_path, wav_bytes)
        index_cmd.run(_Args(target=str(lib), label="x",
                            registry=registry_path))
        rconn = reg.open_registry(registry_path)
        try:
            db_path = reg.get_library(rconn, "x")["db_path"]
        finally:
            rconn.close()

        rc = index_cmd.run(_Args(forget="x", registry=registry_path))
        assert rc == 0
        # registry no longer knows it
        rconn = reg.open_registry(registry_path)
        try:
            assert reg.get_library(rconn, "x") is None
        finally:
            rconn.close()
        # but DB file still exists
        assert os.path.isfile(db_path)

    def test_remove_deletes_db_file(self, tmp_path, central_root, registry_path,
                                     wav_bytes):
        lib = _library(tmp_path, wav_bytes)
        index_cmd.run(_Args(target=str(lib), label="x",
                            registry=registry_path))
        rconn = reg.open_registry(registry_path)
        try:
            db_path = reg.get_library(rconn, "x")["db_path"]
        finally:
            rconn.close()

        rc = index_cmd.run(_Args(remove="x", registry=registry_path))
        assert rc == 0
        rconn = reg.open_registry(registry_path)
        try:
            assert reg.get_library(rconn, "x") is None
        finally:
            rconn.close()
        assert not os.path.isfile(db_path)


class TestOrphans:
    def test_orphans_lists_missing_db(self, tmp_path, central_root,
                                       registry_path, wav_bytes, capsys):
        lib = _library(tmp_path, wav_bytes)
        index_cmd.run(_Args(target=str(lib), label="x",
                            registry=registry_path))
        rconn = reg.open_registry(registry_path)
        try:
            db_path = reg.get_library(rconn, "x")["db_path"]
        finally:
            rconn.close()
        # delete the DB file out from under the registry
        for ext in ("", "-shm", "-wal"):
            p = db_path + ext
            if os.path.isfile(p):
                os.remove(p)

        rc = index_cmd.run(_Args(orphans=True, registry=registry_path))
        assert rc == 0
        captured = capsys.readouterr()
        assert "x" in captured.out


class TestImportTags:
    def test_import_tags(self, tmp_path, central_root, registry_path):
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "Drum_Loop.wav").write_bytes(_make_riff_wav())
        tags_file = tmp_path / "legacy_tags.json"
        tags_file.write_text(json.dumps({
            "data/samples\\Drum_Loop.wav": {
                "description": "Energetic drum loop",
                "tags": ["drums", "loop"],
            }
        }))
        rc = index_cmd.run(_Args(target=str(lib), label="x",
                                 import_tags=str(tags_file),
                                 registry=registry_path))
        assert rc == 0

        rconn = reg.open_registry(registry_path)
        try:
            db_path = reg.get_library(rconn, "x")["db_path"]
        finally:
            rconn.close()
        conn = idx.open_db(db_path)
        try:
            tags = sorted(
                r["tag"] for r in conn.execute("SELECT tag FROM tags")
            )
            desc = conn.execute("SELECT description FROM descriptions").fetchone()
        finally:
            conn.close()
        assert tags == ["drums", "loop"]
        assert desc["description"] == "Energetic drum loop"


class TestSmplRootKey:
    def test_zero_treated_as_unset(self, tmp_path, central_root, registry_path):
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "zero.wav").write_bytes(_make_riff_wav(smpl_root_key=0))
        index_cmd.run(_Args(target=str(lib), label="x", registry=registry_path))

        rconn = reg.open_registry(registry_path)
        try:
            db_path = reg.get_library(rconn, "x")["db_path"]
        finally:
            rconn.close()
        conn = idx.open_db(db_path)
        try:
            row = conn.execute(
                "SELECT key, root_note FROM samples"
            ).fetchone()
        finally:
            conn.close()
        # key may fall back to filename or be None; the regression is that
        # it must NOT be "C-1"
        assert row["key"] != "C-1"
        assert row["root_note"] is None

    def test_nonzero_yields_pitch_class(self, tmp_path, central_root,
                                         registry_path):
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "c4.wav").write_bytes(_make_riff_wav(smpl_root_key=60))
        index_cmd.run(_Args(target=str(lib), label="x", registry=registry_path))
        rconn = reg.open_registry(registry_path)
        try:
            db_path = reg.get_library(rconn, "x")["db_path"]
        finally:
            rconn.close()
        conn = idx.open_db(db_path)
        try:
            row = conn.execute(
                "SELECT key, root_note FROM samples"
            ).fetchone()
        finally:
            conn.close()
        assert row["key"] == "C"
        assert row["root_note"] == 60


class TestJunkFilter:
    def test_appledouble_skipped(self, tmp_path, central_root, registry_path,
                                  wav_bytes):
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "real.wav").write_bytes(wav_bytes)
        (lib / "._real.wav").write_bytes(b"\x00" * 32)
        (lib / ".DS_Store").write_bytes(b"\x00" * 32)
        index_cmd.run(_Args(target=str(lib), label="x", registry=registry_path))
        rconn = reg.open_registry(registry_path)
        try:
            row = reg.get_library(rconn, "x")
        finally:
            rconn.close()
        assert row["sample_count"] == 1
