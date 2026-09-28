"""A path in a machine row is the path as given (cli-2.0.md section 4.1).

Review V8: on Windows `classify` normpath'd every path to backslashes and
`stats --by meta` joined os.walk's roots with os.sep, so a target typed
`C:/samples` gave rows mixing `/` and `\\`. `locate`'s rows had no `path` and
no `label`.
"""

import json
import os

import pytest

import seeds
from acidcat.cli import main
from acidcat.util import paths


@pytest.mark.parametrize("top,found,want", [
    ("C:/samples", "C:/samples\\loops\\a.wav", "C:/samples/loops/a.wav"),
    ("C:\\samples", "C:\\samples\\loops\\a.wav", "C:\\samples\\loops\\a.wav"),
    ("samples", "samples\\a.wav", "samples\\a.wav"),
])
def test_a_found_path_takes_the_targets_separator(monkeypatch, top, found, want):
    monkeypatch.setattr(paths.os, "sep", "\\")
    assert paths.as_given(top, found) == want


def test_posix_paths_are_untouched():
    if os.sep != "/":
        pytest.skip("POSIX only")
    assert paths.as_given("a/b", "a/b/c.wav") == "a/b/c.wav"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.wav").write_bytes(seeds.build("wav"))
    monkeypatch.chdir(tmp_path)
    return "./d"


def _rows(argv, capsys):
    main(argv)
    return json.loads(capsys.readouterr().out)


def test_classify_keeps_the_path_as_given(tree, capsys):
    """normpath made `./d/a.wav` into `d/a.wav` (and `\\` on Windows)."""
    rows = _rows(["classify", tree, "--json"], capsys)
    assert [r["path"] for r in rows] == ["./d/a.wav"]


@pytest.mark.parametrize("by", ["meta", "shape"])
def test_stats_keeps_the_path_as_given(tree, capsys, by):
    rows = _rows(["stats", tree, "--by", by, "--json", "-q"], capsys)
    assert [r["path"] for r in rows] == ["./d/a.wav"]
    rows = _rows(["stats", "./d/a.wav", "--by", by, "--json", "-q"], capsys)
    assert [r["path"] for r in rows] == ["./d/a.wav"]


def test_locate_rows_name_the_blob_and_the_format(tmp_path, capsys):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(bytes(1000) + seeds.build("wav") + bytes(500))
    rows = _rows(["locate", str(blob), "--json", "-q"], capsys)
    wav = [r for r in rows if r["format"] == "wav"]
    assert wav and wav[0]["path"] == str(blob) and wav[0]["label"] == "RIFF/WAVE"
    assert list(wav[0])[:4] == ["path", "kind", "format", "label"]
