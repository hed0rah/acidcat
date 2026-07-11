"""The `acidcat validate` command: read-only constraint checking with an exit
code, over files and directory trees."""
import struct
from types import SimpleNamespace

from acidcat.commands import validate


def _args(inputs, quiet=False):
    return SimpleNamespace(inputs=inputs, quiet=quiet)


def _wav(payload=b"\x00" * 32):
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    data = b"data" + struct.pack("<I", len(payload)) + payload
    body = b"WAVE" + fmt + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_healthy_file_passes(tmp_path, capsys):
    p = tmp_path / "ok.wav"
    p.write_bytes(_wav())
    rc = validate.run(_args([str(p)]))
    assert rc == 0
    assert "OK" in capsys.readouterr().out


def test_broken_file_fails_with_exit_1(tmp_path, capsys):
    good = _wav(b"\x11" * 40)
    broken = bytearray(good)
    struct.pack_into("<I", broken, 4, 3)
    p = tmp_path / "bad.wav"
    p.write_bytes(bytes(broken))
    rc = validate.run(_args([str(p)]))
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "RIFF size" in out


def test_directory_walk_and_quiet(tmp_path, capsys):
    (tmp_path / "a.wav").write_bytes(_wav())
    bad = bytearray(_wav())
    struct.pack_into("<I", bad, 4, 1)
    (tmp_path / "b.wav").write_bytes(bytes(bad))
    rc = validate.run(_args([str(tmp_path)], quiet=True))
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL  b.wav" in out
    assert "OK" not in out                       # quiet hides the healthy one


def test_non_container_skipped(tmp_path, capsys):
    (tmp_path / "x.txt").write_bytes(b"not audio")
    # a directory with nothing modeled -> "no files to check", exit 0
    rc = validate.run(_args([str(tmp_path)]))
    assert rc == 0
