"""The three stego embed methods, and the one honest claim each is allowed.

`replace` overwrites the low bit, `match` moves the whole sample by +/-1 to set
it, `adaptive` writes only where the carrier's low bits are already noisy. All
three round-trip. The interesting assertions are the ones that pin what each
method does and does NOT buy, measured against acidcat's own LSB detector rather
than asserted from the design:

    match      moves changed samples by exactly +/-1, never clobbering higher
               bits. That is the textbook counter to the value-histogram
               attacks (chi-square, sample-pair). It buys nothing against the
               windowed-entropy detector, which is the only LSB test that works
               on real 16-bit audio here -- and this file does not pretend it
               does.

    adaptive   leaves the windowed-entropy profile identical to the clean
               carrier, so the detector that fires on a naive fill does not
               fire on it. This IS a real evasion of that detector, and the
               test proves the naive fill is caught in the same breath.

The carrier is synthesized: a quiet, LSB-clean stretch followed by a loud, noisy
one, which is the shape adaptive exists to exploit. No corpus needed.
"""

import array
import io
import os
import wave

import pytest

stego = pytest.importorskip("acidcat_lab.stego", reason="acidcat_lab not installed")

from acidcat.core.forensics import lsb
from acidcat.core.walk import walk_file

_UNIT = b"the pattern always ends the same way "


def _wav(samples):
    a = array.array("h", samples)
    b = io.BytesIO()
    w = wave.open(b, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(44100)
    w.writeframes(a.tobytes())
    w.close()
    return b.getvalue()


def _samples(wav):
    off = wav.index(b"data") + 8
    a = array.array("h")
    a.frombytes(wav[off:off + (len(wav) - off) // 2 * 2])
    return a


def _mixed_carrier():
    """A quiet, LSB-clean head (low variance, low bit always 0) then a loud,
    noisy tail. Adaptive should pick the tail and leave the head alone."""
    import random
    rng = random.Random(20260907)
    quiet = [(i % 6) * 4 for i in range(12000)]
    noisy = [rng.randint(-6000, 6000) for _ in range(28000)]
    return _wav(quiet + noisy)


def _payload(n):
    return (_UNIT * (n // len(_UNIT) + 1))[:n]


def _analyse(tmp_path, blob):
    p = tmp_path / "s.wav"
    p.write_bytes(blob)
    label, chunks, _ = walk_file(str(p))
    return lsb.analyze(str(p), label, chunks) or {}


# ── round-trip: every method, whitened and raw ──────────────────────

@pytest.mark.parametrize("method", ["replace", "match", "adaptive"])
@pytest.mark.parametrize("raw", [False, True])
def test_round_trip(method, raw):
    carrier = _mixed_carrier()
    room = (stego.adaptive_capacity(carrier) if method == "adaptive"
            else stego.capacity(carrier))
    payload = _payload(min(room, 800))
    hidden = stego.embed(carrier, payload, raw=raw, method=method)
    assert stego.extract(hidden, raw=raw, method=method) == payload


def test_a_wrong_method_does_not_recover_the_payload():
    # adaptive reads a different set of samples than replace, so a mismatched
    # method fails the magic check rather than returning garbage.
    carrier = _mixed_carrier()
    hidden = stego.embed(carrier, _payload(400), method="adaptive")
    with pytest.raises(ValueError):
        stego.extract(hidden, method="replace")


# ── match: +/-1 only, and it survives the full-scale edge ───────────

def test_match_moves_changed_samples_by_exactly_one():
    carrier = _mixed_carrier()
    payload = _payload(600)
    before = _samples(carrier)
    after = _samples(stego.embed(carrier, payload, method="match"))
    deltas = [after[i] - before[i] for i in range(len(before)) if after[i] != before[i]]
    assert deltas, "match changed nothing"
    assert all(abs(d) == 1 for d in deltas), (
        "match moved a sample by more than 1: %r"
        % sorted({d for d in deltas if abs(d) != 1}))


def test_match_clamps_at_the_full_scale_edges():
    # a carrier pinned at both int16 rails: +/-1 must stay in range and still
    # round-trip. 32767 can only go down, -32768 can only go up.
    rails = ([32767] * 6000) + ([-32768] * 6000)
    carrier = _wav(rails)
    payload = _payload(min(stego.capacity(carrier), 400))
    hidden = stego.embed(carrier, payload, method="match")
    a = _samples(hidden)
    assert min(a) >= -32768 and max(a) <= 32767
    assert stego.extract(hidden, method="match") == payload


# ── adaptive: evades the entropy detector; naive does not ───────────

def test_adaptive_leaves_the_entropy_profile_alone_and_naive_lights_it_up(tmp_path):
    """The mechanism, not the field rate. This carrier's quiet head is LSB-clean
    BY CONSTRUCTION (low bit forced to 0). Real quiet audio is usually dithered,
    so its low bits are already random; on such a carrier the detector reads
    uniformly high on the clean file (see the module docstring: it fires on six
    of six clean WAVs), and a naive fill is not catchable there either. Adaptive
    beats naive only where the carrier has genuinely clean quiet stretches that a
    sequential fill would light up. How often real files have that is empirical
    and this test does not answer it -- scratchpad/stego_generalize.py measures
    the hit rate against a real corpus."""
    carrier = _mixed_carrier()
    payload = _payload(min(stego.adaptive_capacity(carrier), 1500))

    clean = _analyse(tmp_path, carrier)
    adaptive = _analyse(tmp_path, stego.embed(carrier, payload, method="adaptive"))
    naive = _analyse(tmp_path, stego.embed(carrier, payload, method="replace"))

    lit = lambda r: sum(1 for x in r["windows"] if x >= 0.9)

    # adaptive is indistinguishable from the untouched carrier at the detector
    assert adaptive["uniform_high"] == clean["uniform_high"]
    assert abs(adaptive["mean"] - clean["mean"]) < 0.02, (
        "adaptive shifted the mean LSB entropy (%.3f vs clean %.3f); the "
        "evasion this method claims no longer holds"
        % (adaptive["mean"], clean["mean"]))
    assert lit(adaptive) == lit(clean)

    # the same payload written naively fills the quiet head and is caught
    assert naive["mean"] > clean["mean"] + 0.1
    assert lit(naive) > lit(clean)


def test_adaptive_selection_is_deterministic_across_the_write():
    # the noisy-block choice is computed from bits the embed does not touch, so
    # the capacity the receiver would compute is unchanged after embedding.
    carrier = _mixed_carrier()
    before = stego.adaptive_capacity(carrier)
    hidden = stego.embed(carrier, _payload(min(before, 800)), method="adaptive")
    assert stego.adaptive_capacity(hidden) == before


# ── method guards ───────────────────────────────────────────────────

def test_unknown_method_is_rejected():
    with pytest.raises(ValueError):
        stego.embed(_mixed_carrier(), b"x", method="nope")


@pytest.mark.parametrize("bits", [8, 24])
def test_match_and_adaptive_are_16_bit_only(bits):
    width = bits // 8
    align = width
    n = 4000
    pcm = b"\x11" * (n * align)
    body = (b"WAVE"
            + b"fmt " + (16).to_bytes(4, "little")
            + b"\x01\x00\x01\x00" + (44100).to_bytes(4, "little")
            + (44100 * align).to_bytes(4, "little")
            + align.to_bytes(2, "little") + bits.to_bytes(2, "little")
            + b"data" + (len(pcm)).to_bytes(4, "little") + pcm)
    wav = b"RIFF" + (len(body)).to_bytes(4, "little") + body
    with pytest.raises(ValueError):
        stego.embed(wav, b"x", method="match")
    with pytest.raises(ValueError):
        stego.embed(wav, b"x", method="adaptive")
