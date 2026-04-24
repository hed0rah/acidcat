"""Tests for `acidcat query` -- filtering over the SQLite index."""

import io
import time

import pytest

from acidcat.commands import query as query_cmd
from acidcat.core import index as idx


class _Args:
    def __init__(self, **kw):
        defaults = {
            "db": None,
            "bpm": None, "key": None, "duration": None, "tag": [],
            "file_format": None, "text": None, "root": None,
            "limit": 50,
            "output_format": "json",
            "output": None,
            "paths_only": False,
        }
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


ROOT_A = idx.normalize_path("/tmp/lib/a")
ROOT_B = idx.normalize_path("/tmp/lib/b")
P_KICK = ROOT_A + "/kick_120.wav"
P_HAT = ROOT_A + "/hat_128.wav"
P_SYNTH = ROOT_B + "/synth_124.flac"


def _seed(db_path):
    conn = idx.open_db(db_path)
    now = time.time()
    rows = [
        {
            "path": P_KICK, "scan_root": ROOT_A,
            "format": "wav", "duration": 1.2, "bpm": 120.0, "key": "C",
            "title": "kick one", "artist": None, "album": None,
            "genre": None, "comment": "booming sub", "mtime": now,
            "size": 100, "indexed_at": now, "last_seen_at": now,
            "chunks": "acid,smpl",
            "acid_beats": 4, "root_note": 60,
            "sample_rate": 44100, "channels": 1, "bits_per_sample": 16,
        },
        {
            "path": P_HAT, "scan_root": ROOT_A,
            "format": "wav", "duration": 0.5, "bpm": 128.0, "key": "Am",
            "title": "closed hat", "artist": None, "album": None,
            "genre": None, "comment": None, "mtime": now,
            "size": 100, "indexed_at": now, "last_seen_at": now,
            "chunks": None, "acid_beats": None, "root_note": None,
            "sample_rate": None, "channels": None, "bits_per_sample": None,
        },
        {
            "path": P_SYNTH, "scan_root": ROOT_B,
            "format": "flac", "duration": 8.0, "bpm": 124.0, "key": "Fm",
            "title": "dusty synth", "artist": "someone",
            "album": "dust pack", "genre": "electronic",
            "comment": "warm and lofi", "mtime": now,
            "size": 200, "indexed_at": now, "last_seen_at": now,
            "chunks": None, "acid_beats": None, "root_note": None,
            "sample_rate": 44100, "channels": 2, "bits_per_sample": 16,
        },
    ]
    for r in rows:
        idx.upsert_sample(conn, r)

    idx.upsert_tags(conn, P_KICK, ["drums", "kick", "punchy"])
    idx.upsert_tags(conn, P_HAT, ["drums", "hat"])
    idx.upsert_tags(conn, P_SYNTH, ["synth", "pad"])

    idx.record_scan_root(conn, ROOT_A, 2, now)
    idx.record_scan_root(conn, ROOT_B, 1, now)

    conn.commit()
    conn.close()


def test_bpm_range(tmp_path):
    db = tmp_path / "q.db"
    _seed(str(db))

    rows = _run(db, bpm="120:125")
    paths = {r["path"] for r in rows}
    assert paths == {P_KICK, P_SYNTH}


def test_bpm_exact(tmp_path):
    db = tmp_path / "q.db"
    _seed(str(db))
    rows = _run(db, bpm="128")
    assert len(rows) == 1
    assert rows[0]["bpm"] == 128.0


def test_duration_range(tmp_path):
    db = tmp_path / "q.db"
    _seed(str(db))
    rows = _run(db, duration=":1")
    assert len(rows) == 1
    assert rows[0]["path"] == P_HAT


def test_key_filter(tmp_path):
    db = tmp_path / "q.db"
    _seed(str(db))
    rows = _run(db, key="am")
    assert len(rows) == 1
    assert rows[0]["path"] == P_HAT


def test_format_filter(tmp_path):
    db = tmp_path / "q.db"
    _seed(str(db))
    rows = _run(db, file_format="flac")
    assert len(rows) == 1


def test_tag_filter_and(tmp_path):
    db = tmp_path / "q.db"
    _seed(str(db))
    rows = _run(db, tag=["drums"])
    paths = {r["path"] for r in rows}
    assert paths == {P_KICK, P_HAT}

    rows = _run(db, tag=["drums", "kick"])
    assert len(rows) == 1
    assert rows[0]["path"] == P_KICK


def test_root_filter(tmp_path):
    db = tmp_path / "q.db"
    _seed(str(db))
    rows = _run(db, root=ROOT_A)
    assert len(rows) == 2
    rows = _run(db, root=ROOT_B)
    assert len(rows) == 1


def test_text_fts(tmp_path):
    db = tmp_path / "q.db"
    _seed(str(db))

    rows = _run(db, text="dusty")
    assert len(rows) == 1
    assert rows[0]["path"] == P_SYNTH

    rows = _run(db, text="punchy")
    assert len(rows) == 1
    assert rows[0]["path"] == P_KICK

    rows = _run(db, text="synth")
    paths = {r["path"] for r in rows}
    assert paths == {P_SYNTH}


def test_limit(tmp_path):
    db = tmp_path / "q.db"
    _seed(str(db))
    rows = _run(db, limit=1)
    assert len(rows) == 1


def test_no_index_returns_error(tmp_path, capsys):
    args = _Args(db=str(tmp_path / "nope.db"))
    rc = query_cmd.run(args)
    assert rc == 1


def _run(db, **kwargs):
    buf = io.StringIO()
    args = _Args(db=str(db), output_format="json", **kwargs)
    # capture to a buffer via the output arg path
    out_file = str(db) + ".out.json"
    args.output = out_file
    rc = query_cmd.run(args)
    assert rc == 0
    import json
    with open(out_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]
