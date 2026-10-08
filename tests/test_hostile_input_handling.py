"""Malformed input must produce a message, not a traceback.

Every case here was demonstrated by the adversarial and RE audits. They share a
shape: a file that other verbs handle cleanly reaches ONE verb that never
guarded the library call underneath it, and the user gets a stack trace from
inside mutagen or the interpreter. For a tool whose whole job is pointing at
untrusted bytes, that is the wrong answer.
"""

import os
import struct

import pytest

from acidcat.cli import main


def _wav(n_frames=500):
    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", n_frames * 2) + bytes(n_frames * 2))
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def _nested_lists(depth):
    """A tiny file that is deeply nested -- 600 levels fits in about 7 KB."""
    inner = b"LIST" + struct.pack("<I", 4) + b"INFO"
    for _ in range(depth):
        inner = b"LIST" + struct.pack("<I", len(inner) + 4) + b"INFO" + inner
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16) + inner)
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ------------------------------------------------------------ carve safety

def test_carve_refuses_to_overwrite_its_own_input(tmp_path, capsys):
    """`carve --help` promises "File to carve from (never modified)". With -o
    pointing back at the target it truncated the file and reported success: a
    2,044-byte WAV became 4 bytes, exit 0, no backup."""
    p = tmp_path / "self.wav"
    p.write_bytes(_wav())
    before = p.read_bytes()

    rc = main(["carve", str(p), "--offset", "0", "--length", "4", "-o", str(p)])
    assert rc == 2, "carve overwrote the file it was reading"
    assert p.read_bytes() == before
    assert "output is the input" in capsys.readouterr().err


def test_carve_to_a_different_file_still_works(tmp_path):
    src = tmp_path / "a.wav"
    src.write_bytes(_wav())
    out = tmp_path / "b.bin"
    assert main(["carve", str(src), "--offset", "0", "--length", "4",
                 "-o", str(out)]) == 0
    assert out.read_bytes() == b"RIFF"
    assert src.read_bytes() == _wav()


# --------------------------------------------------- unbounded recursion

def test_deep_nesting_does_not_exhaust_the_stack(tmp_path, capsys):
    """600 nested LIST chunks in ~7 KB took validate/audit down with a
    RecursionError. In a sweep over a corpus that kills the whole run at the
    first such file."""
    p = tmp_path / "nest.wav"
    p.write_bytes(_nested_lists(600))
    for verb in ("validate", "audit", "repair"):
        rc = main([verb, str(p)])
        out = capsys.readouterr()
        assert "RecursionError" not in (out.out + out.err), f"{verb} recursed"
        assert rc in (0, 1, 2), f"{verb} returned {rc}"


def test_a_sweep_survives_a_hostile_file(tmp_path, capsys):
    """The point of the fix: one bad file must not stop the batch."""
    (tmp_path / "bad.wav").write_bytes(_nested_lists(600))
    (tmp_path / "good.wav").write_bytes(_wav())
    rc = main(["validate", str(tmp_path)])
    out = capsys.readouterr()
    assert "RecursionError" not in (out.out + out.err)
    assert rc in (0, 1)
    assert "good.wav" in out.out or "2 file" in out.out


def test_normal_nesting_is_unaffected(tmp_path):
    """Real IFF nests a few deep; the cap must not touch it."""
    p = tmp_path / "ok.wav"
    p.write_bytes(_nested_lists(3))
    assert main(["validate", str(p)]) in (0, 1)


# --------------------------------------------- library calls that escaped

@pytest.mark.parametrize("name,data,want", [
    ("nocomm.aiff", b"FORM" + struct.pack(">I", 16) + b"AIFF"
                    + b"SSND" + struct.pack(">I", 8) + bytes(8), 0),
    # a file the tag library cannot read has no editor that can run: 2
    ("magic.flac", b"fLaC", 2),
    ("allsync.mp3", b"\xff" * 200, 2),
])
def test_write_reports_instead_of_leaking_a_mutagen_traceback(tmp_path, capsys,
                                                              name, data, want):
    """15 malformed specimens reached the user as raw mutagen tracebacks. Every
    other verb handles the same files cleanly; only the write path did not."""
    p = tmp_path / name
    p.write_bytes(data)
    rc = main(["write", "--set", "title=X", str(p)])
    captured = capsys.readouterr()
    assert "Traceback" not in (captured.out + captured.err)
    assert rc == want


def test_cover_reports_instead_of_leaking_a_mutagen_traceback(tmp_path, capsys):
    """mutagen signals "cannot read" as None for an unknown container and as an
    exception for one it recognized and choked on. Only the first was handled,
    so an AIFF with no COMM raised KeyError from inside mutagen."""
    p = tmp_path / "nocomm.aiff"
    p.write_bytes(b"FORM" + struct.pack(">I", 16) + b"AIFF"
                  + b"SSND" + struct.pack(">I", 8) + bytes(8))
    rc = main(["cover", str(p)])
    captured = capsys.readouterr()
    assert "Traceback" not in (captured.out + captured.err)
    assert rc in (0, 1)
