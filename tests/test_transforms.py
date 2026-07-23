"""Tests for the transform lens (core/transforms.py) -- finding audio hidden
under a reversible byte transform (XOR / bit-rotate / nibble-swap).

Deterministic: audio is a synthesized tone (the shape the detector recognises),
noise is a seeded PRNG. XOR with 0x33 genuinely hides this tone (its score drops
from ~0.9 to ~0.07), so it is a real round-trip for the lens.
"""

import math

from acidcat.core import transforms


def _tone(n, period=40, amp=60):
    """A signed-8-bit sine rendered to unsigned bytes -- a smooth, pitched blob."""
    return bytes(int(amp * math.sin(2 * math.pi * i / period)) & 0xFF for i in range(n))


def _noise(n, seed=1):
    s, out = seed, bytearray()
    for _ in range(n):
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        out.append((s >> 16) & 0xFF)
    return bytes(out)


def test_roughness_lower_for_smooth_than_noise():
    assert transforms._roughness(_tone(2048)) < transforms._roughness(_noise(2048))


def test_refine_xor_recovers_true_key_neighbourhood():
    seg = bytes(b ^ 0x5A for b in _tone(8000))
    got = transforms._refine_key(seg, "xor")
    # true key or its polarity twin (K ^ 0xFF), within a few low bits either way
    assert min(got ^ 0x5A, got ^ 0x5A ^ 0xFF) <= 6


def test_finds_xor_hidden_audio_region():
    blob = _noise(4096) + bytes(b ^ 0x33 for b in _tone(12000)) + _noise(4096)
    hits = transforms.find_transformed_audio(blob)
    assert hits, "expected the XOR'd region to be located"
    h = hits[0]
    assert h["kind"] == "transformed"
    assert h["transform"].startswith("xor:")
    assert 0x1000 - 2048 <= h["offset"] <= 0x1000 + 4096   # near the buried region


def test_finds_nibble_swapped_audio():
    swapped = transforms._apply(_tone(12000), "nibble", 0)
    blob = _noise(2048) + swapped + _noise(2048)
    hits = transforms.find_transformed_audio(blob)
    assert any(h["transform"] == "nibble-swap" for h in hits)


def test_no_false_positive_on_noise():
    assert transforms.find_transformed_audio(_noise(200000)) == []


def test_no_false_positive_on_plain_audio():
    # already audio -> found by the plain detector, not the transform lens
    blob = _noise(2048) + _tone(12000) + _noise(2048)
    assert transforms.find_transformed_audio(blob) == []


def test_read_cap_bounds_the_scan():
    blob = _noise(4096) + bytes(b ^ 0x33 for b in _tone(12000)) + _noise(4096)
    # cap below the buried region -> nothing scanned there
    assert transforms.find_transformed_audio(blob, read_cap=1024) == []
