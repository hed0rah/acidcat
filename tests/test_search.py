"""core.search: the shared compatible-sample engine (harmonic key + tempo,
kind, keyless handling, half/double-time). Exercised over a temp index so the
CLI `query --compatible-with` and the MCP find_compatible tool share coverage."""

from acidcat.core import search, index as idx


def _libs(tmp_path, rows):
    db = str(tmp_path / "t.db")
    conn = idx.open_db(db)
    for r in rows:
        idx.upsert_sample(conn, r)
    conn.commit()
    conn.close()
    return [{"db_path": db, "label": "t"}]


def _loop(path, key, bpm):
    return {"path": path, "key": key, "bpm": bpm, "format": "wav",
            "duration": 2.0, "acid_beats": 4}


def test_find_compatible_key_and_tempo(tmp_path):
    libs = _libs(tmp_path, [
        _loop("/a/kick_Am_128.wav", "Am", 128),
        _loop("/a/pad_C_128.wav", "C", 128),            # relative of Am
        _loop("/a/lead_Fsm_128.wav", "F#m", 128),       # harmonically incompatible
        _loop("/a/bass_Am_64.wav", "Am", 64),           # half-time
        _loop("/a/perc_Am_200.wav", "Am", 200),         # incompatible tempo
        _loop("/a/verbose_Am_128.wav", "A minor", 128),  # spelling variant
    ])
    rows = search.find_compatible(libs, key="Am", bpm=128, kind="any")
    notes = {r["path"].rsplit("/", 1)[-1]: r["compatibility"] for r in rows}
    assert "lead_Fsm_128.wav" not in notes          # incompatible key dropped
    assert "perc_Am_200.wav" not in notes           # incompatible tempo dropped
    assert notes["pad_C_128.wav"] == "relative, same tempo"
    assert notes["bass_Am_64.wav"] == "same key, half-time"     # half/double window
    assert "verbose_Am_128.wav" in notes            # 'A minor' matched via camelot


def test_find_compatible_no_half_double(tmp_path):
    libs = _libs(tmp_path, [_loop("/a/bass_Am_64.wav", "Am", 64)])
    assert search.find_compatible(libs, key="Am", bpm=128, kind="any",
                                  half_double=False) == []


def test_find_compatible_keyless_matches_only_keyless(tmp_path):
    libs = _libs(tmp_path, [
        _loop("/a/drum.wav", None, 90),
        _loop("/a/tonal_Am_90.wav", "Am", 90),
    ])
    names = {r["path"].rsplit("/", 1)[-1]
             for r in search.find_compatible(libs, key=None, bpm=90, kind="any")}
    assert names == {"drum.wav"}                    # keyless ref -> keyless only


def test_infer_kind():
    assert search.infer_kind(3.0, 0) == "loop"
    assert search.infer_kind(0.5, 0) == "one_shot"
    assert search.infer_kind(1.5, 0) == "any"
    assert search.infer_kind(0.5, 8) == "loop"      # beats force loop
