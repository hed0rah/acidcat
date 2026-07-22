"""Tests for the statistical audio-blob detector (core/audioscan.py).

Deterministic: noise from a seeded PRNG, audio from a synthesized tone, so the
class-separation the multi-lag experiment showed is pinned as a regression."""

import math
import random

from acidcat.core import audioscan


def _noise(n, seed=1):
    r = random.Random(seed)
    return bytes(r.getrandbits(8) for _ in range(n))


def _tone(n, period=40, amp=60, phase=0.0):
    """A signed-8-bit sine rendered to unsigned bytes (a smooth, pitched blob)."""
    out = bytearray()
    for i in range(n):
        s = int(amp * math.sin(2 * math.pi * (i + phase) / period))
        out.append(s & 0xFF)
    return bytes(out)


def _code_like(n):
    """Low-entropy structured bytes: a small alphabet with local repetition,
    the way program text / opcodes cluster. Autocorr decays monotonically."""
    r = random.Random(7)
    alphabet = bytes(range(0x20, 0x40))                # 32 values, ASCII-ish
    out = bytearray()
    while len(out) < n:
        run = r.randint(1, 4)
        ch = alphabet[r.randrange(len(alphabet))]
        out.extend([ch] * run)
    return bytes(out[:n])


def test_noise_scores_zero():
    feat = audioscan.window_features(_noise(1024))
    assert feat["peak"] < 0.15                          # flat autocorrelation
    assert audioscan.audio_score(feat) == 0.0


def test_tone_scores_high():
    feat = audioscan.window_features(_tone(1024))
    assert feat["peak"] > 0.8                           # strongly correlated
    assert audioscan.audio_score(feat) > 0.7


def test_constant_is_not_a_blob():
    # a run of one byte has zero entropy -- ambiguous, not flagged as audio
    feat = audioscan.window_features(b"\x00" * 1024)
    assert feat["entropy"] < audioscan._ENTROPY_FLOOR
    assert audioscan.audio_score(feat) == 0.0


def test_code_scores_below_tone():
    code = audioscan.audio_score(audioscan.window_features(_code_like(1024)))
    tone = audioscan.audio_score(audioscan.window_features(_tone(1024)))
    assert code < tone
    assert code < audioscan.DEFAULT_MIN_SCORE           # rejected at the default gate


def test_features_include_distribution():
    feat = audioscan.window_features(_tone(1024))
    assert "printable" in feat and "hist_tv" in feat
    assert 0.0 <= feat["printable"] <= 1.0


def test_distribution_gate_rejects_printable_ramp():
    # a smooth (high-autocorr) but fully-printable ASCII ramp -- structurally
    # "waveform-like" yet obviously text. The distribution gate must veto it,
    # the calibrated defense against the code/text false positives.
    win = bytes(0x41 + (i % 30) for i in range(1024))   # sawtooth in 'A'.. range
    feat = audioscan.window_features(win)
    assert feat["printable"] > 0.95                     # all printable
    assert feat["peak"] > 0.4                            # and highly correlated
    assert audioscan.audio_score(feat) == 0.0           # ... still rejected


def test_buried_tone_is_located():
    # noise | TONE | noise -> exactly one region, overlapping the planted tone
    a0, a1 = 4096, 8192
    blob = _noise(a0, seed=2) + _tone(a1 - a0) + _noise(4096, seed=3)
    regions = audioscan.scan(blob)
    assert len(regions) == 1
    reg = regions[0]
    # the region lands on the tone (allow a window of slop at each edge)
    assert reg["start"] >= a0 - audioscan.DEFAULT_WINDOW
    assert reg["end"] <= a1 + audioscan.DEFAULT_WINDOW
    assert reg["confidence"] > 0.5
    assert reg["evidence"]["width"] == 1


def test_two_tones_two_regions():
    gap = _noise(4096, seed=4)
    blob = gap + _tone(3072) + gap + _tone(3072, period=64) + gap
    regions = audioscan.scan(blob)
    assert len(regions) == 2
    assert regions[0]["end"] <= regions[1]["start"]


def test_scan_degrades_on_tiny_input():
    assert audioscan.scan(b"") == []
    assert audioscan.scan(b"\x01\x02\x03") == []        # shorter than a window


def test_region_evidence_present():
    blob = _noise(2048, seed=5) + _tone(4096) + _noise(2048, seed=6)
    reg = audioscan.scan(blob)[0]
    ev = reg["evidence"]
    assert set(ev["autocorr"]) == set(audioscan.LAGS)
    assert 0.0 <= ev["entropy"] <= 8.0
    assert reg["windows"] >= 1
