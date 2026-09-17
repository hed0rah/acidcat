"""The vectorized scan path must not have its own opinion about what is audio.

audioscan grew a numpy bulk path because a 32 MB image took ~16 s window by
window. Two implementations of a detector is normally how detectors start
disagreeing, so the split is deliberately narrow -- the bulk path computes
arithmetic, and audio_score / the gate / region merging stay single-source --
and these tests hold that line. The contract they enforce is the one a user
sees: the same file yields the same regions whether or not numpy is installed.
"""

import math
import struct

import pytest

from acidcat.core.forensics import audioscan as A

np = pytest.importorskip("numpy")


def _sine(n, period=64, amp=90, dc=128):
    return bytes((dc + int(amp * math.sin(2 * math.pi * i / period))) & 0xFF
                 for i in range(n))


def _noise(n, seed=1):
    out, x = bytearray(), seed
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        out.append((x >> 16) & 0xFF)
    return bytes(out)


def _pure(data, **kw):
    """scan() with the bulk path disabled."""
    orig = A._bulk_features
    A._bulk_features = lambda *a, **k: None
    try:
        return A.scan(data, **kw)
    finally:
        A._bulk_features = orig


CORPUS = {
    "sine": _sine(200_000),
    "noise": _noise(200_000),
    "sine_in_noise": _noise(60_000) + _sine(80_000) + _noise(60_000),
    "silence": bytes(200_000),
    "text": (b"the quick brown fox jumps over the lazy dog. " * 4600)[:200_000],
    "quiet_sine": _sine(200_000, amp=2),
    "sparse": (bytes(1000) + b"\xff") * 190,
    "float32": b"".join(struct.pack("<f", 0.7 * math.sin(i / 50.0))
                        for i in range(50_000)),
    "ramp": bytes(i & 0xFF for i in range(200_000)),
}


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_bulk_and_pure_agree_on_regions(name):
    """The whole point: identical output, not merely similar."""
    data = CORPUS[name]
    fast, slow = A.scan(data), _pure(data)
    assert [(r["start"], r["end"]) for r in fast] == \
           [(r["start"], r["end"]) for r in slow], f"{name}: region set differs"
    for a, b in zip(fast, slow):
        assert a["confidence"] == pytest.approx(b["confidence"], abs=1e-9)


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_bulk_and_pure_agree_per_window(name):
    """Region equality could hide feature drift that only bites on other data,
    so compare the features themselves too."""
    data = CORPUS[name]
    win, step = A.DEFAULT_WINDOW, A.DEFAULT_STEP
    count = (len(data) - win) // step + 1
    bulk = A._bulk_features(data, win, step, count)
    assert bulk is not None, "numpy present but bulk path declined"
    for i in range(0, count, 7):
        ref = A.window_features(data[i * step:i * step + win])
        got = bulk[i]
        assert set(got) == set(ref), f"{name}[{i}]: key mismatch"
        for k, v in ref.items():
            if k == "autocorr":
                for lag, r in v.items():
                    assert got[k][lag] == pytest.approx(r, abs=1e-9), \
                        f"{name}[{i}] lag {lag}"
            elif isinstance(v, float):
                assert got[k] == pytest.approx(v, abs=1e-9), f"{name}[{i}].{k}"
            else:
                assert got[k] == v, f"{name}[{i}].{k}"
        assert A.audio_score(got) == pytest.approx(A.audio_score(ref), abs=1e-9)


def test_float_windows_delegate_rather_than_reimplement():
    """The float probe is the fiddliest part of the feature vector, so the bulk
    path hands those windows back instead of carrying a second copy."""
    data = CORPUS["float32"]
    win, step = A.DEFAULT_WINDOW, A.DEFAULT_STEP
    count = (len(data) - win) // step + 1
    bulk = A._bulk_features(data, win, step, count)
    tagged = [f for f in bulk if f.get("float")]
    assert tagged, "no float regions detected -- probe never exercised"
    for f in tagged:
        assert f["float"] == (32, "<")


def test_falls_back_cleanly_without_numpy(monkeypatch):
    """Missing numpy must degrade to the slow path, not raise."""
    import builtins
    real = builtins.__import__

    def no_numpy(name, *a, **k):
        if name == "numpy" or name.startswith("numpy."):
            raise ImportError("numpy disabled for this test")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_numpy)
    assert A._bulk_features(CORPUS["sine"], 1024, 512, 100) is None
    assert A.scan(CORPUS["sine_in_noise"])       # still works, just slower


def test_entropy_matches_the_tabled_identity():
    """Both paths use H = log2(N) - (1/N)*sum(c*log2(c)); a drift here would
    move the entropy ceiling and silently change what gets scanned at all."""
    for name in ("noise", "sine", "text"):
        data = CORPUS[name]
        bulk = A._bulk_features(data, 1024, 512, 64)
        for i in (0, 17, 63):
            ref = A.window_features(data[i * 512:i * 512 + 1024])["entropy"]
            assert bulk[i]["entropy"] == pytest.approx(ref, abs=1e-9)
            assert bulk[i]["entropy"] >= 0.0


def test_short_input_skips_the_bulk_path():
    """Below the window count where vectorizing pays, the loop is used."""
    small = _sine(5000)
    assert A.scan(small) == _pure(small)


@pytest.mark.slow
def test_peak_memory_is_flat_in_input_size():
    """Regression: the vectorized path first held every window's arrays at once,
    which cost ~46x the input -- 1.4 GB for a 32 MB file, and an out-of-memory
    at the sizes locate is pointed at. Batching made it flat. Asserted as a
    ratio against a doubled input rather than an absolute, so the test does not
    encode this machine's numbers."""
    import tracemalloc
    data = _noise(400_000) + _sine(400_000)

    def peak(buf):
        tracemalloc.start()
        try:
            A.scan(buf)
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    original = (A._BULK_BATCH_BYTES, A._VIEW_BLOCK)
    try:
        # Small enough that the test input spans several of BOTH -- at the
        # shipping sizes these inputs fit in one batch and one view block, so
        # nothing is bounded and the measurement proves nothing. Forcing only
        # the batch left this on a knife edge: it passed locally and failed on
        # Python 3.10 at a ratio of 2.01 against a 2.0 threshold.
        A._BULK_BATCH_BYTES = 512 * 1024
        A._VIEW_BLOCK = 32
        small = peak(data)
        large = peak(data * 3)
    finally:
        A._BULK_BATCH_BYTES, A._VIEW_BLOCK = original
    # The retained feature dicts grow with input; the numpy arrays must not.
    #
    # The threshold is 2.5 against 3x the bytes, not 2.0. The regression this
    # catches was ~46x, so anything near the input ratio fails wide -- and an
    # unbounded loop would land at 3.0, not 2.1. A 2.0 line put a real ratio of
    # 2.01 on the wrong side of it, which failed CI on Python 3.10 while
    # proving nothing about the bug. The comment above recorded that as a known
    # knife edge and left the number where it was, so the flake kept firing on
    # runs that had nothing to do with allocation.
    #
    # A test that fails for reasons unrelated to its subject trains people to
    # ignore it, which costs more than the coverage is worth.
    assert large < small * 2.5, (
        f"peak allocation tracked input size ({small} -> {large} for 3x the "
        "bytes); the batch loop is not bounding numpy allocation")


def test_batching_does_not_change_the_answer():
    """Batch boundaries must be invisible: a window straddling one still gets
    the same features, and the region set is unchanged."""
    data = _noise(60_000) + _sine(120_000) + _noise(60_000)
    ref = A.scan(data)
    original = A._BULK_BATCH_BYTES
    try:
        for probe in (1, 3 * 1024, 64 * 1024, 1 << 30):   # 1 window/batch .. one batch
            A._BULK_BATCH_BYTES = probe
            got = A.scan(data)
            assert [(r["start"], r["end"]) for r in got] == \
                   [(r["start"], r["end"]) for r in ref], f"batch={probe}"
            for a, b in zip(got, ref):
                assert a["confidence"] == pytest.approx(b["confidence"], abs=1e-9)
    finally:
        A._BULK_BATCH_BYTES = original
