"""tests for the packed similarity vector and the standardized-cosine scoring
that find_similar uses. The scoring fix: raw features let 10^3-10^6 spectral
dims dominate the cosine and collapse every result to ~0.99; z-standardizing
per dimension restores a ranking that reflects timbre."""

import struct

import pytest

from acidcat.core import index as idx
from acidcat.core import features as feat
from acidcat.core import search


def test_pack_unpack_roundtrip():
    vec = [0.0, 1.5, -2.25, 1e6, 3.14159]
    blob = idx.pack_vector(vec)
    assert isinstance(blob, bytes) and len(blob) == len(vec) * 4
    back = idx.unpack_vector(blob)
    # float32 roundtrip, so compare with tolerance
    assert back is not None and len(back) == len(vec)
    for a, b in zip(vec, back):
        assert abs(a - b) <= abs(a) * 1e-6 + 1e-4


def test_pack_unpack_edges():
    assert idx.pack_vector([]) is None
    assert idx.pack_vector(None) is None
    assert idx.unpack_vector(None) is None
    assert idx.unpack_vector(b"") is None
    # dims mismatch is rejected (stale-length vector must be skipped, not scored)
    blob = idx.pack_vector([1.0, 2.0, 3.0])
    assert idx.unpack_vector(blob, dims=3) == pytest.approx([1.0, 2.0, 3.0])
    assert idx.unpack_vector(blob, dims=45) is None


def test_vector_from_features_shape_and_exclusions():
    # a full-ish dict plus the deliberately-excluded scale fields
    d = {k: 1.0 for k in feat.FEATURE_KEYS}
    d.update({"sample_rate": 44100, "audio_length_samples": 900000,
              "duration_sec": 3.2, "beat_count": 8})
    v = feat.vector_from_features(d)
    assert len(v) == feat.FEATURE_DIMS == len(feat.FEATURE_KEYS)
    assert all(x == 1.0 for x in v)          # only FEATURE_KEYS, none of the scale fields
    # NaN/inf/missing collapse to 0.0
    v2 = feat.vector_from_features({"spectral_centroid_mean": float("nan"),
                                    "rms_mean": float("inf")})
    assert all(x == 0.0 for x in v2)


def _spread(sims):
    return max(sims) - min(sims)


def test_standardized_cosine_ranks_by_timbre():
    """Target sits near the 'dark' candidate (spectral_centroid ~250) and far
    from the 'bright' one (~6000). After standardization the dark candidate must
    rank higher, and the scores must actually spread (the pre-fix raw cosine
    would have pinned both near 1.0)."""
    tgt = feat.vector_from_features(
        {"spectral_centroid_mean": 200.0, "rms_mean": 0.5, "zcr_mean": 0.02})
    dark = feat.vector_from_features(
        {"spectral_centroid_mean": 250.0, "rms_mean": 0.45, "zcr_mean": 0.03})
    bright = feat.vector_from_features(
        {"spectral_centroid_mean": 6000.0, "rms_mean": 0.1, "zcr_mean": 0.4})
    sims = search._standardized_cosine_py(tgt, [dark, bright])
    assert sims[0] > sims[1]                 # dark is more similar than bright
    assert _spread(sims) > 0.1               # real separation, not a 0.99 cluster


def test_standardized_cosine_numpy_matches_python():
    np = pytest.importorskip("numpy")        # skips where the analysis extra is absent
    import random
    rng = random.Random(1234)
    dims = feat.FEATURE_DIMS
    tgt = [rng.uniform(-5, 5000) for _ in range(dims)]
    cands = [[rng.uniform(-5, 5000) for _ in range(dims)] for _ in range(20)]
    py = search._standardized_cosine_py(tgt, cands)
    both = search._standardized_cosine(tgt, cands)   # numpy path (numpy present here)
    for a, b in zip(py, both):
        assert abs(a - float(b)) < 1e-9
