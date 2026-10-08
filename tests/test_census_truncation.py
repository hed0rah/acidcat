"""A truncated census must not make whole-corpus claims.

`--limit` stops the walk early, and the result then described a prefix in the
same words a complete run uses. The table disclosed "N files opened" but
`--json` carried no limit or truncation field at all, so a consumer could not
tell a sampled census from a small corpus.

The claim that was actually wrong is "rare": on a real library `--limit 20`
reported `LIST 4` under "Rare chunks (<=5 occurrences)" for a chunk that occurs
1,178 times there and is the 4th most common in the tree.
"""

import json
import struct
import subprocess
import sys

import pytest


def _wav(path, extra=b""):
    pcm = b"\x00\x00" * 16
    body = (b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
            + extra
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


@pytest.fixture
def lib(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    # a chunk present in every file: common in the tree, but "rare" in a prefix
    common = b"LIST" + struct.pack("<I", 4) + b"INFO"
    for i in range(12):
        _wav(d / f"w{i:02d}.wav", extra=common)
    return str(d)


def _census(lib, *args):
    r = subprocess.run([sys.executable, "-m", "acidcat", "census", lib]
                       + list(args), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


def test_json_says_whether_it_was_truncated(lib):
    full = json.loads(_census(lib, "--json").stdout)
    assert full["truncated"] is False
    # census is `stats --by chunks` in 2.0, whose --max-files is 10,000
    assert full["limit"] == 10000
    assert full["files_opened"] == 12

    part = json.loads(_census(lib, "--limit", "3", "--json").stdout)
    assert part["truncated"] is True
    assert part["limit"] == 3
    assert part["files_opened"] <= 3


def test_a_prefix_does_not_claim_rarity(lib):
    """LIST is in all 12 files. Seen in 3, it looks rare; the report must say
    the count came from a prefix rather than presenting it as evidence."""
    out = _census(lib, "--limit", "3").stdout
    if "Rare chunks" in out:
        assert "NOT evidence of rarity" in out
        assert "first 3 file(s) only" in out


def test_a_complete_census_adds_no_caveat(lib):
    out = _census(lib).stdout
    assert "NOT evidence of rarity" not in out


def test_the_full_run_sees_list_as_common(lib):
    """Proves the fixture is doing what the docstring claims -- otherwise the
    truncation test above could pass against a corpus where LIST really is
    rare."""
    full = json.loads(_census(lib, "--json").stdout)
    assert full["chunk_histogram"].get("LIST") == {"files": 12, "occurrences": 12}
    assert "LIST" not in {c[0] for c in full["rare_chunks"]}
