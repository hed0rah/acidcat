"""Every `acidcat edit` example in README.md and CHEATSHEET.md runs.

`write loop.wav --set bpm=128 --set key=Am` is in the cheatsheet, and it
exited 1 ("the patch does not verify") once the edit went through the verified
Patch: the acid writer reported the old key as None and the new one as the
string asked for, so the read-back could never match. Each example here runs
as a dry run on a generated WAV that carries an acid chunk, and must exit 0.

Syntax summaries (`FILE`, `field=value`, `[-o OUT]`) are not examples and are
left out; everything else on a line that starts with `acidcat edit` is run.
(The docs said `acidcat write` until 2.0 renamed the verb; `write` is an alias
of `edit`, so the examples are the same commands.)
"""

import pathlib
import re
import shlex
import struct

import pytest

from acidcat.cli import main

ROOT = pathlib.Path(__file__).resolve().parent.parent
_PLACEHOLDER = re.compile(r"\bFILE\b|\[|\bfield=value\b|\bk=v\b")


def _examples():
    out = []
    for doc in ("README.md", "CHEATSHEET.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        for line in text.splitlines():
            cmd = line.strip().strip("`")
            if not cmd.startswith("acidcat edit ") or _PLACEHOLDER.search(cmd):
                continue
            out.append(pytest.param(cmd, id=f"{doc}:{cmd[13:50]}"))
    return out


def _wav_with_acid():
    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", 8) + bytes(8)
            + b"acid" + struct.pack("<I", 24)
            + struct.pack("<IHHfIHHf", 0x02, 62, 0x8000, 0.0, 4, 4, 4, 120.0))
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def test_the_docs_have_write_examples():
    assert len(_examples()) >= 5


@pytest.mark.parametrize("cmd", _examples())
def test_a_documented_write_example_runs(cmd, tmp_path, capsys, monkeypatch):
    # the cover examples name an image beside the file
    monkeypatch.chdir(tmp_path)
    (tmp_path / "art.jpg").write_bytes(
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9")
    wav = tmp_path / "loop.wav"
    wav.write_bytes(_wav_with_acid())
    argv = shlex.split(cmd)[1:]
    # the file operands are every token between the verb and the first flag
    first_flag = next(i for i, a in enumerate(argv) if a.startswith("-"))
    argv = ["edit", str(wav)] + argv[first_flag:]
    if "--dry-run" not in argv:
        argv.append("--dry-run")
    rc = main(argv)
    err = capsys.readouterr().err
    assert rc == 0, f"{cmd!r} exited {rc}: {err}"
    assert wav.read_bytes() == _wav_with_acid(), "a dry run wrote the file"


def test_key_am_says_the_mode_is_not_stored(tmp_path, capsys):
    """The acid chunk holds a root note. key=Am stores A and says, once,
    that the minor is dropped; the report shows the pitch it holds."""
    wav = tmp_path / "loop.wav"
    wav.write_bytes(_wav_with_acid())
    assert main(["write", str(wav), "--set", "key=Am", "--dry-run"]) == 0
    got = capsys.readouterr()
    assert "key: 'D3' -> 'A3'" in got.out
    assert got.err.count("'minor' is not stored") == 1


def test_root_reads_back_in_the_note_domain(tmp_path, capsys):
    """root=C3 and root=60 are the same request; the smpl chunk holds 60."""
    wav = tmp_path / "loop.wav"
    wav.write_bytes(_wav_with_acid())
    for value in ("C3", "60"):
        assert main(["write", str(wav), "--set", f"root={value}", "--dry-run"]) == 0
        assert "root: None -> 'C3'" in capsys.readouterr().out


def test_clearing_the_key_reports_the_old_one(tmp_path, capsys):
    wav = tmp_path / "loop.wav"
    wav.write_bytes(_wav_with_acid())
    assert main(["write", str(wav), "--set", "key=", "--dry-run"]) == 0
    assert "key: 'D3' -> None" in capsys.readouterr().out
