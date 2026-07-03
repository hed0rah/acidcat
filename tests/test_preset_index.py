"""tests for preset-metadata extraction and its flow into the index schema."""

import sqlite3
import struct

from acidcat.core import preset_meta
from acidcat.core import index as idx


def _bw_field(key, val):
    return (struct.pack(">I", len(key)) + key.encode() + b"\x08"
            + struct.pack(">I", len(val)) + val.encode())


def test_extract_bitwig():
    data = (b"BtWg0003000200"
            + _bw_field("device_name", "Polysynth")
            + _bw_field("creator", "hed0rah")
            + _bw_field("device_category", "Synth")
            + _bw_field("tags", "bass wide"))
    m = preset_meta.extract(data)
    assert m["preset_name"] == "Polysynth" and m["device"] == "Polysynth"
    assert m["product"] == "Bitwig" and m["creator"] == "hed0rah"
    assert m["category"] == "Synth" and m["tags"] == ["bass", "wide"]


def test_extract_vital():
    data = (b'{"author":"Flamedragonz","preset_name":"EG",'
            b'"preset_style":"Bass","synth_version":"1.0.7"}')
    m = preset_meta.extract(data)
    assert m["preset_name"] == "EG" and m["product"] == "Vital"
    assert m["creator"] == "Flamedragonz" and m["category"] == "Bass"


def test_extract_ni_hsin():
    def p16(s):
        return struct.pack("<I", len(s)) + s.encode("utf-16-le")
    body = bytearray(0x30)
    body[0x0C:0x10] = b"hsin"
    body += p16("1.3.1.0") + p16("nicecombo") + p16("Massive")
    struct.pack_into("<Q", body, 0, len(body))
    m = preset_meta.extract(bytes(body))
    assert m["preset_name"] == "nicecombo" and m["product"] == "Massive"


def test_extract_none_for_non_preset():
    assert preset_meta.extract(b"RIFF____WAVEfmt ") is None


def test_schema_migration_v1_to_v2(tmp_path):
    """An existing v1 DB must migrate cleanly: preset columns added, FTS widened,
    data preserved, version bumped."""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
        INSERT INTO meta VALUES ('schema_version', '1');
        CREATE TABLE samples (path TEXT PRIMARY KEY, title TEXT, artist TEXT,
            album TEXT, genre TEXT, comment TEXT);
        INSERT INTO samples (path, title) VALUES ('/x.wav', 'kick');
        CREATE TABLE descriptions (path TEXT PRIMARY KEY, description TEXT);
        CREATE TABLE tags (path TEXT, tag TEXT, PRIMARY KEY (path, tag));
        CREATE VIRTUAL TABLE samples_fts USING fts5(path, title, artist, album,
            genre, comment, description, tags);
    """)
    conn.commit()
    conn.close()

    conn = idx.open_db(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(samples)")}
    assert {"device", "product", "creator", "category", "preset_name"} <= cols
    ver = conn.execute(
        "SELECT v FROM meta WHERE k='schema_version'").fetchone()[0]
    assert ver == str(idx.SCHEMA_VERSION) == "2"
    # original data preserved and FTS rebuilt with the new (wider) column set
    assert conn.execute(
        "SELECT title FROM samples WHERE path='/x.wav'").fetchone()[0] == "kick"
    ftscols = conn.execute("PRAGMA table_info(samples_fts)").fetchall()
    assert any(c[1] == "device" for c in ftscols)
    conn.close()
