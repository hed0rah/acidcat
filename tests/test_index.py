"""Tests for the SQLite index and `acidcat index` command."""

import json
import os
import struct
import time

import pytest

from acidcat.commands import index as index_cmd
from acidcat.core import index as idx


def _make_riff_wav(sample_rate=44100, channels=1, bits=16, num_samples=4,
                   smpl_root_key=None):
    """Build a minimal valid PCM WAV. If smpl_root_key is given, add a SMPL
    chunk with that MIDI note as the unity note (no loops).
    """
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
        # 36-byte smpl body: manufacturer, product, sample_period,
        # midi_unity_note, midi_pitch_fraction, smpte_format, smpte_offset,
        # sample_loops, sampler_data
        smpl_body = struct.pack(
            "<IIIIIIiiI",
            0, 0, 0, smpl_root_key, 0, 0, 0, 0, 0,
        )
        smpl_chunk = b"smpl" + struct.pack("<I", len(smpl_body)) + smpl_body

    riff_body = b"WAVE" + fmt_chunk + data_chunk + smpl_chunk
    return b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body


class _Args:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        for name in ("target", "db", "features", "deep", "import_tags",
                     "list_roots", "remove_root", "stats", "quiet"):
            if not hasattr(self, name):
                setattr(self, name, None)
        if self.quiet is None:
            self.quiet = True
        if self.features is None:
            self.features = False
        if self.deep is None:
            self.deep = False
        if self.list_roots is None:
            self.list_roots = False
        if self.stats is None:
            self.stats = False


def _library(tmp_path, minimal_wav_bytes):
    """Build a small library of WAVs in tmp_path/lib."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "kick.wav").write_bytes(minimal_wav_bytes)
    (lib / "snare.wav").write_bytes(minimal_wav_bytes)
    sub = lib / "sub"
    sub.mkdir()
    (sub / "hat.wav").write_bytes(minimal_wav_bytes)
    return lib


@pytest.fixture
def wav_bytes():
    return _make_riff_wav()


def test_open_db_creates_schema(tmp_path):
    db = tmp_path / "test.db"
    conn = idx.open_db(str(db))
    try:
        row = conn.execute(
            "SELECT v FROM meta WHERE k = 'schema_version'"
        ).fetchone()
        assert row["v"] == str(idx.SCHEMA_VERSION)
        tables = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')"
            ).fetchall()
        }
        for required in ("samples", "scan_roots", "tags", "descriptions",
                         "features", "samples_fts"):
            assert required in tables
    finally:
        conn.close()


def test_index_populates_rows(tmp_path, wav_bytes):
    lib = _library(tmp_path, wav_bytes)
    db = tmp_path / "idx.db"
    args = _Args(target=str(lib), db=str(db))
    rc = index_cmd.run(args)
    assert rc == 0

    conn = idx.open_db(str(db))
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM samples").fetchone()["c"]
        assert total == 3
        fmt_row = conn.execute(
            "SELECT DISTINCT format FROM samples"
        ).fetchone()
        assert fmt_row["format"] == "wav"
        roots = idx.list_roots(conn)
        assert len(roots) == 1
        assert roots[0]["file_count"] == 3
    finally:
        conn.close()


def test_reindex_skips_unchanged(tmp_path, wav_bytes):
    lib = _library(tmp_path, wav_bytes)
    db = tmp_path / "idx.db"
    args = _Args(target=str(lib), db=str(db))
    index_cmd.run(args)

    conn = idx.open_db(str(db))
    try:
        rows = conn.execute(
            "SELECT path, indexed_at FROM samples"
        ).fetchall()
        first_indexed = {r["path"]: r["indexed_at"] for r in rows}
    finally:
        conn.close()

    time.sleep(0.05)
    index_cmd.run(args)

    conn = idx.open_db(str(db))
    try:
        rows = conn.execute(
            "SELECT path, indexed_at, last_seen_at FROM samples"
        ).fetchall()
        for r in rows:
            assert r["indexed_at"] == first_indexed[r["path"]]
            assert r["last_seen_at"] >= r["indexed_at"]
    finally:
        conn.close()


def test_prune_missing_files(tmp_path, wav_bytes):
    lib = _library(tmp_path, wav_bytes)
    db = tmp_path / "idx.db"
    args = _Args(target=str(lib), db=str(db))
    index_cmd.run(args)

    os.remove(lib / "kick.wav")

    index_cmd.run(args)

    conn = idx.open_db(str(db))
    try:
        paths = [
            r["path"] for r in conn.execute(
                "SELECT path FROM samples"
            ).fetchall()
        ]
        assert not any(p.endswith("kick.wav") for p in paths)
        assert len(paths) == 2
    finally:
        conn.close()


def test_changed_file_updates_row(tmp_path, wav_bytes):
    lib = tmp_path / "lib"
    lib.mkdir()
    target = lib / "x.wav"
    target.write_bytes(wav_bytes)
    db = tmp_path / "idx.db"
    args = _Args(target=str(lib), db=str(db))
    index_cmd.run(args)

    conn = idx.open_db(str(db))
    orig_size = conn.execute("SELECT size FROM samples").fetchone()["size"]
    conn.close()

    # mutate the file: rewrite with longer audio
    target.write_bytes(_make_riff_wav(num_samples=1024))
    os.utime(target, (time.time() + 1, time.time() + 1))

    index_cmd.run(args)

    conn = idx.open_db(str(db))
    new_size = conn.execute("SELECT size FROM samples").fetchone()["size"]
    conn.close()
    assert new_size != orig_size


def test_multiple_roots_share_db(tmp_path, wav_bytes):
    db = tmp_path / "idx.db"

    lib_a = tmp_path / "a"
    lib_a.mkdir()
    (lib_a / "one.wav").write_bytes(wav_bytes)

    lib_b = tmp_path / "b"
    lib_b.mkdir()
    (lib_b / "two.wav").write_bytes(wav_bytes)

    index_cmd.run(_Args(target=str(lib_a), db=str(db)))
    index_cmd.run(_Args(target=str(lib_b), db=str(db)))

    conn = idx.open_db(str(db))
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM samples").fetchone()["c"]
        assert total == 2
        roots = idx.list_roots(conn)
        assert len(roots) == 2
    finally:
        conn.close()


def test_remove_root(tmp_path, wav_bytes):
    lib = _library(tmp_path, wav_bytes)
    db = tmp_path / "idx.db"
    index_cmd.run(_Args(target=str(lib), db=str(db)))

    scan_root = idx.normalize_path(str(lib))
    args = _Args(remove_root=scan_root, db=str(db))
    rc = index_cmd.run(args)
    assert rc == 0

    conn = idx.open_db(str(db))
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM samples").fetchone()["c"]
        assert total == 0
        assert idx.list_roots(conn) == []
    finally:
        conn.close()


def test_junk_files_skipped(tmp_path, wav_bytes):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "real.wav").write_bytes(wav_bytes)
    # AppleDouble sidecars, plus other OS junk
    (lib / "._real.wav").write_bytes(b"\x00" * 32)
    (lib / ".DS_Store").write_bytes(b"\x00" * 32)
    (lib / "Thumbs.db").write_bytes(b"\x00" * 32)
    (lib / "desktop.ini").write_bytes(b"\x00" * 32)

    db = tmp_path / "idx.db"
    index_cmd.run(_Args(target=str(lib), db=str(db)))

    conn = idx.open_db(str(db))
    try:
        paths = [
            r["path"] for r in conn.execute("SELECT path FROM samples").fetchall()
        ]
        assert len(paths) == 1
        assert paths[0].endswith("/real.wav")
    finally:
        conn.close()


def test_smpl_root_key_stores_pitch_class_only(tmp_path):
    # MIDI 60 = C4; key column should hold "C" (pitch class), not "C4".
    bytes_c4 = _make_riff_wav(smpl_root_key=60)
    bytes_fs3 = _make_riff_wav(smpl_root_key=54)  # F#3

    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "c_sample.wav").write_bytes(bytes_c4)
    (lib / "fsharp_sample.wav").write_bytes(bytes_fs3)

    db = tmp_path / "idx.db"
    index_cmd.run(_Args(target=str(lib), db=str(db)))

    conn = idx.open_db(str(db))
    try:
        rows = {
            os.path.basename(r["path"]): dict(r)
            for r in conn.execute(
                "SELECT path, key, root_note FROM samples"
            ).fetchall()
        }
        assert rows["c_sample.wav"]["key"] == "C"
        assert rows["c_sample.wav"]["root_note"] == 60
        assert rows["fsharp_sample.wav"]["key"] == "F#"
        assert rows["fsharp_sample.wav"]["root_note"] == 54
    finally:
        conn.close()


def test_import_tags(tmp_path, wav_bytes):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "Drum_Loop.wav").write_bytes(wav_bytes)
    db = tmp_path / "idx.db"

    tags_file = tmp_path / "legacy_tags.json"
    tags_file.write_text(json.dumps({
        "data/samples\\Drum_Loop.wav": {
            "description": "Energetic drum loop",
            "tags": ["drums", "loop"],
        }
    }))

    args = _Args(target=str(lib), db=str(db), import_tags=str(tags_file))
    rc = index_cmd.run(args)
    assert rc == 0

    conn = idx.open_db(str(db))
    try:
        tag_rows = conn.execute(
            "SELECT tag FROM tags ORDER BY tag"
        ).fetchall()
        assert [r["tag"] for r in tag_rows] == ["drums", "loop"]
        desc = conn.execute(
            "SELECT description FROM descriptions"
        ).fetchone()
        assert desc["description"] == "Energetic drum loop"
    finally:
        conn.close()
