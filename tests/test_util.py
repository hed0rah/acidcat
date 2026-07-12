"""Coverage for the small util helpers on the CLI entry path:
stdin buffering (pipe support) and CSV filename slugging."""

import io
import os

from acidcat.util.stdin import is_stdin_target, stdin_to_tempfile
from acidcat.util.csv_helpers import safe_basename_for_csv


class _FakeStdin:
    """Minimal stand-in for sys.stdin: a tty flag plus a .buffer."""

    def __init__(self, data=b"", tty=False):
        self._tty = tty
        self.buffer = io.BytesIO(data)

    def isatty(self):
        return self._tty


# ── is_stdin_target ────────────────────────────────────────────────

def test_is_stdin_target_dash():
    assert is_stdin_target("-") is True


def test_is_stdin_target_path():
    assert is_stdin_target("song.wav") is False
    assert is_stdin_target("") is False
    assert is_stdin_target("--") is False


# ── stdin_to_tempfile ──────────────────────────────────────────────

def test_stdin_tempfile_returns_none_for_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin", _FakeStdin(tty=True))
    assert stdin_to_tempfile() is None


def test_stdin_tempfile_returns_none_for_empty_pipe(monkeypatch):
    monkeypatch.setattr("sys.stdin", _FakeStdin(b"", tty=False))
    assert stdin_to_tempfile() is None


def test_stdin_tempfile_buffers_piped_bytes(monkeypatch):
    payload = b"RIFF\x24\x00\x00\x00WAVEfmt "
    monkeypatch.setattr("sys.stdin", _FakeStdin(payload, tty=False))
    path = stdin_to_tempfile()
    try:
        assert path is not None
        assert path.endswith(".acidcat_stdin")
        with open(path, "rb") as f:
            assert f.read() == payload
    finally:
        if path:
            os.unlink(path)


# ── safe_basename_for_csv ──────────────────────────────────────────

def test_csv_name_adds_suffix():
    assert safe_basename_for_csv("scan") == "scan.csv"


def test_csv_name_keeps_existing_suffix():
    assert safe_basename_for_csv("report.csv") == "report.csv"
    # case-insensitive on the suffix check
    assert safe_basename_for_csv("REPORT.CSV") == "REPORT.CSV"


def test_csv_name_slugifies_only_basename():
    assert safe_basename_for_csv("my report!!.csv") == "my_report_.csv"


def test_csv_name_blank_basename_falls_back():
    # a name that slugs to empty (whitespace) hits the "output.csv" fallback
    assert safe_basename_for_csv(" ") == "output.csv"


def test_csv_name_empty_string_is_degenerate():
    # documents a quirk: normpath("") == ".", so base is "." not "" and the
    # fallback never fires -- the result is the odd but harmless "..csv"
    assert safe_basename_for_csv("") == "..csv"


def test_csv_name_preserves_dir_and_creates_it(tmp_path):
    target = os.path.join(str(tmp_path), "out", "sub", "a b.csv")
    result = safe_basename_for_csv(target)
    assert result.endswith(os.path.join("out", "sub", "a_b.csv"))
    assert os.path.isdir(os.path.dirname(result))  # parents created
