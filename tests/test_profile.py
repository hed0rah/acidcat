"""The profile pass: a short hit, a long tail, a held tone and a repeating
pattern each get their envelope, the bands say where the energy is, and the
index stores a profile once per file and filters on it."""

import json
import struct

import pytest

from acidcat.core.catalogue import index as idx
from acidcat.core.catalogue import query_sql

np = pytest.importorskip("numpy")

RATE = 44100


def _t(seconds):
    return np.arange(int(RATE * seconds)) / RATE


def _hit():
    t = _t(1.0)
    return np.sin(2 * np.pi * 55 * t) * np.exp(-t * 20)


def _decay():
    t = _t(4.0)
    return np.sin(2 * np.pi * 330 * t) * np.exp(-t * 2.5)


def _sustain():
    t = _t(6.0)
    return 0.5 * np.sin(2 * np.pi * 440 * t) * np.minimum(1, t / 0.2)


def _pulse():
    rng = np.random.default_rng(3)
    y = np.zeros(RATE * 4)
    n = RATE // 10
    for k in range(16):
        i = int(k * 0.25 * RATE)
        y[i:i + n] += rng.standard_normal(n) * np.exp(-np.arange(n) / 600)
    # hats: keep it bright
    return np.diff(y, prepend=0.0)


def _write_wav(path, y):
    pcm = (np.clip(y / max(1e-9, np.abs(y).max()) * 0.9, -1, 1) * 32000).astype("<i2").tobytes()
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack(
        "<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16)
    data = b"data" + struct.pack("<I", len(pcm)) + pcm
    body = b"WAVE" + fmt + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


class TestEstimate:
    @pytest.mark.parametrize("make,want", [
        (_hit, "hit"), (_decay, "decay"), (_sustain, "sustain"), (_pulse, "pulse"),
    ])
    def test_envelope(self, make, want):
        from acidcat.core.analysis.profile import estimate_profile
        assert estimate_profile(make(), RATE)["envelope"] == want

    def test_bands(self):
        from acidcat.core.analysis.profile import estimate_profile
        sub = estimate_profile(_hit(), RATE)
        assert sub["band"] in ("sub", "low") and sub["bands"]["high"] < 0.01
        assert estimate_profile(_sustain(), RATE)["band"] == "lowmid"
        bright = estimate_profile(_pulse(), RATE)
        assert bright["band"] == "high" and bright["centroid_hz"] > 4000

    def test_attack_and_decay_times(self):
        from acidcat.core.analysis.profile import estimate_profile
        d = estimate_profile(_sustain(), RATE)
        # a 200 ms fade-in, measured from 10% amplitude
        assert 100 < d["attack_ms"] < 250
        assert estimate_profile(_hit(), RATE)["decay_ms"] < 300

    def test_nothing_to_measure(self):
        from acidcat.core.analysis.profile import estimate_profile
        assert estimate_profile(np.zeros(RATE), RATE) is None
        assert estimate_profile(np.zeros(100) + 0.5, RATE) is None
        bad = _hit()
        bad[10] = np.nan
        assert estimate_profile(bad, RATE) is None

    def test_reads_a_wav_file(self, tmp_path):
        from acidcat.core.analysis.profile import estimate_profile
        d = estimate_profile(_write_wav(tmp_path / "hit.wav", _hit()))
        assert d["envelope"] == "hit"
        assert d["duration"] == pytest.approx(1.0, abs=0.01)


def _lib(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    _write_wav(lib / "sub.wav", _hit())
    _write_wav(lib / "pad.wav", _sustain())
    _write_wav(lib / "hats.wav", _pulse())
    return lib


def _names(conn, **kw):
    w, p, j = query_sql.build_filter(**kw)
    return sorted(r["path"].rsplit("/", 1)[1] for r in
                  conn.execute(query_sql.assemble(w, j), p))


class TestIndex:
    def test_every_audio_file_gets_a_profile_once(self, tmp_path):
        from acidcat.core.catalogue.indexing import walk_and_upsert
        lib = _lib(tmp_path)
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            walk_and_upsert(conn, str(lib), quiet=True, do_profile=True)
            rows = {r["path"].rsplit("/", 1)[1]: dict(r) for r in conn.execute(
                "SELECT * FROM profile")}
            assert {k: v["envelope"] for k, v in rows.items()} == {
                "sub.wav": "hit", "pad.wav": "sustain", "hats.wav": "pulse"}
            assert rows["hats.wav"]["band"] == "high"
            stamp = rows["pad.wav"]["extracted_at"]

            walk_and_upsert(conn, str(lib), quiet=True, do_profile=True)
            again = conn.execute(
                "SELECT extracted_at FROM profile WHERE path LIKE '%pad.wav'"
            ).fetchone()[0]
            assert again == stamp
            assert idx.index_stats(conn)["with_profile"] == 3
        finally:
            conn.close()

    def test_an_index_without_profile_stores_none(self, tmp_path):
        from acidcat.core.catalogue.indexing import walk_and_upsert
        lib = _lib(tmp_path)
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            walk_and_upsert(conn, str(lib), quiet=True)
            assert conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 0
        finally:
            conn.close()

    def test_prune_drops_the_profile(self, tmp_path):
        from acidcat.core.catalogue.indexing import walk_and_upsert
        lib = _lib(tmp_path)
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            walk_and_upsert(conn, str(lib), quiet=True, do_profile=True)
            (lib / "pad.wav").unlink()
            walk_and_upsert(conn, str(lib), quiet=True, do_profile=True)
            assert conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 2
        finally:
            conn.close()

    def test_filters(self, tmp_path):
        from acidcat.core.catalogue.indexing import walk_and_upsert
        lib = _lib(tmp_path)
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            walk_and_upsert(conn, str(lib), quiet=True, do_profile=True)
            assert _names(conn, envelope=["hit"]) == ["sub.wav"]
            assert _names(conn, envelope=["HIT", "pulse"]) == ["hats.wav", "sub.wav"]
            assert _names(conn, band=["high"]) == ["hats.wav"]
            assert _names(conn, centroid_min=3000) == ["hats.wav"]
            assert _names(conn, centroid_max=600) == ["pad.wav", "sub.wav"]
            assert len(_names(conn)) == 3
            with pytest.raises(ValueError):
                query_sql.build_filter(envelope=["swell"])
            with pytest.raises(ValueError):
                query_sql.build_filter(band=["treble"])
        finally:
            conn.close()


class TestCli:
    def test_analyze_profile_row(self, tmp_path, capsys):
        from acidcat.cli import main
        path = _write_wav(tmp_path / "hit.wav", _hit())
        code = main(["analyze", "--profile", "--output-format", "json", path])
        out = capsys.readouterr().out
        assert code == 0
        (row,) = json.loads(out)
        assert row["envelope"] == "hit"
        assert set(row) >= {"attack_ms", "decay_ms", "band", "band_sub", "band_high"}

    def test_analyze_pitch_and_profile_share_a_row(self, tmp_path, capsys):
        from acidcat.cli import main
        path = _write_wav(tmp_path / "hit.wav", _hit())
        code = main(["analyze", "--pitch", "--profile", "--output-format", "json", path])
        (row,) = json.loads(capsys.readouterr().out)
        assert code == 0
        assert row["note"] == "A1" and row["envelope"] == "hit"


class TestMcp:
    def test_search_by_envelope_and_band(self, tmp_path, monkeypatch):
        from acidcat.core.catalogue import registry as reg
        from acidcat.core.catalogue.indexing import walk_and_upsert
        from acidcat.mcp_server import handlers
        lib = _lib(tmp_path)
        db = str(tmp_path / "lib.db")
        conn = idx.open_db(db)
        walk_and_upsert(conn, str(lib), quiet=True, do_profile=True)
        conn.close()
        regpath = str(tmp_path / "registry.db")
        rconn = reg.open_registry(regpath)
        reg.register_library(rconn, str(lib), "lib", db, in_tree=False,
                             schema_version=idx.SCHEMA_VERSION)
        rconn.commit()
        rconn.close()
        monkeypatch.setattr(handlers, "_REGISTRY_PATH", regpath)
        got = handlers.search_samples({"envelope": ["sustain"]})
        assert [s["path"].rsplit("/", 1)[1] for s in got["samples"]] == ["pad.wav"]
        assert got["samples"][0]["band"] == "lowmid"
        got = handlers.search_samples({"band": "high"})
        assert [s["path"].rsplit("/", 1)[1] for s in got["samples"]] == ["hats.wav"]
        with pytest.raises(handlers.ToolError):
            handlers.search_samples({"envelope": ["swell"]})
