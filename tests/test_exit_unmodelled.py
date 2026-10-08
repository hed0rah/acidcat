"""A file a verb has nothing for is could-not-run (2) on every verb, and a
tree with nothing a `stats` mode reads is the answer no (1) in every mode.

Review V7: on an unrecognised file `edit` and `extract` exited 1 while
`inspect`, `check` and `convert` exited 2; `stats` on junk gave 1, 0 or 0
depending on --by.
"""

import os

import pytest

import seeds
from acidcat.cli import main

pytest.importorskip("mutagen")


@pytest.fixture
def junk(tmp_path):
    p = tmp_path / "junk.bin"
    p.write_bytes(bytes(range(256)) * 3)
    return p


@pytest.mark.parametrize("argv", [
    ["edit", "{f}", "--set", "title=x"],
    ["edit", "{f}", "--strip"],
    ["edit", "{f}", "--set", "@0+1=hex:00"],
    ["edit", "{f}", "--set", "cover=@{img}"],
    ["edit", "{f}", "--get", "cover"],
    ["extract", "{f}", "-o", "{out}"],
    ["inspect", "{f}"],
    ["check", "{f}"],
])
def test_a_file_the_verb_has_nothing_for_exits_2(junk, tmp_path, argv, capsys):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    argv = [a.format(f=junk, img=img, out=tmp_path / "out") for a in argv]
    assert main(argv) == 2
    capsys.readouterr()


def test_a_refused_edit_of_a_file_it_edits_is_still_1(tmp_path, capsys):
    # the editor reads the file and declines: an acid chunk too short to
    # rewrite safely
    import struct
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"acid" + struct.pack("<I", 8) + b"\x00" * 8
            + b"data" + struct.pack("<I", 4) + b"\x00" * 4)
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    assert main(["edit", str(p), "--set", "bpm=120", "--dry-run"]) == 1


def test_a_field_the_format_does_not_have_is_a_bad_argument(tmp_path, capsys):
    # was 1 (a refused edit); a name the editor has no field for is a bad
    # argument value, like `formats nope`: could not run, 2
    p = tmp_path / "a.wav"
    p.write_bytes(seeds.build("wav"))
    assert main(["edit", str(p), "--set", "nosuchtag=1", "--dry-run"]) == 2


@pytest.mark.parametrize("by", ["meta", "shape", "chunks"])
def test_stats_with_nothing_to_read_is_1_in_every_mode(junk, tmp_path, by, capsys):
    d = tmp_path / "d"
    d.mkdir()
    os.replace(junk, d / "junk.bin")
    assert main(["stats", str(d), "--by", by, "-q"]) == 1
    assert main(["stats", str(d / "junk.bin"), "--by", by, "-q"]) == 1
    (d / "a.wav").write_bytes(seeds.build("wav"))
    assert main(["stats", str(d), "--by", by, "-q"]) == 0
