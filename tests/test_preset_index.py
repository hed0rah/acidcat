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


def test_schema_migration_v1_to_current(tmp_path):
    """An existing v1 DB must migrate cleanly to the current schema: preset
    columns added (v2), samples re-keyed to an integer id and the FTS re-keyed to
    it (v3), data preserved, version bumped. The samples fixture mirrors the real
    v1 audio schema (v1->v2 only ADDED the preset columns; the audio columns were
    always present)."""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
        INSERT INTO meta VALUES ('schema_version', '1');
        CREATE TABLE samples (
            path TEXT PRIMARY KEY, scan_root TEXT, mtime REAL, size INTEGER,
            format TEXT, duration REAL, bpm REAL, key TEXT,
            title TEXT, artist TEXT, album TEXT, genre TEXT, comment TEXT,
            acid_beats INTEGER, root_note INTEGER,
            sample_rate INTEGER, channels INTEGER, bits_per_sample INTEGER,
            chunks TEXT, indexed_at REAL, last_seen_at REAL);
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
    assert "id" in cols                                   # v3 integer primary key
    ver = conn.execute(
        "SELECT v FROM meta WHERE k='schema_version'").fetchone()[0]
    assert ver == str(idx.SCHEMA_VERSION) == "3"
    # original data preserved and FTS rebuilt with the new (wider) column set
    assert conn.execute(
        "SELECT title FROM samples WHERE path='/x.wav'").fetchone()[0] == "kick"
    ftscols = conn.execute("PRAGMA table_info(samples_fts)").fetchall()
    assert any(c[1] == "device" for c in ftscols)
    # the FTS row is keyed to samples.id and matches (rowid re-key worked)
    sid = conn.execute(
        "SELECT id FROM samples WHERE path='/x.wav'").fetchone()[0]
    hit = conn.execute(
        "SELECT rowid FROM samples_fts WHERE samples_fts MATCH 'kick'").fetchone()
    assert hit is not None and hit[0] == sid
    conn.close()


def test_v2_to_v3_fts_rekey(tmp_path):
    """A v2 DB (several samples + tags + descriptions, FTS populated with the old
    arbitrary rowids) migrates to v3 with the FTS re-keyed to samples.id: every
    FTS rowid resolves to a real sample, MATCH still finds the right paths, and a
    delete goes by rowid (removing the sample removes its FTS row)."""
    db = str(tmp_path / "v2.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
        INSERT INTO meta VALUES ('schema_version', '2');
        CREATE TABLE samples (
            path TEXT PRIMARY KEY, scan_root TEXT, mtime REAL, size INTEGER,
            format TEXT, duration REAL, bpm REAL, key TEXT,
            title TEXT, artist TEXT, album TEXT, genre TEXT, comment TEXT,
            acid_beats INTEGER, root_note INTEGER,
            sample_rate INTEGER, channels INTEGER, bits_per_sample INTEGER,
            chunks TEXT, device TEXT, product TEXT, creator TEXT, category TEXT,
            preset_name TEXT, indexed_at REAL, last_seen_at REAL);
        CREATE TABLE scan_roots (path TEXT PRIMARY KEY, added_at REAL,
            last_indexed_at REAL, file_count INTEGER);
        CREATE TABLE descriptions (path TEXT PRIMARY KEY, description TEXT);
        CREATE TABLE tags (path TEXT, tag TEXT, PRIMARY KEY (path, tag));
        CREATE TABLE features (path TEXT PRIMARY KEY, features_json TEXT,
            features_version INTEGER, extracted_at REAL);
        CREATE VIRTUAL TABLE samples_fts USING fts5(
            path, title, artist, album, genre, comment, description, tags,
            preset_name, device, product, creator, category, tokenize='porter');
    """)
    # three samples under a common root; give the FTS deliberately mismatched
    # rowids (100, 200, 300) so a rowid==id assumption from the old data fails.
    for i, (p, title) in enumerate([
            ("/lib/kick_deep.wav", "deep kick"),
            ("/lib/snare_tight.wav", "tight snare"),
            ("/lib/hat_open.wav", "open hat")], start=1):
        conn.execute(
            "INSERT INTO samples (path, scan_root, title) VALUES (?, '/lib', ?)",
            (p, title))
        conn.execute(
            "INSERT INTO samples_fts (rowid, path, title) VALUES (?, ?, ?)",
            (i * 100, p, title))
    conn.execute("INSERT INTO descriptions VALUES ('/lib/kick_deep.wav', 'boomy')")
    conn.execute("INSERT INTO tags VALUES ('/lib/snare_tight.wav', 'crispy')")
    conn.commit()
    conn.close()

    conn = idx.open_db(db)
    try:
        # every FTS rowid resolves to a live sample id (no orphans, no scan needed)
        orphans = conn.execute(
            "SELECT COUNT(*) FROM samples_fts f "
            "LEFT JOIN samples s ON s.id = f.rowid WHERE s.id IS NULL"
        ).fetchone()[0]
        assert orphans == 0

        # MATCH finds the right path, and the description/tag were folded in
        row = conn.execute(
            "SELECT s.path FROM samples_fts f JOIN samples s ON s.id = f.rowid "
            "WHERE samples_fts MATCH 'boomy'").fetchone()
        assert row[0] == "/lib/kick_deep.wav"
        assert conn.execute(
            "SELECT COUNT(*) FROM samples_fts WHERE samples_fts MATCH 'crispy'"
        ).fetchone()[0] == 1

        # deleting the root removes samples AND their FTS rows (delete-by-rowid)
        removed = idx.remove_root(conn, "/lib")
        conn.commit()
        assert removed == 3
        assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM samples_fts").fetchone()[0] == 0
    finally:
        conn.close()
