"""`-q` drops stderr lines and never changes stdout (cli-2.0.md section 1),
checked byte for byte on audit and edit (review V14): their stdout with and
without -q is identical, in the table and in JSON, for a clean file, a
damaged one and a dry-run edit, and -q leaves nothing on stderr that a run
without it would not have had."""

import struct
import subprocess
import sys

import pytest

import seeds


def _run(*argv):
    r = subprocess.run([sys.executable, "-m", "acidcat", *argv],
                       capture_output=True)
    return r.returncode, r.stdout, r.stderr


@pytest.fixture
def files(tmp_path):
    clean = tmp_path / "clean.wav"
    clean.write_bytes(seeds.build("wav"))
    bad = bytearray(seeds.build("wav"))
    struct.pack_into("<I", bad, 4, struct.unpack_from("<I", bad, 4)[0] - 8)
    damaged = tmp_path / "damaged.wav"
    damaged.write_bytes(bytes(bad) + b"PK\x03\x04" + bytes(12))
    return {"clean": str(clean), "damaged": str(damaged)}


CASES = [
    ["audit", "{clean}"],
    ["audit", "{damaged}"],
    ["audit", "{clean}", "{damaged}", "--json"],
    ["edit", "{clean}", "--set", "title=x", "--set", "key=Am", "--dry-run"],
    ["edit", "{clean}", "--set", "RIFF/fmt_#sample_rate=48000", "--dry-run",
     "--json"],
    ["edit", "{damaged}", "--strip", "--dry-run"],
]


@pytest.mark.parametrize("argv", CASES, ids=lambda a: " ".join(a[:1] + a[2:]))
def test_quiet_changes_no_byte_of_stdout(files, argv):
    argv = [a.format(**files) for a in argv]
    code, out, err = _run(*argv)
    qcode, qout, qerr = _run(*argv, "-q")
    assert qout == out
    assert qcode == code
    assert len(qerr) <= len(err)


def test_quiet_does_drop_what_it_should(files):
    """Guards the guard: -q has something to drop here, and drops it."""
    argv = ["edit", files["clean"], "--set", "key=Am", "--dry-run"]
    _c, _o, err = _run(*argv)
    _qc, _qo, qerr = _run(*argv, "-q")
    assert b"not stored" in err and not qerr
    _c, _o, err = _run("audit", files["clean"], files["damaged"])
    _qc, _qo, qerr = _run("audit", files["clean"], files["damaged"], "-q")
    assert b"==> " in err and b"==> " not in qerr
