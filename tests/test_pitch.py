"""The one-shot pitch estimator and its place in the library index: a tuned
tone reads as its note and offset, noise reads as no pitch, and the index
stores an estimate once per file and filters on it."""

import json
import math
import struct

import pytest

from acidcat.core.analysis.pitch import note_name, parse_note
from acidcat.core.catalogue import index as idx
from acidcat.core.catalogue import query_sql

np = pytest.importorskip("numpy")

RATE = 44100


def _tone(hz, seconds=1.0, decay=3.0, glide_cents=0.0):
    """A decaying sine at `hz`, optionally starting `glide_cents` high and
    falling to it over the first 80 ms, as an 808 does."""
    t = np.arange(int(RATE * seconds)) / RATE
    cents = glide_cents * np.clip(1 - t / 0.08, 0, 1)
    phase = 2 * np.pi * np.cumsum(hz * 2 ** (cents / 1200)) / RATE
    return np.sin(phase) * np.exp(-t * decay)


def _write_wav(path, y):
    pcm = (np.clip(y, -1, 1) * 32000).astype("<i2").tobytes()
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack(
        "<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16)
    data = b"data" + struct.pack("<I", len(pcm)) + pcm
    body = b"WAVE" + fmt + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


class TestEstimate:
    def test_tuned_tone_is_its_note(self):
        from acidcat.core.analysis.pitch import estimate_pitch
        r = estimate_pitch(_tone(55.0), RATE)
        assert (r["note_name"], r["midi_note"], r["pitch_class"]) == ("A1", 33, 9)
        assert abs(r["cents"]) < 2
        assert r["tunability"] > 0.9

    def test_detuned_tone_reports_its_offset(self):
        from acidcat.core.analysis.pitch import estimate_pitch
        r = estimate_pitch(_tone(110.0 * 2 ** (30 / 1200)), RATE)
        assert r["note_name"] == "A2"
        assert 25 < r["cents"] < 35

    def test_falling_start_is_a_positive_glide(self):
        from acidcat.core.analysis.pitch import estimate_pitch
        r = estimate_pitch(_tone(49.0, glide_cents=400), RATE)
        assert r["note_name"] == "G1"
        assert r["glide_cents"] > 100

    def test_noise_has_no_note(self):
        from acidcat.core.analysis.pitch import estimate_pitch
        rng = np.random.default_rng(7)
        y = rng.standard_normal(RATE // 2) * np.exp(-np.arange(RATE // 2) / 4000)
        r = estimate_pitch(y, RATE)
        assert r["midi_note"] is None and r["note_name"] is None
        assert r["tunability"] < 0.35

    def test_silence_has_no_note(self):
        from acidcat.core.analysis.pitch import estimate_pitch
        r = estimate_pitch(np.zeros(RATE // 4), RATE)
        assert r["midi_note"] is None and r["tunability"] == 0.0

    def test_reads_a_wav_file(self, tmp_path):
        from acidcat.core.analysis.pitch import estimate_pitch
        r = estimate_pitch(_write_wav(tmp_path / "hit.wav", _tone(65.41)))
        assert r["note_name"] == "C2"


class TestNotes:
    def test_names(self):
        assert note_name(60) == "C4"
        assert note_name(30) == "F#1"

    @pytest.mark.parametrize("spec,want", [
        ("F#", (6, None)), ("gb", (6, None)), ("A", (9, None)),
        ("F#1", (6, 30)), ("C4", (0, 60)), ("Bb0", (10, 22)),
        ("Cb4", (11, 59)), ("B#3", (0, 60)), ("C-1", (0, 0)),
    ])
    def test_parse(self, spec, want):
        assert parse_note(spec) == want

    @pytest.mark.parametrize("spec", ["", "H", "F#x", "C99", "minor"])
    def test_parse_rejects(self, spec):
        assert parse_note(spec) is None


def _lib(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    _write_wav(lib / "sub_a1.wav", _tone(55.0))
    _write_wav(lib / "tom_c3.wav", _tone(130.81, seconds=0.5, decay=6))
    _write_wav(lib / "pad.wav", _tone(220.0, seconds=6.0, decay=0.1))
    return lib


class TestIndex:
    def test_short_files_get_a_pitch_once(self, tmp_path):
        from acidcat.core.catalogue.indexing import walk_and_upsert
        lib = _lib(tmp_path)
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            walk_and_upsert(conn, str(lib), quiet=True, do_pitch=True)
            rows = {r["path"].rsplit("/", 1)[1]: dict(r) for r in conn.execute(
                "SELECT * FROM pitch")}
            # the 6 s pad is a loop by length: no estimate
            assert set(rows) == {"sub_a1.wav", "tom_c3.wav"}
            assert rows["sub_a1.wav"]["pitch_note"] == 33
            assert rows["tom_c3.wav"]["pitch_note"] == 48
            stamp = rows["sub_a1.wav"]["extracted_at"]

            walk_and_upsert(conn, str(lib), quiet=True, do_pitch=True)
            again = conn.execute(
                "SELECT extracted_at FROM pitch WHERE path LIKE '%sub_a1.wav'"
            ).fetchone()[0]
            assert again == stamp
            assert idx.index_stats(conn)["with_pitch"] == 2
        finally:
            conn.close()

    def test_an_index_without_pitch_stores_none(self, tmp_path):
        from acidcat.core.catalogue.indexing import walk_and_upsert
        lib = _lib(tmp_path)
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            walk_and_upsert(conn, str(lib), quiet=True)
            assert conn.execute("SELECT COUNT(*) FROM pitch").fetchone()[0] == 0
        finally:
            conn.close()

    def test_prune_drops_the_pitch(self, tmp_path):
        from acidcat.core.catalogue.indexing import walk_and_upsert
        lib = _lib(tmp_path)
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            walk_and_upsert(conn, str(lib), quiet=True, do_pitch=True)
            (lib / "sub_a1.wav").unlink()
            walk_and_upsert(conn, str(lib), quiet=True, do_pitch=True)
            assert conn.execute("SELECT COUNT(*) FROM pitch").fetchone()[0] == 1
        finally:
            conn.close()

    def test_filters(self, tmp_path):
        from acidcat.core.catalogue.indexing import walk_and_upsert
        lib = _lib(tmp_path)
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            walk_and_upsert(conn, str(lib), quiet=True, do_pitch=True)

            def names(**kw):
                w, p, j = query_sql.build_filter(**kw)
                return sorted(r["path"].rsplit("/", 1)[1] for r in
                              conn.execute(query_sql.assemble(w, j), p))

            assert names(note="A") == ["sub_a1.wav"]
            assert names(note="A1") == ["sub_a1.wav"]
            assert names(note="A2") == []
            assert names(note="C") == ["tom_c3.wav"]
            assert names(tunability_min=0.5) == ["sub_a1.wav", "tom_c3.wav"]
            # no filter: every row, the pad with empty pitch columns
            assert len(names()) == 3
            with pytest.raises(ValueError):
                query_sql.build_filter(note="H#")
        finally:
            conn.close()


class TestAnalyzeCli:
    def test_pitch_row(self, tmp_path, capsys):
        from acidcat.cli import main
        path = _write_wav(tmp_path / "hit.wav", _tone(55.0))
        code = main(["analyze", "--pitch", "--output-format", "json", path])
        out = capsys.readouterr().out
        assert code == 0
        (row,) = json.loads(out)
        assert row["note"] == "A1"
        assert abs(row["cents"]) < 2 and math.isfinite(row["f0_hz"])
