"""`acidcat DIR` must do what `acidcat scan DIR` does.

The README presents them as the same thing -- "acidcat DIR | Batch-scan a
directory (auto-detected)" -- and they were not. The bare-path route builds its
OWN fallback parser, a second declaration of flags the real verbs already
declare, and the two defaults drifted: the fallback defaulted `output_format` to
"table" while `scan`'s parser defaults to "csv".

So the bare form rendered a twelve-line vertical record per file. A first-time
user pointing acidcat at a 3,200-file sample library got roughly 38,000 lines
into their terminal, and the one word "auto-detected" is what hid it.

Found by an auditor who installed the wheel, read only the README, and did the
first thing anyone does.
"""

import struct
import subprocess
import sys

import pytest


def _wav(path):
    pcm = b"\x11\x22" * 256
    body = (b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


@pytest.fixture
def lib(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    for i in range(3):
        _wav(d / f"w{i}.wav")
    return d


def _run(cwd, *args):
    return subprocess.run([sys.executable, "-m", "acidcat"] + list(args),
                          cwd=str(cwd), capture_output=True, text=True)


def test_bare_dir_matches_the_stats_verb(tmp_path, lib):
    """2.0: `acidcat DIR` is `acidcat stats DIR`."""
    a = _run(tmp_path, str(lib))
    b = _run(tmp_path, "stats", str(lib))
    assert a.returncode == b.returncode
    assert a.stdout == b.stdout


def test_bare_dir_does_not_dump_a_record_per_file(tmp_path, lib):
    """The symptom, stated directly: three files must not produce dozens of
    lines on stdout."""
    out = _run(tmp_path, str(lib)).stdout
    assert len(out.splitlines()) < 6, out[:400]


def test_an_explicit_rendering_still_wins(tmp_path, lib):
    """The override only applies when the user did NOT ask. `acidcat DIR --json`
    still means JSON."""
    import json
    for flag in ("--json", "--output-format", "--output-format=json"):
        args = [str(lib), flag] + (["json"] if flag == "--output-format" else [])
        out = _run(tmp_path, *args).stdout
        json.loads(out)                     # must be JSON, not a CSV file write


def test_the_bare_form_is_the_verb_not_a_copy_of_it():
    """The drift came from a second parser declaring the verbs' flags. The bare
    form now puts the verb in and parses as that verb, so there is nothing to
    drift."""
    import os
    from acidcat.cli import _bare_path
    here = os.path.dirname(__file__)
    assert _bare_path([here, "--json"]) == ["stats", here, "--json"]
    assert _bare_path([__file__]) == ["inspect", "--summary", __file__]
    assert _bare_path(["stats", here]) is None


def test_bare_file_still_routes_to_info(tmp_path):
    """The other half of the bare-path contract must not have moved."""
    p = tmp_path / "a.wav"
    _wav(p)
    a = _run(tmp_path, str(p))
    b = _run(tmp_path, "inspect", "--summary", str(p))
    assert a.returncode == b.returncode
    assert a.stdout == b.stdout
