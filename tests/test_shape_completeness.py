"""`shape` answers about every file you NAME, walkable or not.

Naming a file is a question about that file. It used to be answered with zero
bytes and exit 0 -- indistinguishable from "walked it, the filters excluded it"
-- so `shape mystery.ch1` printed nothing, and `shape *.ch1` over sixteen
specimens of one unknown format produced an empty histogram instead of the
cluster of sixteen that is the whole signal.

Directory recursion keeps filtering: `shape ~/samples` is a question about a
tree, where a row per README and .DS_Store is noise. The two cases are pinned
separately below, because it is the distinction that makes both correct.
"""

import struct
import subprocess
import sys


def _wav(path):
    pcm = b"\x00\x00" * 512
    body = (b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def _shape(tmp_path, *args):
    r = subprocess.run([sys.executable, "-m", "acidcat", "shape", "--no-path"]
                       + list(args), cwd=str(tmp_path),
                       capture_output=True, text=True)
    return r


def test_every_file_gets_a_row(tmp_path):
    _wav(tmp_path / "a.wav")
    (tmp_path / "opaque.bin").write_bytes(bytes(range(256)) * 8)
    (tmp_path / "plain.txt").write_text("hello world\n")

    r = _shape(tmp_path, "a.wav", "opaque.bin", "plain.txt")
    assert r.returncode == 0, r.stderr

    rows = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(rows) == 3, f"3 files in, {len(rows)} rows out: {r.stdout!r}"
    assert sum(1 for ln in rows if ln.startswith("?unwalked")) == 2


def test_single_unknown_file_is_not_silence(tmp_path):
    (tmp_path / "mystery.ch1").write_bytes(bytes(range(256)) * 8)
    r = _shape(tmp_path, "mystery.ch1")
    # the row is printed; the exit says no walker read it, as --by meta and
    # --by chunks say of the same file (review V7)
    assert r.returncode == 1
    assert r.stdout.strip(), "an unknown file produced no output at all"
    assert r.stdout.startswith("?unwalked")


def test_unknowns_cluster_for_uniq(tmp_path):
    for i in range(4):
        (tmp_path / f"m{i}.ch1").write_bytes(bytes(range(256)) * (8 + i))
    r = _shape(tmp_path, "--coarse", *[f"m{i}.ch1" for i in range(4)])
    labels = {ln.split("\t")[0] for ln in r.stdout.splitlines() if ln.strip()}
    assert labels == {"?unwalked"}
    assert len(r.stdout.splitlines()) == 4


def test_fast_path_agrees_with_the_full_path(tmp_path):
    _wav(tmp_path / "a.wav")
    (tmp_path / "opaque.bin").write_bytes(bytes(range(256)) * 8)

    full = _shape(tmp_path, "a.wav", "opaque.bin")
    fast = _shape(tmp_path, "--fast", "a.wav", "opaque.bin")
    assert len(full.stdout.splitlines()) == len(fast.stdout.splitlines()) == 2


def test_warn_only_still_means_warnings(tmp_path):
    """An unwalked file is not a structural warning; --warn-only must not
    suddenly fill up with them."""
    (tmp_path / "opaque.bin").write_bytes(bytes(range(256)) * 8)
    r = _shape(tmp_path, "--warn-only", "opaque.bin")
    assert r.returncode == 1          # ran fine, the filter matched nothing
    assert r.stdout.strip() == ""


def test_directory_recursion_still_filters(tmp_path):
    """The other half of the contract. A tree sweep must not sprout a row for
    every README, cover.jpg and .DS_Store it walks past."""
    lib = tmp_path / "lib"
    lib.mkdir()
    _wav(lib / "a.wav")
    (lib / "README.md").write_text("# notes\n")
    (lib / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0" + bytes(64))
    (lib / ".DS_Store").write_bytes(bytes(128))

    r = _shape(tmp_path, str(lib))
    rows = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(rows) == 1
    assert rows[0].startswith("RIFF/WAVE")
