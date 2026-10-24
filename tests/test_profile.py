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


def _dry():
    """a plucked note that is over in a few hundred ms, then silence"""
    t = _t(2.5)
    return np.sin(2 * np.pi * 330 * t) * np.exp(-t * 30) * np.minimum(1, t / 0.002)


def _room(rt60=1.2, seconds=2.0, seed=0):
    """a synthetic reverb: decorrelated noise for each ear, decaying 60 dB in rt60"""
    rng = np.random.default_rng(seed)
    t = _t(seconds)
    env = np.exp(-6.9078 * t / rt60)
    return [rng.standard_normal(len(t)) * env for _ in range(2)]


def _wet(direct=0.5):
    """the same note sent through _room, with some of the direct sound kept"""
    x = _dry()
    n = len(x)
    out = []
    for ir in _room():
        m = 1 << int(np.ceil(np.log2(n + len(ir))))
        tail = np.fft.irfft(np.fft.rfft(x, m) * np.fft.rfft(ir, m), m)[:n]
        tail *= np.sqrt((x ** 2).sum() / (tail ** 2).sum())
        out.append(direct * x + tail)
    return np.stack(out, axis=1)


def _write_wav(path, y):
    y = np.asarray(y)
    ch = 1 if y.ndim == 1 else y.shape[1]
    pcm = (np.clip(y / max(1e-9, np.abs(y).max()) * 0.9, -1, 1) * 32000).astype("<i2").tobytes()
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack(
        "<HHIIHH", 1, ch, RATE, RATE * 2 * ch, 2 * ch, 16)
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


class TestWetness:
    """how much of a sound is its room: the dry note against the same note
    through a synthetic 1.2 s reverb"""

    def _w(self, y):
        from acidcat.core.analysis.profile import estimate_profile
        return estimate_profile(y, RATE)["wetness"]

    def test_dry_and_wet(self):
        dry, wet = self._w(_dry()), self._w(_wet())
        assert 0 <= dry < 0.2
        assert 0.6 < wet <= 1

    def test_more_room_reads_wetter(self):
        assert self._w(_wet(direct=2.0)) < self._w(_wet(direct=0.5))

    def test_the_tail_alone_says_wet_in_mono(self):
        # a mono file has no stereo evidence; the decay still tells
        assert self._w(_wet().mean(axis=1)) > 0.5

    def test_a_dry_note_on_two_identical_channels_stays_dry(self):
        x = _dry()
        assert self._w(np.stack([x, x], axis=1)) < 0.2

    def test_a_short_dry_hit_is_dry(self):
        # a hit shorter than the measuring window still has its fall seen
        t = _t(0.15)
        assert self._w(np.sin(2 * np.pi * 200 * t) * np.exp(-t * 60)) < 0.2

    def test_stereo_file_reads_like_the_array(self, tmp_path):
        from acidcat.core.analysis.profile import estimate_profile
        path = _write_wav(tmp_path / "wet.wav", _wet())
        assert estimate_profile(path)["wetness"] == pytest.approx(self._w(_wet()), abs=0.05)

    def test_the_version_moved(self):
        # rows measured before wetness existed are measured again
        from acidcat.core.analysis.profile import PROFILE_VERSION
        assert PROFILE_VERSION >= 2


def _lib(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    _write_wav(lib / "sub.wav", _hit())
    _write_wav(lib / "pad.wav", _sustain())
    _write_wav(lib / "hats.wav", _pulse())
    return lib


def _wet_lib(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    _write_wav(lib / "dry.wav", _dry())
    _write_wav(lib / "wet.wav", _wet())
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

    def test_wetness_is_stored_and_filtered(self, tmp_path):
        from acidcat.core.catalogue.indexing import walk_and_upsert
        lib = _wet_lib(tmp_path)
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            walk_and_upsert(conn, str(lib), quiet=True, do_profile=True)
            got = {r["path"].rsplit("/", 1)[1]: r["wetness"] for r in conn.execute(
                "SELECT path, wetness FROM profile")}
            assert got["dry.wav"] < 0.2 < 0.6 < got["wet.wav"]
            assert _names(conn, wetness_min=0.5) == ["wet.wav"]
            assert _names(conn, wetness_max=0.3) == ["dry.wav"]
            assert _names(conn, wetness_min=0.0, wetness_max=1.0) == ["dry.wav", "wet.wav"]
        finally:
            conn.close()

    def test_a_profile_from_before_wetness_is_measured_again(self, tmp_path):
        from acidcat.core.analysis.profile import PROFILE_VERSION
        from acidcat.core.catalogue.indexing import walk_and_upsert
        lib = _wet_lib(tmp_path)
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            walk_and_upsert(conn, str(lib), quiet=True, do_profile=True)
            conn.execute("UPDATE profile SET profile_version = 1, wetness = NULL")
            conn.commit()
            walk_and_upsert(conn, str(lib), quiet=True, do_profile=True)
            rows = conn.execute("SELECT profile_version, wetness FROM profile").fetchall()
            assert [r[0] for r in rows] == [PROFILE_VERSION] * 2
            assert all(r[1] is not None for r in rows)
        finally:
            conn.close()


class TestMigrationV5:
    def test_v4_profile_rows_survive_and_gain_wetness(self, tmp_path):
        db = str(tmp_path / "lib.db")
        conn = idx.open_db(db)
        conn.execute("INSERT INTO samples (path, format, duration) "
                     "VALUES ('/a.wav', 'wav', 1.0)")
        conn.execute("INSERT INTO profile (path, profile_version, envelope, band) "
                     "VALUES ('/a.wav', 1, 'hit', 'sub')")
        conn.commit()
        # roll the file back to the v4 layout: profile without wetness
        conn.execute("ALTER TABLE profile DROP COLUMN wetness")
        conn.execute("UPDATE meta SET v = '4' WHERE k = 'schema_version'")
        conn.commit()
        conn.close()

        conn = idx.open_db(db)
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(profile)")}
            row = dict(conn.execute(
                "SELECT profile_version, envelope, band, wetness FROM profile").fetchone())
            ver = conn.execute(
                "SELECT v FROM meta WHERE k = 'schema_version'").fetchone()[0]
            due = idx.profile_due(conn, "/a.wav", 2)
        finally:
            conn.close()
        assert ver == str(idx.SCHEMA_VERSION) == "5"
        assert "wetness" in cols
        assert row == {"profile_version": 1, "envelope": "hit", "band": "sub",
                       "wetness": None}
        assert due

    def test_a_partial_v5_step_does_not_wedge_the_next_open(self, tmp_path):
        # the column is there but the version still says 4 (an interrupted
        # migration, or a build that added it by hand): the step is a no-op
        db = str(tmp_path / "lib.db")
        conn = idx.open_db(db)
        conn.execute("UPDATE meta SET v = '4' WHERE k = 'schema_version'")
        conn.commit()
        conn.close()
        conn = idx.open_db(db)
        try:
            ver = conn.execute(
                "SELECT v FROM meta WHERE k = 'schema_version'").fetchone()[0]
        finally:
            conn.close()
        assert ver == "5"

    def test_fresh_db_has_wetness(self, tmp_path):
        conn = idx.open_db(str(tmp_path / "lib.db"))
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(profile)")}
        finally:
            conn.close()
        assert "wetness" in cols
        assert "wetness" in idx.PROFILE_COLUMNS


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

    def test_analyze_profile_has_wetness(self, tmp_path, capsys):
        from acidcat.cli import main
        path = _write_wav(tmp_path / "wet.wav", _wet())
        code = main(["analyze", "--profile", "--output-format", "json", path])
        (row,) = json.loads(capsys.readouterr().out)
        assert code == 0
        assert row["wetness"] > 0.6

    def test_lib_query_wet(self, tmp_path, capsys):
        from acidcat.cli import main
        regpath = _registered(tmp_path, _wet_lib(tmp_path))
        code = main(["lib", "query", "--registry", regpath, "--wet", "0.5:",
                     "--output-format", "json"])
        out = json.loads(capsys.readouterr().out)
        assert code == 0
        assert [r["path"].rsplit("/", 1)[1] for r in out] == ["wet.wav"]
        assert out[0]["wetness"] > 0.6
        code = main(["lib", "query", "--registry", regpath, "--wet", ":0.3",
                     "--output-format", "json"])
        out = json.loads(capsys.readouterr().out)
        assert [r["path"].rsplit("/", 1)[1] for r in out] == ["dry.wav"]

    def test_lib_query_bad_wet_is_a_usage_error(self, tmp_path, capsys):
        from acidcat.cli import main
        regpath = _registered(tmp_path, _wet_lib(tmp_path))
        assert main(["lib", "query", "--registry", regpath, "--wet", "damp"]) == 2


def _registered(tmp_path, lib):
    """index `lib` with profile into its own DB and register it"""
    from acidcat.core.catalogue import registry as reg
    from acidcat.core.catalogue.indexing import walk_and_upsert
    db = str(tmp_path / "lib.db")
    conn = idx.open_db(db)
    walk_and_upsert(conn, str(lib), quiet=True, do_profile=True)
    conn.commit()
    conn.close()
    regpath = str(tmp_path / "registry.db")
    rconn = reg.open_registry(regpath)
    reg.register_library(rconn, str(lib), "lib", db, in_tree=False,
                         schema_version=idx.SCHEMA_VERSION)
    rconn.commit()
    rconn.close()
    return regpath


class TestMcpWetness:
    def test_search_and_get_sample(self, tmp_path, monkeypatch):
        from acidcat.mcp_server import handlers
        lib = _wet_lib(tmp_path)
        monkeypatch.setattr(handlers, "_REGISTRY_PATH", _registered(tmp_path, lib))
        got = handlers.search_samples({"wetness_min": 0.5})
        assert [s["path"].rsplit("/", 1)[1] for s in got["samples"]] == ["wet.wav"]
        assert got["samples"][0]["wetness"] > 0.6
        got = handlers.search_samples({"wetness_max": 0.3})
        assert [s["path"].rsplit("/", 1)[1] for s in got["samples"]] == ["dry.wav"]
        one = handlers.get_sample({"path": str(lib / "dry.wav")})
        assert one["wetness"] < 0.2

    def test_schema_offers_the_filter(self):
        from acidcat.mcp_server import schema
        tools = {t["name"]: t for t in schema.TOOLS}
        props = tools["search_samples"]["input_schema"]["properties"]
        assert {"wetness_min", "wetness_max"} <= set(props)


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
