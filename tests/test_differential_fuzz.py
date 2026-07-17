"""Differential + round-trip fuzz over the strict (``structure``) and lenient
(``walk``) parsers -- the harness the strict/lenient split was built for but that
nothing previously exercised (external-audit technique gap).

For each seeded mutation of a hermetic IFF file, two contracts are checked on the
SAME bytes:
  - the **lenient walker** (`walk_file`) must degrade -- return a result or raise
    `Unsupported` -- never a stray struct.error / IndexError / TypeError.
  - the **strict parser** (`structure.parse`) must either round-trip byte-exactly
    (`emit(parse(m)) == m`, its bedrock invariant) or raise `StructError`; never a
    stray exception, and never a silent lossy round-trip.

Deterministic (seeded), hermetic (no external corpus), so it is CI-safe.
"""
import random

from conftest import _make_riff_wav

from acidcat.core import structure
from acidcat.core.structure import StructError
from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported


def _mutate(data, rng):
    b = bytearray(data)
    kind = rng.randint(0, 3)
    if kind == 0 and b:                          # flip one bit
        b[rng.randrange(len(b))] ^= 1 << rng.randrange(8)
    elif kind == 1 and len(b) > 4:               # truncate
        b = b[:rng.randrange(1, len(b))]
    elif kind == 2 and len(b) >= 8:              # scribble a size/offset field
        b[rng.randrange(4, min(len(b), 48))] = rng.randrange(256)
    else:                                        # append junk
        b += bytes(rng.randrange(256) for _ in range(rng.randrange(1, 24)))
    return bytes(b)


def test_structure_roundtrip_on_clean_seed():
    """The strict parser's bedrock invariant on a well-formed file."""
    wav = _make_riff_wav(channels=2, num_samples=16)
    assert structure.emit(structure.parse(wav)) == wav


def test_differential_fuzz_wav(tmp_path):
    rng = random.Random(20260717)
    seed = _make_riff_wav(channels=2, num_samples=16)
    p = tmp_path / "f.wav"
    for _ in range(1500):                        # seeded; a regression guard, not a soak
        m = _mutate(seed, rng)
        p.write_bytes(m)
        try:
            walk_file(str(p))                    # lenient: degrade, never crash
        except Unsupported:
            pass
        try:
            node = structure.parse(m)            # strict: exact or StructError
        except StructError:
            continue
        assert structure.emit(node) == m         # ...and if it parses, byte-exact
