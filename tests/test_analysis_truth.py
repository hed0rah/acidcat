"""Does the audio match what the container claims?

These checks answer "is this file what it says it is" for a sample library:
a WAV that is really a decoded MP3, and a stereo file carrying one signal
twice. Both were validated against ffmpeg-encoded corpora during development;
the suite reproduces the same signals synthetically so it stays deterministic
and needs no encoder installed.

The threshold that matters was set from measurement, not taste: on encoded
material MP3 scored 57-89 dB while every clean control -- broadband noise, a
pure tone, a dark pad, a quiet file, a resampled file, a gentle 10 kHz lowpass
-- stayed at or below 29.3 dB. The wall threshold sits between them at 40 dB.
"""

import math

import pytest

from acidcat.core.analysis import bandwidth, channels

np = pytest.importorskip("numpy")

RATE = 44100
DUR = 4


def _noise(n=RATE * DUR, seed=7):
    return np.random.default_rng(seed).standard_normal(n) * 0.2


def _brickwall(y, rate, cutoff):
    """Zero every bin above `cutoff` -- what a lossy encoder does to a band."""
    spec = np.fft.rfft(y)
    spec[np.fft.rfftfreq(len(y), 1.0 / rate) > cutoff] = 0
    return np.fft.irfft(spec, n=len(y))


def _gentle_lowpass(y, rate, cutoff, order=2):
    """A sloping filter: the thing a brick wall must not be confused with."""
    freqs = np.fft.rfftfreq(len(y), 1.0 / rate)
    with np.errstate(divide="ignore"):
        mag = 1.0 / np.sqrt(1.0 + (freqs / cutoff) ** (2 * order))
    return np.fft.irfft(np.fft.rfft(y) * mag, n=len(y))


def _tone(freq=440.0, n=RATE * DUR):
    return 0.5 * np.sin(2 * math.pi * freq * np.arange(n) / RATE)


# ---------------------------------------------------------------- bandwidth

def test_brickwalled_audio_is_caught():
    """The signature of a decoded lossy file: everything above the encoder's
    lowpass is simply gone."""
    y = _brickwall(_noise(), RATE, 16000)
    r = bandwidth.analyze([y], RATE)
    assert r["verdict"] == "brick-wall"
    assert 15000 < r["wall_hz"] < 17500, r["wall_hz"]
    assert r["wall_db"] >= 40


@pytest.mark.parametrize("cutoff", [11025, 16000, 19000, 20500])
def test_wall_found_at_each_common_encoder_cutoff(cutoff):
    r = bandwidth.analyze([_brickwall(_noise(), RATE, cutoff)], RATE)
    assert r["verdict"] == "brick-wall", f"missed a wall at {cutoff} Hz"
    assert abs(r["wall_hz"] - cutoff) < cutoff * 0.12


def test_full_band_audio_is_not_flagged():
    r = bandwidth.analyze([_noise()], RATE)
    assert r["verdict"] == "no-wall"


def test_gentle_rolloff_is_not_a_codec_wall():
    """The critical false positive. A dark or filtered sample is missing its
    top octave too -- if that read as "transcoded", the check would be useless
    on the material a producer owns most of."""
    y = _gentle_lowpass(_noise(), RATE, 6000)
    r = bandwidth.analyze([y], RATE)
    assert r["verdict"] == "no-wall", f"filtering misread as a codec ({r['detail']})"


def test_pure_tone_is_not_a_codec_wall():
    """A sine has almost no energy anywhere else, which is a spectrum-shaped
    trap for anything comparing against a global level."""
    assert bandwidth.analyze([_tone()], RATE)["verdict"] == "no-wall"


def test_tilted_spectrum_does_not_fake_an_edge():
    """Pink-weighted material slopes continuously. An earlier implementation
    compared against a global reference and put the "edge" at 5 kHz on tilt
    alone, missing real walls on exactly this material."""
    y = _noise()
    spec = np.fft.rfft(y)
    freqs = np.fft.rfftfreq(len(y), 1.0 / RATE)
    spec[1:] /= np.sqrt(freqs[1:])                 # pink tilt
    pink = np.fft.irfft(spec, n=len(y))
    assert bandwidth.analyze([pink], RATE)["verdict"] == "no-wall"
    walled = _brickwall(pink, RATE, 16000)
    assert bandwidth.analyze([walled], RATE)["verdict"] == "brick-wall", \
        "a real wall was hidden by spectral tilt"


def test_quiet_audio_is_judged_the_same_as_loud():
    """Level must not change the verdict -- the measure is a ratio."""
    y = _brickwall(_noise(), RATE, 16000)
    loud = bandwidth.analyze([y], RATE)
    quiet = bandwidth.analyze([y * 0.001], RATE)
    assert loud["verdict"] == quiet["verdict"] == "brick-wall"


def test_resampled_file_is_not_flagged():
    """A resampler's antialias filter lands just under Nyquist. Judging by
    cutoff frequency alone would call this a codec wall: it sits at ~94% of
    Nyquist while a 128 kbps MP3 sits at ~91%."""
    y = _noise(RATE * DUR)[::2]                    # 22050 Hz content
    assert bandwidth.analyze([y], 22050)["verdict"] == "no-wall"


def test_silence_and_short_input_return_none():
    assert bandwidth.analyze([np.zeros(RATE)], RATE) is None
    assert bandwidth.analyze([_noise(64)], RATE) is None


def test_verdict_survives_a_different_transform_size():
    """Checked on 392 real library files with zero flips; pinned here so a
    parameter change cannot quietly turn the verdict into an artefact."""
    y = _brickwall(_noise(), RATE, 16000)
    first = bandwidth.analyze([y], RATE)["verdict"]
    original = bandwidth._FFT
    try:
        bandwidth._FFT = 4096
        bandwidth._MIN_SAMPLES = 4096 * 2
        assert bandwidth.analyze([y], RATE)["verdict"] == first
    finally:
        bandwidth._FFT = original
        bandwidth._MIN_SAMPLES = original * 2


# ----------------------------------------------------------------- channels

def test_bit_identical_channels_are_dual_mono():
    y = _noise()
    r = channels.analyze([y, y.copy()])
    assert r["verdict"] == "dual-mono"
    assert "twice" in r["detail"]


def test_near_mono_is_separated_from_true_stereo():
    """Not bit-identical, but the difference is inaudible -- worth saying so
    without claiming the file is literally duplicated."""
    y = _noise()
    r = channels.analyze([y, y + np.random.default_rng(1).standard_normal(len(y)) * 1e-7])
    assert r["verdict"] == "near-mono"
    assert r["correlation"] > 0.999


def test_real_stereo_is_left_alone():
    rng = np.random.default_rng(3)
    r = channels.analyze([rng.standard_normal(RATE) * 0.2,
                          rng.standard_normal(RATE) * 0.2])
    assert r["verdict"] == "stereo"
    assert abs(r["correlation"]) < 0.1


def test_mono_and_multichannel_are_not_judged():
    y = _noise(RATE)
    assert channels.analyze([y]) is None
    assert channels.analyze([y, y, y]) is None



def test_audit_reports_matching_channels_without_failing_the_file(tmp_path):
    """A stereo file whose channels match is legal: audit --signal names it
    and exits 0 (it counted as an integrity mismatch, exit 1)."""
    import struct
    import subprocess
    import sys
    pytest.importorskip("numpy")
    mono = _tone(n=RATE)
    pcm = b"".join(struct.pack("<hh", int(s * 16000), int(s * 16000)) for s in mono)
    fmt = struct.pack("<HHIIHH", 1, 2, RATE, RATE * 4, 4, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    p = tmp_path / "twin.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    r = subprocess.run([sys.executable, "-m", "acidcat", "audit", "--signal", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NOTE        dual-mono" in r.stdout
    assert "mismatch" not in r.stdout
