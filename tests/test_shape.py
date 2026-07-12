"""Tests for `acidcat shape` -- the structural-fingerprint scanner."""

from conftest import _make_riff_wav
from acidcat.commands import shape


class _Args:
    def __init__(self, targets, no_path=False, coarse=False, warn_only=False,
                 fast=False, anomalies=False, fmt_filter=None):
        self.targets = targets
        self.no_path = no_path
        self.coarse = coarse
        self.warn_only = warn_only
        self.fast = fast
        self.anomalies = anomalies
        self.fmt_filter = fmt_filter


def test_fingerprint_wav(minimal_wav):
    label, summary, ids, flag = shape._full_fingerprint(minimal_wav, False)
    assert label == "RIFF/WAVE"
    assert "PCM" in summary
    assert ids == "data,fmt"
    assert flag == ""


def test_fingerprint_skips_non_audio(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("not an audio file")
    assert shape._full_fingerprint(str(p), False) is None


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


def test_fast_fingerprint_wav(minimal_wav):
    # header-only: format + chunk-set, no summary, no walk
    label, summary, ids, flag = shape._fast_fingerprint(minimal_wav)
    assert label == "RIFF/WAVE"
    assert summary == "" and flag == ""
    assert ids == "data,fmt"


def test_format_filter(tmp_path, capsys):
    (tmp_path / "a.wav").write_bytes(_make_riff_wav())
    shape.run(_Args([str(tmp_path)], fmt_filter="RIFF"))
    assert capsys.readouterr().out.strip()          # matches -> a line
    shape.run(_Args([str(tmp_path)], fmt_filter="MIDI"))
    assert capsys.readouterr().out.strip() == ""     # no match -> nothing


def test_anomalies_flag_names_trailing(tmp_path, capsys):
    # a WAV with bytes past its declared RIFF end -> a trailing_data anomaly
    (tmp_path / "t.wav").write_bytes(_make_riff_wav() + b"TRAILINGJUNK")
    shape.run(_Args([str(tmp_path)], anomalies=True))
    line = capsys.readouterr().out.strip()
    assert "trailing_data" in line.split("\t")[3]
