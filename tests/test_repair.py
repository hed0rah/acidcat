"""The `acidcat repair` command: fixes stale container sizes in place / to a copy,
guards the audio, and is a no-op on a clean file."""
import struct
from types import SimpleNamespace

from acidcat.commands import repair


def _args(inputs, output=None, dry_run=False, overwrite=False, keep_pad=False):
    return SimpleNamespace(inputs=inputs, output=output, dry_run=dry_run,
                           overwrite=overwrite, keep_pad=keep_pad)


def _wav(payload=b"\x00" * 64):
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    data = b"data" + struct.pack("<I", len(payload)) + payload
    body = b"WAVE" + fmt + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_repair_noop_on_clean_file(tmp_path, capsys):
    p = tmp_path / "clean.wav"
    p.write_bytes(_wav())
    rc = repair.run(_args([str(p)]))
    assert rc == 0
    assert "already consistent" in capsys.readouterr().out


def test_repair_fixes_in_place_with_backup(tmp_path):
    good = _wav(b"\x11" * 100)
    broken = bytearray(good)
    struct.pack_into("<I", broken, 4, 5)          # stale master size
    p = tmp_path / "broken.wav"
    p.write_bytes(bytes(broken))
    rc = repair.run(_args([str(p)]))
    assert rc == 0
    assert p.read_bytes() == good                 # repaired to the correct bytes
    assert (tmp_path / "broken_original.wav").read_bytes() == bytes(broken)  # backup


def test_repair_to_output_copy_leaves_input(tmp_path):
    good = _wav(b"\x22" * 40)
    broken = bytearray(good)
    struct.pack_into("<I", broken, 4, 9)
    src = tmp_path / "in.wav"
    src.write_bytes(bytes(broken))
    out = tmp_path / "out.wav"
    rc = repair.run(_args([str(src)], output=str(out)))
    assert rc == 0
    assert src.read_bytes() == bytes(broken)      # input untouched
    assert out.read_bytes() == good


def test_repair_dry_run_writes_nothing(tmp_path, capsys):
    good = _wav()
    broken = bytearray(good)
    struct.pack_into("<I", broken, 4, 1)
    p = tmp_path / "b.wav"
    p.write_bytes(bytes(broken))
    rc = repair.run(_args([str(p)], dry_run=True))
    assert rc == 0
    assert p.read_bytes() == bytes(broken)        # unchanged on disk
    assert "size:" in capsys.readouterr().out


def test_repair_rejects_non_iff(tmp_path, capsys):
    p = tmp_path / "x.bin"
    p.write_bytes(b"ID3\x04not a container")
    rc = repair.run(_args([str(p)]))
    assert rc == 1
    assert "not a RIFF/AIFF/MP4 container" in capsys.readouterr().err
