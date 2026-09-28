"""A search anchor that finds nothing is the answer no (1), on every verb
that takes `--at`; a malformed one is still could-not-run (2). `od F --at
find:NOPE` exited 2, as for a typo (cli-2.0.md section 1)."""

import pytest

import seeds
from acidcat.cli import main


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(seeds.build("wav"))
    return str(p)


@pytest.mark.parametrize("verb", ["od", "carve", "inspect"])
@pytest.mark.parametrize("anchor,want", [
    ("find:NOPE", 1), ("find:0xdeadbeef", 1), ("chunk:ZZZZ", 1),
    ("bogus", 2), ("find:", 2),
])
def test_an_anchor_that_finds_nothing_is_1(wav, tmp_path, verb, anchor, want,
                                          capsys):
    extra = ["-o", str(tmp_path / "c.bin")] if verb == "carve" else []
    assert main([verb, wav, "--at", anchor, *extra]) == want
    capsys.readouterr()


def test_an_anchor_that_finds_something_is_0(wav, tmp_path, capsys):
    assert main(["od", wav, "--at", "find:data"]) == 0
    out = tmp_path / "c.bin"
    assert main(["carve", wav, "--at", "find:data", "-o", str(out)]) == 0
    assert out.read_bytes().startswith(b"data")
