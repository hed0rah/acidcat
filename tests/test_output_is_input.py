"""An -o that names the input must refuse, not destroy it.

The report verbs open their -o before reading the input, so `audit f.wav -o
f.wav` replaced the audio with the report (od and stats exited 0 doing it), and
`inspect f.wav -o f.wav` left it at 0 bytes. carve's field path wrote the
field's bytes over the file it was carving from, exit 0. Every one of them
refuses now, with 2, and the input is untouched.
"""
import shutil
from pathlib import Path

import pytest

from acidcat.cli import main

SPEC = Path(__file__).parent / "specimens" / "final" / "wav" / "05_OCTAVE_1.wav"


@pytest.fixture
def wav(tmp_path):
    if not SPEC.exists():
        pytest.skip("specimen library not present")
    p = tmp_path / "in.wav"
    shutil.copyfile(SPEC, p)
    return p


@pytest.mark.parametrize("argv", [
    ["audit", "{f}", "-o", "{f}"],
    ["od", "{f}", "-o", "{f}"],
    ["classify", "{f}", "-o", "{f}"],
    ["inspect", "{f}", "-o", "{f}"],
    ["stats", "{f}", "-o", "{f}"],
    ["carve", "{f}", "RIFF/fmt_#sample_rate", "-o", "{f}"],
    ["carve", "{f}", "--field", "sample_rate", "-o", "{f}"],
    ["probe", "strings", "{f}", "-o", "{f}"],
])
def test_an_output_that_is_the_input_is_refused(wav, argv, capsys):
    before = wav.read_bytes()
    rc = main([a.format(f=wav) for a in argv])
    assert rc == 2
    assert wav.read_bytes() == before
    assert "output is the input" in capsys.readouterr().err


def test_edit_to_its_own_input_is_still_an_in_place_edit(wav):
    """edit -o naming the input is the documented in-place edit (atomic, with a
    backup), not a clobber, so the guard leaves it alone."""
    assert main(["edit", str(wav), "--set", "title=x", "-o", str(wav), "-q"]) == 0
    assert b"x" in wav.read_bytes()


def test_a_different_output_still_works(wav, tmp_path):
    out = tmp_path / "report.txt"
    assert main(["audit", str(wav), "-o", str(out)]) in (0, 1)
    assert out.stat().st_size > 0
