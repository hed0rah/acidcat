"""Tests for `acidcat shape` -- the structural-fingerprint scanner."""

from conftest import _make_riff_wav
from acidcat.commands import shape


class _Args:
    def __init__(self, targets, no_path=False, coarse=False, warn_only=False):
        self.targets = targets
        self.no_path = no_path
        self.coarse = coarse
        self.warn_only = warn_only


def test_fingerprint_wav(minimal_wav):
    label, summary, ids, warned = shape._fingerprint(minimal_wav)
    assert label == "RIFF/WAVE"
    assert "PCM" in summary
    assert ids == "data,fmt"
    assert warned is False


def test_fingerprint_skips_non_audio(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("not an audio file")
    assert shape._fingerprint(str(p)) is None


def test_shape_run_lists_only_decodable(tmp_path, capsys):
    (tmp_path / "a.wav").write_bytes(_make_riff_wav())
    (tmp_path / "readme.txt").write_text("x")
    shape.run(_Args([str(tmp_path)]))
    lines = capsys.readouterr().out.splitlines()   # no strip: preserves trailing tabs
    assert len(lines) == 1
    assert lines[0].startswith("RIFF/WAVE\t")
    assert lines[0].endswith("a.wav")


def test_shape_no_path_and_coarse_collapse(tmp_path, capsys):
    # two WAVs of different bit depth: distinct with the summary, identical coarse
    (tmp_path / "a.wav").write_bytes(_make_riff_wav(bits=16))
    (tmp_path / "b.wav").write_bytes(_make_riff_wav(bits=24))
    shape.run(_Args([str(tmp_path)], no_path=True))
    assert len(set(capsys.readouterr().out.splitlines())) == 2
    shape.run(_Args([str(tmp_path)], no_path=True, coarse=True))
    assert len(set(capsys.readouterr().out.splitlines())) == 1
