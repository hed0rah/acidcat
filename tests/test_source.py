"""Walkers read a Source, so bytes in memory walk exactly as a file does.

core/infra/source.py replaced the path every walker used to open and stat.
These tests pin the three things that change could break: the Source and its
file object behave like a file, a plain path passed to a helper is opened as it
always was (no mapping left behind), and every seed gives the same Document
from bytes as from disk, apart from the differences that need a directory.
"""

import io
import json
import os
import tempfile

import pytest

import seeds
from acidcat.core.infra import contract
from acidcat.core.infra.source import (BytesSource, MappedSource, as_source,
                                       input_name, input_size, open_input)
from acidcat.core.walk import walk_bytes, walk_file


# ── the Source and its file object ─────────────────────────────────────

@pytest.fixture
def blob(tmp_path):
    data = bytes(range(256)) * 4
    p = tmp_path / "blob.bin"
    p.write_bytes(data)
    return data, str(p)


def test_both_backends_give_the_same_bytes(blob):
    data, path = blob
    with MappedSource(path) as m:
        b = BytesSource(data, name="blob.bin")
        for src in (m, b):
            assert src.size == len(data)
            assert src.read(10, 5) == data[10:15]
            assert src.read(len(data) - 3, 10) == data[-3:]      # short at the end
            assert src.read(len(data) + 1, 4) == b""
            assert bytes(src.view(0, 4)) == data[:4]
        assert m.name == b.name == "blob.bin" and m.ext == ".bin"


def test_the_file_object_seeks_and_reads_like_a_file(blob):
    data, path = blob
    with open(path, "rb") as real, BytesSource(data).file() as f:
        for off, whence, n in ((5, 0, 7), (3, 1, 2), (-10, 2, 20), (2000, 0, 4)):
            assert f.seek(off, whence) == real.seek(off, whence)
            assert f.read(n) == real.read(n)
            assert f.tell() == real.tell()
        f.seek(0)
        buf = bytearray(8)
        assert f.readinto(buf) == 8 and bytes(buf) == data[:8]
        with pytest.raises(OSError):
            f.seek(-1)


def test_zipfile_and_gzip_read_through_it(tmp_path):
    import gzip
    import zipfile
    from acidcat.core.infra.source import gzip_open, zip_open
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("a.txt", b"hello")
    with zip_open(BytesSource(zbuf.getvalue())) as z:
        assert z.read("a.txt") == b"hello"
    with gzip_open(BytesSource(gzip.compress(b"payload"))) as g:
        assert g.read(7) == b"payload"


def test_a_sibling_needs_a_directory(tmp_path):
    (tmp_path / "tune.minigsf").write_bytes(b"x")
    (tmp_path / "tune.gsflib").write_bytes(b"lib!")
    with MappedSource(str(tmp_path / "tune.minigsf")) as m:
        lib = m.sibling("tune.gsflib")
        assert lib is not None and lib.read(0, 4) == b"lib!"
        lib.close()
        assert m.sibling("absent.gsflib") is None
    assert BytesSource(b"x", name="tune.minigsf").sibling("tune.gsflib") is None


def test_a_plain_path_is_opened_as_before(blob):
    """The helpers serve callers that hold a path too; for those they must not
    map the file (Windows cannot replace a mapped file, and the editors do)."""
    data, path = blob
    with open_input(path) as f:
        assert isinstance(f, io.BufferedReader)
        assert f.read(4) == data[:4]
    assert input_size(path) == len(data)
    assert input_name(path) == path
    assert as_source(BytesSource(data)).size == len(data)


# ── walking bytes: no temp file, and the same answer ───────────────────

def test_walking_bytes_writes_no_temp_file(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("walk_bytes wrote a temp file")
    monkeypatch.setattr(tempfile, "mkstemp", refuse)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", refuse)
    label, chunks, _ = walk_bytes(seeds.build("rmid"), suffix=".rmi")
    assert chunks, label
    # RMID walks its wrapped SMF in memory too
    assert any(str(c.get("id")).strip() == "MThd" for c in chunks)


def test_the_cue_seed_parses():
    """A sheet that fails to parse still walks (it degrades to a summary), so
    nothing else notices when the parser breaks. It broke once already: the
    parser opened its argument as a text file and a Source is not one."""
    label, chunks, warns = walk_bytes(seeds.build("cue"), suffix=".cue")
    assert "did not parse" not in chunks[0]["summary"], chunks[0]["summary"]
    assert "2 track(s)" in chunks[0]["summary"]


# format -> why its Document from bytes differs from the one from disk. Each
# needs a directory to look beside the file; in memory there is none, so the
# check is not made rather than reported as a missing file. Shrink only.
KNOWN_DIFFERENCES = {
    "cue": "the BIN files it names are looked for beside the sheet",
    "psf": "the _lib it names is looked for beside the file",
}


def _documents():
    tmp = tempfile.mkdtemp(prefix="acidcat-source-")
    for fmt in sorted(seeds.SEEDS):
        data, name = seeds.build(fmt), fmt + seeds.suffix(fmt)
        path = os.path.join(tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        yield (fmt, contract.walk(path, deep=True),
               contract.walk(BytesSource(data, name=name), deep=True))


def test_bytes_and_disk_give_the_same_document():
    differ, same = [], set()
    for fmt, disk, mem in _documents():
        if json.dumps(disk, sort_keys=True) != json.dumps(mem, sort_keys=True):
            differ.append(fmt)
        else:
            same.add(fmt)
    unexplained = [f for f in differ if f not in KNOWN_DIFFERENCES]
    assert not unexplained, ("Documents that differ between a file and its "
                             "bytes: " + ", ".join(unexplained))
    stale = sorted(set(KNOWN_DIFFERENCES) & same)
    assert not stale, "KNOWN_DIFFERENCES entries that no longer differ: " + ", ".join(stale)


def test_walk_file_takes_a_source_and_leaves_it_open(blob):
    data, path = blob
    src = BytesSource(seeds.build("wav"), name="a.wav")
    label, chunks, _ = walk_file(src)
    assert label == "RIFF/WAVE" and chunks
    assert src.read(0, 4) == b"RIFF"        # the caller's Source is still usable
