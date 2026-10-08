"""`acidcat tui` must say why it cannot start, not just fail to start.

Reported from Git Bash: running it returned straight to the prompt with no
output and no error. Textual needs a real terminal to attach to, and MinTTY --
what Git Bash uses on Windows -- is a named pipe rather than a Windows console,
so `sys.stdout.isatty()` is False even though the window looks and behaves like
a terminal. Textual exits immediately, and a command that does nothing while
saying nothing is indistinguishable from a broken build.

That is the same defect this project keeps finding in its own output, in its
smallest possible form: silence where a reason belongs.
"""

import os
import sys

import pytest

from acidcat.commands import tui as tuicmd


@pytest.fixture(autouse=True)
def never_launch(monkeypatch):
    """No test in this file may start the real Textual app.

    Autouse rather than per-test: with the guard removed, any test that calls
    run() without this starts a full-screen UI on a terminal it cannot draw on
    and blocks forever, so the suite HANGS instead of failing. A mutation you
    cannot tell from an infinite loop teaches you nothing.
    """
    textual = pytest.importorskip("textual")           # noqa: F841
    import acidcat.tui_app as tui_app

    class _MustNotStart:
        def __init__(self, *a, **k):
            raise AssertionError(
                "started the UI on a terminal it cannot draw on")

    monkeypatch.setattr(tui_app, "AcidcatTUI", _MustNotStart, raising=False)


class _Args:
    def __init__(self, file=None):
        self.file = file


class TestTheDiagnosis:
    def test_a_real_terminal_is_not_diagnosed(self, monkeypatch):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        assert tuicmd._no_terminal() is None

    def test_git_bash_is_named_and_given_the_way_out(self, monkeypatch):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setenv("MSYSTEM", "MINGW64")
        why = tuicmd._no_terminal()
        assert why and "Git Bash" in why
        assert "winpty" in why, "named the problem without naming the fix"

    def test_it_says_the_rest_of_the_tool_still_works(self, monkeypatch):
        """The failure is scoped to the full-screen UI. Leaving that unsaid
        invites the reading that acidcat does not work here at all."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setenv("MSYSTEM", "MINGW64")
        assert "affects the full-screen UI only" in tuicmd._no_terminal()

    def test_a_plain_redirect_gets_a_plain_reason(self, monkeypatch):
        """Not everyone hitting this is on Git Bash; `acidcat tui > out.txt`
        lands here too and should not be told about MinTTY."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        monkeypatch.delenv("MSYSTEM", raising=False)
        why = tuicmd._no_terminal()
        assert why and "Git Bash" not in why
        assert "not one" in why


class TestTheExitCode:
    def test_it_exits_2_because_nothing_was_inspected(self, monkeypatch, tmp_path,
                                                      capsys):
        """0 it worked, 1 it ran and the answer is no, 2 it could not run.
        Returning 0 is what made this look like success."""
        pytest.importorskip("textual")
        p = tmp_path / "t.bin"
        p.write_bytes(b"\x00" * 64)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setenv("MSYSTEM", "MINGW64")

        assert tuicmd.run(_Args(str(p))) == 2
        assert "terminal" in capsys.readouterr().err

    def test_the_reason_goes_to_stderr(self, monkeypatch, tmp_path, capsys):
        """stdout is where a verb's answer goes. This is not an answer."""
        pytest.importorskip("textual")
        p = tmp_path / "t.bin"
        p.write_bytes(b"\x00" * 64)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        monkeypatch.delenv("MSYSTEM", raising=False)
        tuicmd.run(_Args(str(p)))
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err.strip()

    def test_a_missing_file_is_still_reported_first(self, monkeypatch, capsys):
        """Whichever is wrong, say the one the user can act on."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        # could-not-run is 2, and the message goes to stderr like every verb's
        assert tuicmd.run(_Args("no/such/file.wav")) == 2
        out = capsys.readouterr()
        assert out.out == "" and "No such file" in out.err
