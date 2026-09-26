"""The specimen library: one real file per format variant, checked every run.

`tests/specimens/` is a separate private repository of real files (see
`manifest.json` there). This repository ignores it, and every test here skips
when it is absent, so a checkout without it still passes. Where it is present,
each specimen must be the file the manifest names, sniff as its format, walk
without raising, place no two siblings on the same bytes, and read every
positioned field back from its bytes.

These are the checks the seeds cannot make: a seed is built to be valid, and
the bugs that reached 1.8.6 lived in variants no seed had.
"""
import hashlib
import json
import os

import pytest

from acidcat.core.infra import sniff
from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported

import test_chunk_geometry as geo

LIB = os.path.join(os.path.dirname(__file__), "specimens")
MANIFEST = os.path.join(LIB, "manifest.json")

pytestmark = pytest.mark.skipif(not os.path.exists(MANIFEST),
                                reason="no specimen library at tests/specimens/")


def _entries():
    if not os.path.exists(MANIFEST):
        return []
    with open(MANIFEST, encoding="utf-8") as fh:
        return [e for e in json.load(fh)["specimens"] if e.get("status") != "drop"]


@pytest.fixture(scope="module")
def walked():
    out = []
    for e in _entries():
        if e["label"] == "unsupported":
            continue
        path = os.path.join(LIB, e["file"])
        label, chunks, warns = walk_file(path)
        out.append((path, label, chunks, warns))
    return out


def test_the_library_is_not_empty():
    assert len(_entries()) >= 50, "the manifest lists almost nothing"


@pytest.mark.parametrize("e", _entries(), ids=lambda e: e["file"])
def test_the_file_is_the_one_the_manifest_names(e):
    path = os.path.join(LIB, e["file"])
    with open(path, "rb") as fh:
        assert hashlib.sha256(fh.read()).hexdigest() == e["sha256"]


@pytest.mark.parametrize("e", _entries(), ids=lambda e: e["file"])
def test_it_sniffs_as_its_format_and_walks(e):
    path = os.path.join(LIB, e["file"])
    assert sniff.sniff(path) == e["format"]
    if e["label"] == "unsupported":
        with pytest.raises(Unsupported):
            walk_file(path)
        return
    label, chunks, warns = walk_file(path)
    assert label == e["label"]
    assert chunks, "the walk found nothing"


def test_no_two_siblings_claim_the_same_bytes(walked):
    bad = [(os.path.basename(p), c) for p, _l, chunks, _w in walked
           for c in geo._collisions(chunks)[:1]]
    assert not bad, "\n".join(f"  {f}: {c}" for f, c in bad[:12])


def test_every_positioned_value_is_its_bytes(walked):
    bad = [m for m in geo._mismatches(walked)
           if m[0] not in geo.KNOWN_TRANSFORMS and m[0] not in geo.CORPUS_TRANSFORMS]
    assert not bad, "\n".join(f"  {k} {why} ({fn})" for k, why, fn in bad[:12])
