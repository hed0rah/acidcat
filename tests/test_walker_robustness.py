"""Systemic guard for the truncated-header crash class the external audit found.

Every magic-sniffed walker must DEGRADE on a file whose magic matches but whose
header is truncated -- return a (possibly partial) result or raise Unsupported,
never an unexpected struct.error / IndexError / TypeError. walk_file has no
top-level exception boundary, so a raise here is a hard crash of acidcat audit /
info / od on untrusted input."""
import pytest

from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported

# (filename, leading bytes): the magic matches, the rest of the header is absent.
_MAGICS = [
    ("t.wav", b"RIFF\x00\x00\x00\x00WAVE"),
    ("t.sf2", b"RIFF\x00\x00\x00\x00sfbk"),
    ("t.rmi", b"RIFF\x00\x00\x00\x00RMID"),
    ("t.akp", b"RIFF\x00\x00\x00\x00APRG"),
    ("t.aif", b"FORM\x00\x00\x00\x00AIFF"),
    ("t.aifc", b"FORM\x00\x00\x00\x00AIFC"),
    ("t.e4b", b"FORM\x00\x00\x00\x00E4B0"),
    ("t.exb", b"FORM\x00\x00\x00\x00E5B0"),
    ("t.mid", b"MThd\x00\x00\x00\x06\x00\x01"),
    ("t.rf64", b"RF64\x00\x00\x00\x00WAVE"),
    ("t.flac", b"fLaC"),
    ("t.ogg", b"OggS"),
    ("t.xm", b"Extended Module: "),
    ("t.it", b"IMPM"),
    ("t.wt", b"vawt"),
    ("t.bwpreset", b"BtWg"),
    ("t.fxp", b"CcnK"),
    ("t.serum", b"XferJson"),
    ("t.s3m", b"\x00" * 0x1C + b"\x1a\x10" + b"\x00" * 14 + b"SCRM"),
    ("t.pgm", b"\x04\x2a\x00\x00MPC1000 PGM 1.00"),
    ("t.krz", b"PRAM"),
    ("t.srom.krz", b"SROM"),
]


@pytest.mark.parametrize("fn,magic", _MAGICS, ids=[m[0] for m in _MAGICS])
def test_truncated_magic_never_unexpectedly_raises(tmp_path, fn, magic):
    p = tmp_path / fn
    p.write_bytes(magic + b"\x00" * 8)          # magic then a truncated stub
    try:
        walk_file(str(p))                        # may return a degraded result
    except Unsupported:
        pass                                     # a clean "cannot decode" is fine
    # any other exception propagates and fails the test


def test_truncated_at_every_length_of_a_wav(tmp_path):
    """Truncating a real (hermetic) WAV at every prefix length degrades, never
    raises -- a mini differential-fuzz pass on the busiest walker."""
    from conftest import _make_riff_wav
    full = _make_riff_wav(channels=2)
    p = tmp_path / "trunc.wav"
    for n in range(len(full) + 1):
        p.write_bytes(full[:n])
        try:
            walk_file(str(p))
        except Unsupported:
            pass
