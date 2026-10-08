"""`probe table` -- turning a discovered offset table into carve-ready regions.

The gap between acidcat-as-hex-viewer and acidcat-as-RE-workbench. An audit
reverse-engineered a real proprietary container (a version byte, a frame count,
an offset table, frame headers) using `od`, `probe read`, `probe entropy` and
cross-specimen `carve --encoding hex` -- about eight commands from "opaque" to a
complete spec. Then it stopped: nothing let you express what you had learned.
`--struct` decodes one fixed record and cannot take a count from a field it just
read, so carving the 375 frames meant leaving the tool and computing offsets in
Python.

The specimen below is that container's layout:

    0x00  u8       version
    0x01  u32      per-file id
    0x05  u8       0
    0x06  u8       mode
    0x07  u32 le   frame_count
    0x0B  u32 le   [frame_count] offsets, relative to just past the table
"""

import json
import struct
import subprocess
import sys

import pytest


def _frames(n=6):
    return [bytes([0x05, 0x04, 0x05, 0x05, i, 0, 0, 0, 0x30]) + bytes(40 + i * 3)
            for i in range(n)]


def _specimen(path, n=6, claim=None):
    frames = _frames(n)
    offs, run = [], 0
    for f in frames:
        offs.append(run)
        run += len(f)
    hdr = bytes([3]) + struct.pack("<I", 0xCAFEBABE) + bytes([0]) + b"E"
    hdr += struct.pack("<I", n if claim is None else claim)
    hdr += b"".join(struct.pack("<I", o) for o in offs)
    path.write_bytes(hdr + b"".join(frames))
    return frames, len(hdr)


@pytest.fixture
def spec(tmp_path):
    p = tmp_path / "spec.ch1"
    frames, data_start = _specimen(p)
    return p, frames, data_start


def _probe(path, *args):
    # operand last: `probe SUBVERB [OPTIONS] FILE...`
    return subprocess.run([sys.executable, "-m", "acidcat", "probe"]
                          + list(args) + [str(path)],
                          capture_output=True, text=True)


_WALK = ["table", "0x0b", "--count-at", "0x07", "--type", "u32", "--le",
         "--base", "after-table"]


def test_the_table_resolves_to_the_real_frames(spec):
    p, frames, data_start = spec
    doc = json.loads(_probe(p, "--json", *_WALK).stdout)

    assert doc["entries"] == len(frames)
    assert doc["base"] == data_start
    got = [(r["offset"], r["length"]) for r in doc["regions"]]

    off, want = data_start, []
    for f in frames:
        want.append((off, len(f)))
        off += len(f)
    assert got == want


def test_it_pipes_straight_into_carve(spec, tmp_path):
    """No jq in the middle: carve --batch already accepts this envelope, which
    is the entire reason the records are shaped like `locate`'s."""
    p, frames, _ = spec
    out = tmp_path / "frames"

    probe = subprocess.Popen(
        [sys.executable, "-m", "acidcat", "probe", "--json", *_WALK, str(p)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    carve = subprocess.Popen(
        [sys.executable, "-m", "acidcat", "carve", str(p), "--batch", "-",
         "-o", str(out)], stdin=probe.stdout,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    probe.stdout.close()
    _, err = carve.communicate()
    probe.wait()
    assert carve.returncode == 0, err.decode()

    written = sorted(out.iterdir())
    assert len(written) == len(frames)
    for got, expect in zip(written, frames):
        assert got.read_bytes() == expect      # byte-identical, not just sized


def test_absolute_entries_need_no_base(tmp_path):
    """The other common layout: entries are absolute file offsets."""
    p = tmp_path / "abs.bin"
    body = b"AAAA" + b"BBBBBB" + b"CCCCCCCC"
    # 4-byte count, then a 3-entry table (12 bytes), so the body starts at 16
    tbl = struct.pack("<III", 16, 20, 26)
    p.write_bytes(struct.pack("<I", 3) + tbl + body)
    assert len(p.read_bytes()) == 34

    doc = json.loads(_probe(p, "--json", "table", "0x04", "--count-at", "0x00",
                            "--type", "u32", "--le").stdout)
    assert [r["offset"] for r in doc["regions"]] == [16, 20, 26]
    assert [r["length"] for r in doc["regions"]] == [4, 6, 8]


def test_an_explicit_count_works_without_reading_one(spec):
    p, frames, _ = spec
    doc = json.loads(_probe(p, "--json", "table", "0x0b", "--count",
                            str(len(frames)), "--type", "u32", "--le",
                            "--base", "after-table").stdout)
    assert doc["entries"] == len(frames)


def test_a_lying_count_is_bounded_and_declared(tmp_path):
    """The count comes out of the file under investigation, so it is hostile by
    definition. It must not drive an allocation, and the report must not quote
    the clamped number back as though the file had said it."""
    p = tmp_path / "liar.ch1"
    _specimen(p, n=6, claim=0x0FFFFFFF)

    doc = json.loads(_probe(p, "--json", *_WALK).stdout)
    assert doc["declared_count"] == 0x0FFFFFFF     # what the file claimed
    assert doc["truncated_to_file"] is True
    assert doc["entries"] < 1000                   # what we actually walked
    assert doc["usable"] <= doc["entries"]


def test_a_zero_count_is_an_error_not_an_empty_success(tmp_path):
    p = tmp_path / "zero.ch1"
    _specimen(p, n=6, claim=0)
    r = _probe(p, *_WALK)
    assert r.returncode == 2
    assert "count" in r.stderr.lower()


def test_missing_count_is_a_usage_error(spec):
    p, _, _ = spec
    r = _probe(p, "table", "0x0b", "--type", "u32", "--le")
    assert r.returncode == 2
    assert "--count" in r.stderr


def test_table_without_a_byte_order_uses_the_formats_own(spec):
    # the byte order is optional: leaving it off once crashed on args.be
    p, _, _ = spec
    r = _probe(p, "table", "0x0b", "--count-at", "0x07", "--type", "u32")
    assert r.returncode == 0, r.stderr
    assert "internal error" not in r.stderr
