"""`locate` must find raw 16-bit PCM, not just 8-bit.

The README sells the statistical engine as finding "signatureless raw PCM".
A fresh-eyes audit pointed it at 600,000 bytes of ordinary 16-bit stereo audio
carved out of a real WAV and got:

    located 0 region(s): 0 container(s), 0 stream(s), 0 blob(s)

The cause was not a threshold. The features read a window as 8-bit signed PCM,
and in 16-bit audio the low byte of each sample is close to noise, so
consecutive bytes alternate noisy/smooth and the lag-1 correlation the detector
keys on is destroyed. Measured on that same buffer:

    read as bytes      r1 = +0.001   r2 = +0.051
    read as int16      r1 = +0.572   r2 = +0.998

scan() now reads each window three ways and keeps the best. These tests pin
that 16-bit is found, that random data still is not, and that the 8-bit path
did not regress.
"""

import math
import struct

import pytest

from acidcat.core.forensics import audioscan


def _pcm16(n_frames, channels=2, period=180, amp=9000, endian="<"):
    """Music-ish 16-bit PCM: a tone with a slow envelope, so it is smooth in
    the sample domain and noisy in the low byte -- the case that broke."""
    out = bytearray()
    for i in range(n_frames):
        env = 0.4 + 0.6 * abs(math.sin(i / 5000.0))
        v = int(amp * env * math.sin(2 * math.pi * i / period))
        for _ in range(channels):
            out += struct.pack(endian + "h", v)
    return bytes(out)


def _pcm8(n, period=64, amp=90, dc=128):
    return bytes((dc + int(amp * math.sin(2 * math.pi * i / period))) & 0xFF
                 for i in range(n))


def _noise(n, seed=11):
    out, x = bytearray(), seed
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        out.append((x >> 16) & 0xFF)
    return bytes(out)


def test_bare_16bit_pcm_is_found():
    """The headline claim, and the exact case that returned zero regions."""
    data = _pcm16(60_000)
    regions = audioscan.scan(data)
    assert regions, "16-bit PCM was invisible to the detector"
    covered = sum(r["length"] for r in regions)
    assert covered > len(data) * 0.8, (
        f"only {covered:,} of {len(data):,} bytes covered -- the detector is "
        f"finding edges rather than the body")


def test_the_winning_reading_is_reported():
    regions = audioscan.scan(_pcm16(60_000))
    assert regions[0]["evidence"]["view"] == "16bit-le"
    assert regions[0]["evidence"]["width"] == 2


def test_big_endian_16bit_is_found_too():
    regions = audioscan.scan(_pcm16(60_000, endian=">"))
    assert regions
    assert regions[0]["evidence"]["view"] == "16bit-be"


def test_8bit_detection_did_not_regress():
    """The pre-existing capability. Adding readings must not cost the old one."""
    blob = _noise(4096, seed=2) + _pcm8(8192) + _noise(4096, seed=3)
    regions = audioscan.scan(blob)
    assert len(regions) == 1
    assert regions[0]["confidence"] > 0.5


def test_random_data_is_still_rejected_under_every_reading():
    """Three chances to be wrong instead of one -- the false-positive risk the
    extra readings introduce."""
    assert audioscan.scan(_noise(200_000)) == []


def test_compressed_looking_data_is_still_rejected():
    import os
    assert audioscan.scan(os.urandom(200_000)) == []


def test_16bit_buried_in_noise_is_located_where_it_sits():
    lead = _noise(8192, seed=5)
    audio = _pcm16(20_000)
    blob = lead + audio + _noise(8192, seed=6)
    regions = audioscan.scan(blob)
    assert regions
    best = max(regions, key=lambda r: r["length"])
    assert best["start"] >= len(lead) - audioscan.DEFAULT_WINDOW
    assert best["end"] <= len(lead) + len(audio) + audioscan.DEFAULT_WINDOW


@pytest.mark.slow
def test_peak_memory_does_not_scale_with_input():
    """Regression: the first version sliced `data[offset::stride]` for the whole
    buffer once per reading, copying half the input twice -- +256 MB on a scan
    at the read cap. Decimation happens per block now."""
    import tracemalloc
    unit = _pcm16(20_000)

    def peak(buf):
        tracemalloc.start()
        try:
            audioscan.scan(buf)
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    # Force several blocks. At the shipping block size these inputs fit in one
    # block, so nothing is bounded and the measurement proves nothing -- the
    # same trap the bulk-path memory test fell into.
    original = audioscan._VIEW_BLOCK
    try:
        audioscan._VIEW_BLOCK = 32
        small = peak(unit)
        large = peak(unit * 4)
    finally:
        audioscan._VIEW_BLOCK = original
    assert large < small * 2.0, (
        f"peak allocation tracked input size ({small} -> {large} for 4x the "
        f"bytes); a reading is copying the whole buffer")


def test_every_reading_agrees_between_the_bulk_and_scalar_paths():
    """The numpy path and the pure-Python path must not disagree about which
    reading wins."""
    data = _pcm16(40_000)
    fast = audioscan.scan(data)
    original = audioscan._bulk_features
    audioscan._bulk_features = lambda *a, **k: None
    try:
        slow = audioscan.scan(data)
    finally:
        audioscan._bulk_features = original
    assert [(r["start"], r["end"]) for r in fast] == \
           [(r["start"], r["end"]) for r in slow]
    assert [r["evidence"]["view"] for r in fast] == \
           [r["evidence"]["view"] for r in slow]
