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


def test_a_file_a_directory_walk_reads_is_an_input(wav, tmp_path, capsys):
    """`stats DIR -o DIR/x.wav` truncated x.wav before the walk read it."""
    before = wav.read_bytes()
    for verb in ("stats", "audit", "classify", "inspect"):
        assert main([verb, str(tmp_path), "-o", str(wav)]) == 2, verb
        assert wav.read_bytes() == before, verb
    # a report file the walk would not read is fine to overwrite
    old = tmp_path / "report.csv"
    old.write_text("old")
    assert main(["stats", str(tmp_path), "-o", str(old)]) in (0, 1)


def test_an_abbreviated_output_flag_is_not_taken_for_an_input(wav, tmp_path):
    """argparse reads --out as --output; its value is the output, so an old
    output file is overwritten, not refused as if it were an input."""
    old = tmp_path / "old.bin"
    old.write_bytes(b"old")
    assert main(["carve", str(wav), "@0+4", "--out", str(old)]) == 0
    assert old.read_bytes() == wav.read_bytes()[:4]


def test_edit_get_cover_to_its_own_input_is_refused(tmp_path):
    """--get writes what it reads out with a plain open, not the atomic
    in-place writer: -o naming the input replaced an MP3 with its JPEG."""
    src = SPEC.parent.parent / "mp3" / "12_2-12_Normal_End_SIESTA_DEMO.mp3"
    if not src.exists():
        pytest.skip("specimen library not present")
    p = tmp_path / "c.mp3"
    shutil.copyfile(src, p)
    before = p.read_bytes()
    assert main(["edit", str(p), "--get", "cover", "-o", str(p)]) == 2
    assert p.read_bytes() == before
