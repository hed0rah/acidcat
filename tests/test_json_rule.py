"""One JSON rule across verbs (review R7, cli-2.0.md section 4.1): row verbs
give an array of rows, each naming its file by `path` as given and its format
by `format` (the registry id) and `label`; keys are snake_case."""

import io
import json
import re
import sys

import pytest

import seeds
from acidcat.cli import main

_SNAKE = re.compile(r"^[a-z0-9_]+$")


def _json(argv):
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = main(argv)
    finally:
        sys.stdout, sys.stderr = old
    return rc, json.loads(out.getvalue())


@pytest.fixture
def two(tmp_path):
    paths = []
    for name in ("a.wav", "b.wav"):
        p = tmp_path / name
        p.write_bytes(seeds.build("wav"))
        paths.append(str(p))
    return paths


@pytest.mark.parametrize("argv", [
    ["inspect", "--summary"], ["classify"], ["check"], ["audit"],
    ["edit", "--set", "title=x", "--dry-run"], ["stats", "--by", "shape"],
])
def test_a_row_verb_gives_one_array_of_rows_by_path(two, argv):
    rc, rows = _json(argv[:1] + two + argv[1:] + ["--json"])
    assert isinstance(rows, list) and len(rows) == 2, rows
    for row, path in zip(rows, two):
        assert row["path"] == path
        assert (row["format"], row["label"]) == ("wav", "RIFF/WAVE")
        assert all(_SNAKE.match(k) for k in row), sorted(row)


def test_one_file_is_still_an_array(two):
    for argv in (["inspect", "--summary"], ["audit"]):
        _rc, rows = _json(argv + two[:1] + ["--json"])
        assert isinstance(rows, list) and len(rows) == 1


def test_an_unrecognised_file_has_null_format(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(bytes(range(256)) * 4)
    _rc, (row,) = _json(["audit", str(p), "--json"])
    assert (row["format"], row["label"]) == (None, None)
