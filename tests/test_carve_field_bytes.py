"""`carve FILE FIELD -o PATH` writes the field's bytes (review V10).

It wrote the display text and a platform newline (`11025\\r\\n` on Windows),
and `--encoding raw` on a field was ignored. The value text stays the default
on stdout.
"""

import pytest

import seeds
from acidcat.cli import main

ADDR = "RIFF/fmt_#sample_rate"


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(seeds.build("wav"))
    return p


def test_output_is_the_bytes(wav, tmp_path):
    out = tmp_path / "f.bin"
    assert main(["carve", str(wav), ADDR, "-o", str(out)]) == 0
    assert out.read_bytes() == (44100).to_bytes(4, "little")


def test_stdout_is_the_value(wav, capsys):
    assert main(["carve", str(wav), ADDR]) == 0
    assert capsys.readouterr().out == "44100\n"


def test_the_encodings_format_the_bytes(wav, tmp_path, capsys):
    assert main(["carve", str(wav), ADDR, "--encoding", "hex"]) == 0
    assert capsys.readouterr().out == "44 ac 00 00\n"
    out = tmp_path / "v.txt"
    assert main(["carve", str(wav), ADDR, "--encoding", "value", "-o", str(out)]) == 0
    assert out.read_bytes() == b"44100\n"          # LF, on every platform


def test_raw_to_stdout_is_the_bytes(wav, capfdbinary):
    assert main(["carve", str(wav), ADDR, "--encoding", "raw"]) == 0
    assert capfdbinary.readouterr().out == (44100).to_bytes(4, "little")


def test_a_derived_value_has_no_bytes(tmp_path, capsys):
    p = tmp_path / "s.8svx"
    p.write_bytes(seeds.build("8svx"))
    out = tmp_path / "d.txt"
    assert main(["carve", str(p), "FORM/BODY#duration", "-o", str(out)]) == 0
    assert out.read_bytes() == b"0.008\n"
    assert main(["carve", str(p), "FORM/BODY#duration", "--encoding", "raw"]) == 2
    assert "no bytes" in capsys.readouterr().err


def test_a_glob_lists_values_and_refuses_byte_encodings(wav, capsys):
    assert main(["carve", str(wav), "RIFF/*#sample_rate"]) == 0
    assert capsys.readouterr().out == "44100\n"
    assert main(["carve", str(wav), "RIFF/*#sample_rate", "--encoding", "raw"]) == 2
