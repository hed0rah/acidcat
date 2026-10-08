"""classify's verdict has to be actionable, not just readable.

Its whole design promise is "each verdict names the verb that follows, so an
unknown file is the start of a workflow rather than a dead end". But `--json`
gave `next: "locate"` -- a bare verb with no target -- so the one field whose
entire purpose is "what to run now" could not be run.

`path` is the other half, and it briefly regressed to a basename while stdin
support was being added, which would have made the record unusable from any
directory but the file's own.
"""

import json
import os
import struct
import subprocess
import sys

import pytest


def _wav(path):
    pcm = b"\x00\x01" * 128
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


@pytest.fixture
def lib(tmp_path):
    d = tmp_path / "has space & punct"      # the quoting case, deliberately
    d.mkdir()
    _wav(d / "a.wav")
    (d / "blob.bin").write_bytes(bytes(range(256)) * 8)
    return d


def _classify(target):
    r = subprocess.run([sys.executable, "-m", "acidcat", "classify",
                        str(target), "--json"], capture_output=True, text=True)
    return json.loads(r.stdout)


def test_path_is_the_real_path_not_a_basename(lib):
    for rec in _classify(lib):
        assert os.path.isabs(rec["path"]), rec
        assert os.path.exists(rec["path"]), rec


def test_next_command_is_runnable(lib):
    """Every command classify emits must resolve its target. Exit code is not
    asserted -- a verb may legitimately answer 1 -- but 'No such file' means the
    path or the quoting is wrong."""
    ran = 0
    for rec in _classify(lib):
        if not rec["next_command"]:
            continue
        assert rec["next_command"].startswith(f"acidcat {rec['next']} ")
        argv = [sys.executable, "-m", "acidcat", rec["next"], rec["path"]]
        out = subprocess.run(argv, capture_output=True, text=True)
        assert "No such file" not in out.stderr, rec["next_command"]
        ran += 1
    assert ran, "no verdict named a next verb, so nothing was proven"


def test_next_command_quotes_even_without_spaces(tmp_path):
    """A Windows path has no spaces but plenty of backslashes, which an
    unquoted shell word eats. The table's display hint quotes only on spaces;
    the machine field must always quote."""
    _wav(tmp_path / "plain.wav")
    rec = _classify(tmp_path / "plain.wav")[0]
    if rec["next_command"]:
        target = rec["next_command"].split(" ", 2)[2]
        assert target.startswith('"') and target.endswith('"')


def test_the_rows_name_a_file_by_path(lib):
    """2.0 (cli-2.0.md section 4.1): a machine row names its file by `path`,
    the one to run things on; the display name is the table's, so there is
    no `file` beside it to collapse into it."""
    for rec in _classify(lib):
        assert "file" not in rec and rec["path"]
