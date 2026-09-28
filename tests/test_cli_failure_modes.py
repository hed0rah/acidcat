"""Failures must not read as success.

Two demonstrated defects, both of which made a scripted run report success
while producing nothing:

  - `_is_closed_pipe` matched any OSError with EINVAL. On Windows an invalid
    output filename raises exactly that, so a failed `carve -o` was treated as
    "the reader went away", suppressed, and exited 0.
  - SUBCOMMANDS was a hand-maintained literal that had drifted; a directory
    named after a missing verb shadowed the command entirely.
"""

import errno
import os

import pytest

from acidcat.cli import SUBCOMMANDS, _build_parser, _is_closed_pipe, main


def test_a_failed_file_write_is_not_mistaken_for_a_closed_pipe():
    """The discriminator: a failed file operation carries .filename, a broken
    stdout does not."""
    named = OSError(errno.EINVAL, "Invalid argument", "out.bin")
    named.filename = "out.bin"
    assert not _is_closed_pipe(named)

    pipe = OSError(errno.EINVAL, "Invalid argument")
    assert _is_closed_pipe(pipe)
    assert _is_closed_pipe(BrokenPipeError())


def test_broken_pipe_is_still_quiet():
    assert _is_closed_pipe(OSError(errno.EPIPE, "Broken pipe"))
    win = OSError(errno.EINVAL, "pipe closing")
    win.winerror = 232
    assert _is_closed_pipe(win)


@pytest.mark.skipif(os.name != "nt", reason="invalid-filename errno is platform specific")
def test_unwritable_output_reports_failure(tmp_path, capsys):
    src = tmp_path / "a.bin"
    src.write_bytes(bytes(64))
    rc = main(["carve", str(src), "--offset", "0", "--length", "8",
               "-o", str(tmp_path / "q?w.bin")])
    assert rc == 1, "a write that produced no file exited as success"
    assert "acidcat carve:" in capsys.readouterr().err


def test_every_registered_verb_is_shadow_guarded():
    """SUBCOMMANDS is derived from the parser now. As a literal it had drifted:
    `census` and `wrap` were missing, so a directory of either name in the cwd
    shadowed the verb and ran `scan` on the directory instead."""
    parser = _build_parser()
    assert set(parser._sub.choices) == SUBCOMMANDS
    for expected in ("stats", "locate", "carve", "audit", "edit"):
        assert expected in SUBCOMMANDS
    # census is an alias of `stats --by chunks` in 2.0; the alias table guards it
    from acidcat.cli import _bare_path
    assert _bare_path(["census", "x"]) is None


def test_an_alias_verb_is_shadow_guarded_too(tmp_path, monkeypatch):
    """A 1.8 verb name is an alias through 2.x, so a directory called
    `validate` must not turn `acidcat validate` into `acidcat stats validate`."""
    from acidcat.cli import _bare_path
    (tmp_path / "validate").mkdir()
    monkeypatch.chdir(tmp_path)
    assert _bare_path(["validate", "x.wav"]) is None


def test_a_directory_named_after_a_verb_does_not_shadow_it(tmp_path, monkeypatch, capsys):
    (tmp_path / "wrap").mkdir()
    monkeypatch.chdir(tmp_path)
    # with the bug this ran `scan` on ./wrap and exited 0
    rc = main(["wrap", "--rate", "44100"])
    err = capsys.readouterr().err
    assert rc != 0 or "wrap" in err.lower()
