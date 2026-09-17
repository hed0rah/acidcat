"""S98: the PC-98 register log. v1 keeps its title before the dump, v3 its
[S98] tag after; the sync count after 0xFE is a 7-bit varint in both.
"""

import os
import struct

import pytest

from acidcat.core.formats import s98 as s98mod
from acidcat.core.infra import sniff
from acidcat.core.walk import s98 as walker

import seeds


def _s98(**kw):
    return seeds.SEEDS["s98"][0](**kw)


def _write(tmp_path, blob, name="a.s98"):
    p = tmp_path / name
    p.write_bytes(blob)
    return p


def _tiles(chunks, size):
    pos = 0
    for c in sorted(chunks, key=lambda c: c["offset"]):
        if c["offset"] != pos:
            return False
        pos += c["size"]
    return pos == size


def test_an_s98_is_identified_by_magic_and_version_digit(tmp_path):
    assert sniff.sniff(str(_write(tmp_path, _s98()))) == "s98"
    assert sniff.sniff(str(_write(tmp_path, _s98(version=1), "b.s98"))) == "s98"
    assert "s98" in sniff.KNOWN_FORMATS


def test_v3_reads_the_device_table_and_puts_the_tag_after_the_dump():
    raw = _s98(devices=((4, 7987200), (1, 3993600)))
    h = s98mod.parse_header(raw)
    assert [d["name"] for d in h["devices"]] == ["YM2608 (OPNA)", "YM2149 (PSG)"]
    assert h["header_size"] == 0x20 + 32
    assert not h["tag_before_dump"] and h["tag_at"] > h["dump_at"]


def test_v1_puts_a_plain_title_before_the_dump_at_0x80():
    raw = _s98(version=1, title="Silf")
    h = s98mod.parse_header(raw)
    assert h["tag_before_dump"] and h["tag_at"] == 0x20 and h["dump_at"] == 0x80
    assert not h["devices"]
    tag = s98mod.parse_tag(raw, h["tag_at"], h["dump_at"])
    assert tag["ok"] and tag["fields"]["title"] == "Silf"


def test_the_timer_defaults_to_ten_milliseconds():
    blob = bytearray(_s98())
    struct.pack_into("<II", blob, 4, 0, 0)
    assert s98mod.parse_header(bytes(blob))["timer"] == (10, 1000)


def test_the_sync_count_is_a_varint_in_every_version():
    """One byte reads 200 real v1 files wrong: their counts run past 127."""
    for version in (1, 3):
        raw = _s98(version=version, big_wait=300)
        h = s98mod.parse_header(raw)
        end = len(raw) if version == 1 else h["tag_at"]
        w = s98mod.walk_dump(raw, h["dump_at"], end, version, 10 ** 6)
        assert w["ended"] and w["end"] == end
        assert w["syncs"] == 2 + 300, (version, w["syncs"])
        assert w["writes"] == {0: 4}


def test_the_walk_tiles_v3_and_v1(tmp_path):
    for version in (3, 1):
        p = _write(tmp_path, _s98(version=version), "v%d.s98" % version)
        chunks, warns = walker.inspect_s98(str(p))
        assert _tiles(chunks, os.path.getsize(p)), [(c["id"], c["offset"], c["size"]) for c in chunks]
        assert not warns
        ids = [c["id"] for c in chunks]
        assert ids == (["header", "dump", "tag"] if version == 3 else ["header", "tag", "dump"])
        assert "SEED" in chunks[0]["summary"]


def test_the_tag_is_shift_jis_unless_it_says_utf8():
    raw = _s98(title="SEED")
    h = s98mod.parse_header(raw)
    t = s98mod.parse_tag(raw, h["tag_at"], len(raw))
    assert t["encoding"] == "shift_jis" and t["fields"]["game"] == "SEED GAME"
    blob = raw + b"utf8=1\n"
    assert s98mod.parse_tag(blob, h["tag_at"], len(blob))["encoding"] == "utf-8"


def test_a_dump_without_an_end_marker_is_said(tmp_path):
    raw = _s98(version=1)
    _chunks, warns = walker.inspect_s98(str(_write(tmp_path, raw[:-1])))
    assert any("no end marker" in w for w in warns)


def test_writes_to_an_undeclared_device_are_said(tmp_path):
    blob = bytearray(_s98())
    h = s98mod.parse_header(bytes(blob))
    blob[h["dump_at"]] = 0x06                                  # device 3
    _chunks, warns = walker.inspect_s98(str(_write(tmp_path, bytes(blob))))
    assert any("device 3" in w for w in warns)


@pytest.mark.parametrize("n", [5, 0x20, 0x30, 0x60])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    from acidcat.core.walk.base import Unsupported
    try:
        walker.inspect_s98(str(_write(tmp_path, _s98()[:n])))
    except Unsupported:
        pass


@pytest.mark.skipif(not os.environ.get("ACIDCAT_S98_CORPUS"),
                    reason="set ACIDCAT_S98_CORPUS to a dir of real .s98 files")
def test_real_corpus_walks_completely():
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file
    root = os.environ["ACIDCAT_S98_CORPUS"]
    files = [os.path.join(r, f) for r, _d, fn in os.walk(root) for f in fn
             if f.lower().endswith(".s98")]
    assert len(files) >= 50
    seen = tiled = 0
    for path in files:
        if sniff.sniff(path) != "s98":
            continue
        seen += 1
        _label, chunks, _warns = walk_file(path)
        size = os.path.getsize(path)
        geometry.normalize(chunks, size)
        assert all(geometry.is_trustworthy(c) for c in chunks), path
        tiled += _tiles(chunks, size)
    assert seen >= len(files) * 0.99, "%d of %d not identified" % (len(files) - seen, len(files))
    assert tiled == seen, "%d of %d did not tile" % (seen - tiled, seen)
