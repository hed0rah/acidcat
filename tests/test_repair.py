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
    assert rc == 1                                # repairs are pending
    assert p.read_bytes() == bytes(broken)        # unchanged on disk
    assert "size:" in capsys.readouterr().out


def test_repair_rejects_non_iff(tmp_path, capsys):
    p = tmp_path / "x.bin"
    p.write_bytes(b"ID3\x04not a container")
    rc = repair.run(_args([str(p)]))
    assert rc == 2                     # nothing checkable, as validate says
    err = capsys.readouterr().err
    assert "not a format check models" in err and "check covers: 8svx" in err


def _box(btype, payload):
    return struct.pack(">I", 8 + len(payload)) + btype + payload


def _m4a_forged_stsz():
    """A single-track m4a whose stsz claims 1000 entries it does not hold:
    damage with no witness, so --fix has nothing safe to write."""
    stsz = _box(b"stsz", bytes(4) + struct.pack(">II", 0, 1000))
    stsc = _box(b"stsc", bytes(4) + struct.pack(">IIII", 1, 1, 1, 1))
    stco = _box(b"stco", bytes(4) + struct.pack(">II", 1, 1))
    tree = _box(b"moov", _box(b"trak", _box(b"mdia", _box(
        b"minf", _box(b"stbl", stsz + stsc + stco)))))
    return (_box(b"ftyp", b"M4A \x00\x00\x00\x00") + tree
            + _box(b"mdat", bytes(16)))


def test_fix_leaving_a_defect_exits_1_like_dry_run(tmp_path, capsys):
    """check --fix on a file whose defect has no witness wrote nothing and
    exited 0, its json row saying "clean", while --dry-run on the same file
    exited 1. `check --fix f && ship f` shipped the defect."""
    import json
    from acidcat.cli import main
    p = tmp_path / "hostile.m4a"
    original = _m4a_forged_stsz()
    p.write_bytes(original)
    assert main(["check", "--fix", "--dry-run", str(p)]) == 1
    capsys.readouterr()
    assert main(["check", "--fix", str(p)]) == 1
    assert "left unrepaired" in capsys.readouterr().err
    assert p.read_bytes() == original
    assert main(["check", "--fix", "--json", str(p)]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc[0]["action"] == "unrepaired"


def test_fix_exit_does_not_depend_on_file_order(tmp_path):
    """`rc = one() or rc` let a later 1 overwrite an earlier 2: a missing
    file then a damaged one exited 1, the reverse order 2."""
    missing = str(tmp_path / "missing.wav")
    broken = bytearray(_wav())
    struct.pack_into("<I", broken, 4, 1)
    bad = tmp_path / "bad.wav"
    bad.write_bytes(bytes(broken))
    for order in ([missing, str(bad)], [str(bad), missing]):
        assert repair.run(_args(order, dry_run=True)) == 2, order
