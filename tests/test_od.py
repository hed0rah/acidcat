"""Tests for `acidcat od` -- the annotated colored hex-dump view."""

from conftest import _make_riff_wav
from acidcat.commands import od


class _Args:
    def __init__(self, target, color="never", width=16):
        self.target = target
        self.color = color
        self.width = width


def test_od_renders_wav_structure(tmp_path, capsys):
    p = tmp_path / "a.wav"
    p.write_bytes(_make_riff_wav(channels=2))
    assert od.run(_Args(str(p))) == 0
    out = capsys.readouterr().out
    assert "RIFF/WAVE" in out
    assert "'fmt '" in out
    assert "format_tag" in out and "sample_rate" in out
    # fmt payload_base = 0x0c + 8 = 0x14; format_tag bytes sit there
    assert "0x00000014" in out
    assert "02 00" in out          # channels = 2, little-endian


def test_od_no_color_emits_no_ansi(tmp_path, capsys):
    p = tmp_path / "a.wav"
    p.write_bytes(_make_riff_wav())
    od.run(_Args(str(p), color="never"))
    assert "\033[" not in capsys.readouterr().out


def test_od_color_always_emits_ansi(tmp_path, capsys):
    p = tmp_path / "a.wav"
    p.write_bytes(_make_riff_wav())
    od.run(_Args(str(p), color="always"))
    assert "\033[" in capsys.readouterr().out


def test_od_unsupported_returns_2(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("not audio")
    assert od.run(_Args(str(p))) == 2
