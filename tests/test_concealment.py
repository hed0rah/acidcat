"""Tests for ripper-concealment detection.

The bar here is the false positive rate, not the hit rate. Every pattern this
module looks for is ordinary in music -- sample libraries are full of digital
silence, loops produce byte-identical blocks, percussion is broadband -- and an
earlier version that matched patterns alone fired on 8 to 60 percent of 457 real
WAV files. The gates that fixed it (sector shape, and structural discontinuity
with the neighbourhood) are what these tests exist to protect.
"""

import pytest

# the sample-level checks build signals with numpy (the analysis extra)
np = pytest.importorskip("numpy")

from acidcat.core.forensics import concealment as C

SF = C.SECTOR_FRAMES


def music(n_sectors=12, seed=5):
    """Structured stereo content: correlated sample to sample, with real
    high-frequency energy, so a structureless region stands out from it."""
    rng = np.random.default_rng(seed)
    n = n_sectors * SF
    t = np.arange(n) / 44100.0
    left = (9000 * np.sin(2 * np.pi * 220 * t)
            + 3000 * np.sin(2 * np.pi * 3300 * t)
            + rng.normal(0, 1500, n))
    right = (8000 * np.sin(2 * np.pi * 277 * t)
             + 2600 * np.sin(2 * np.pi * 4100 * t)
             + rng.normal(0, 1500, n))
    return np.stack([left, right], axis=1).astype(np.int16)


def at_sector(k=6):
    return k * SF


def apply(x, how, k=6):
    """Inject one concealment strategy at a sector-aligned position."""
    s = x.copy()
    a = at_sector(k)
    b = a + SF
    if how == "mute":
        s[a:b] = 0
    elif how == "hold":
        s[a:b] = s[a - 1]
    elif how == "interpolate":
        lo, hi = s[a - 1].astype(np.float64), s[b].astype(np.float64)
        t = np.linspace(0, 1, SF, endpoint=False)[:, None]
        s[a:b] = (lo + (hi - lo) * t).astype(np.int16)
    elif how == "repeat":
        s[a:b] = s[a - SF:a]
    return s


# ── the strategies it must find ─────────────────────────────────────

@pytest.mark.parametrize("how", ["mute", "hold", "interpolate", "repeat"])
def test_each_strategy_is_found_and_named(how):
    f = C.scan(apply(music(), how))
    assert f, f"{how} went undetected"
    assert f[0]["strategy"] == how, f"{how} reported as {f[0]['strategy']}"
    assert f[0]["frame"] == at_sector()


@pytest.mark.parametrize("how", ["mute", "hold", "interpolate", "repeat"])
def test_the_finding_is_localised(how):
    f = C.scan(apply(music(), how))
    assert f[0]["sector"] == 6
    assert abs(f[0]["frames"] - SF) <= SF * 0.35


def test_clean_audio_is_silent():
    assert C.scan(music()) == []


def test_the_nesting_resolves_to_the_most_specific_strategy():
    """A run of zeros is also constant, and a constant run is also linear, so
    silence matches all three detectors. The narrowest match is the right
    answer, and reporting all three would be three findings for one event."""
    f = C.scan(apply(music(), "mute"))
    assert len(f) == 1
    assert f[0]["strategy"] == "mute"


# ── the false positives that killed the first version ───────────────

def test_leading_and_trailing_silence_is_not_concealment():
    """Every sample library has this. It is a file that starts quietly, not a
    sector that could not be read."""
    x = music()
    x[:SF * 2] = 0
    x[-SF * 2:] = 0
    assert C.scan(x) == []


def test_a_loop_is_not_concealment():
    """Loops repeat many times; a concealed sector repeats exactly once."""
    x = music(n_sectors=16)
    block = x[SF * 4:SF * 5]
    for k in range(5, 12):
        x[k * SF:(k + 1) * SF] = block
    assert C.scan(x) == []


def test_a_quiet_passage_inside_quiet_music_is_not_concealment():
    """The gate is structural contrast, not absolute level."""
    x = (music() // 40).astype(np.int16)
    assert C.scan(x) == []


def test_an_unaligned_gap_is_not_reported():
    """Concealment lands on the 588-frame CD grid. A dropout that does not is
    something else, and calling it a rip artifact would be wrong."""
    x = music()
    a = at_sector() + 137          # deliberately off the grid
    x[a:a + SF] = 0
    assert C.scan(x) == []


def test_a_gap_of_the_wrong_length_is_not_reported():
    x = music()
    a = at_sector()
    x[a:a + SF // 4] = 0           # far too short to be a sector
    assert C.scan(x) == []


# ── shape and honesty of the output ─────────────────────────────────

def test_too_short_to_have_a_neighbourhood_returns_nothing():
    """With no audio either side there is nothing to be discontinuous with, and
    guessing would be worse than declining."""
    assert C.scan(np.zeros((SF * 2, 2), dtype=np.int16)) == []


def test_mono_input_is_accepted():
    x = music()[:, :1]
    f = C.scan(apply(x, "mute"))
    assert f and f[0]["strategy"] == "mute"


def test_summary_names_the_sector_grid():
    """The alignment is the part that identifies the ORIGIN -- 588 frames is not
    a length that arises by accident in a file that never touched a CD."""
    note = C.summarise(C.scan(apply(music(), "mute")))
    assert "588" in note and "CD" in note


def test_summary_is_none_when_nothing_was_found():
    assert C.summarise([]) is None


def test_raw_passthrough_is_not_claimed():
    """Deliberately undetected. Measured over 588-sample windows, real audio and
    random data overlap by 40 percent on lag-1 autocorrelation, so claiming this
    would mean a false positive on roughly six real files in ten."""
    x = music()
    a = at_sector()
    rng = np.random.default_rng(3)
    x[a:a + SF] = rng.integers(-32768, 32767, (SF, 2), dtype=np.int16)
    assert all(f["strategy"] != "raw" for f in C.scan(x))


# ── wired into audit --signal ───────────────────────────────────────

def _wav(path, samples):
    import struct
    b = samples.astype("<i2").tobytes()
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(b)) + b"WAVEfmt "
                + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 44100 * 4, 4, 16)
                + b"data" + struct.pack("<I", len(b)) + b)


def _audit(path, *extra):
    import subprocess
    import sys
    return subprocess.run([sys.executable, "-m", "acidcat", "audit", str(path),
                           "--signal", *extra], capture_output=True, text=True)


def test_audit_signal_reports_a_concealed_sector(tmp_path):
    p = tmp_path / "concealed.wav"
    _wav(p, apply(music(n_sectors=14), "mute"))
    r = _audit(p)
    assert "concealed-sectors" in r.stdout
    assert "588" in r.stdout, "the sector grid is the origin evidence, name it"
    assert r.returncode == 1


def test_audit_signal_is_silent_on_clean_audio(tmp_path):
    p = tmp_path / "clean.wav"
    _wav(p, music(n_sectors=14))
    r = _audit(p)
    # "concealed-sectors" specifically: the ordinary HIDDEN line already says
    # "no concealed or appended data", so a bare substring check passes for the
    # wrong reason on a clean file and fails for the wrong reason here
    assert "concealed-sectors" not in r.stdout
    assert r.returncode == 0


def test_a_skipped_check_is_reported_but_not_counted(tmp_path):
    """24-bit audio did not come off a Red Book disc, so concealment analysis
    does not apply. Saying nothing would read as "clean"; counting it as a
    mismatch would make every 24-bit file a failure. It is named separately."""
    # A committed 24-bit WAV rather than one built here by ffmpeg. The check
    # under test fires on sample width alone, so the specimen only has to BE
    # 24-bit -- and building it per-run made this test depend on a package feed
    # being up, which is how it went red on 2026-08-10 while nothing about the
    # product had changed.
    import shutil
    from conftest import CORPUS_WAV24
    deep = tmp_path / "deep.wav"
    shutil.copyfile(CORPUS_WAV24, deep)
    out = _audit(deep).stdout
    assert "NOT CHECKED" in out
    assert "24-bit" in out
    # the section count and the verdict count must agree, and neither may
    # include the skipped check
    import re
    sect = re.search(r"INTEGRITY\s+(\d+) mismatch", out)
    verd = re.search(r"VERDICT: (\d+) integrity mismatch", out)
    if sect and verd:
        assert sect.group(1) == verd.group(1), out


def test_alignment_slack_covers_the_anchor_sample(tmp_path):
    """An interpolation is anchored ON the last good sample, so that sample is
    collinear with the line drawn from it and joins the run -- the detected run
    starts one frame before the sector boundary. Demanding exact alignment
    misses every interpolated gap, which is how this surfaced: against
    sector-aligned specimens the interp case was the only one not found."""
    x = music(n_sectors=14)
    a = at_sector()
    lo, hi = x[a - 1].astype(np.float64), x[a + SF].astype(np.float64)
    t = np.linspace(0, 1, SF, endpoint=False)[:, None]
    x[a:a + SF] = (lo + (hi - lo) * t).astype(np.int16)
    f = C.scan(x)
    assert f, "an anchored interpolation went undetected"
    assert f[0]["strategy"] == "interpolate"
    assert abs(f[0]["frame"] - a) <= 2


def test_the_slack_is_small_enough_to_stay_strict():
    """Two frames out of 588 is 0.3 percent. A gap well off the grid must still
    be rejected, or the slack has quietly become 'any offset'."""
    x = music(n_sectors=14)
    a = at_sector() + 40           # far off the grid, well beyond the slack
    x[a:a + SF] = 0
    assert C.scan(x) == []
