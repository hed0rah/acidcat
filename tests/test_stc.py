"""STC: the ZX Spectrum Sound Tracker module. No magic; the blocks come in
a fixed order with fixed record sizes, so identification is arithmetic.
"""

import os
import struct

import pytest

from acidcat.core.formats import stc as stcmod
from acidcat.core.infra import sniff
from acidcat.core.walk import stc as walker

import seeds


def _stc(**kw):
    return seeds.SEEDS["stc"][0](**kw)


def _write(tmp_path, blob, name="a.stc"):
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


def test_an_stc_is_identified_by_arithmetic_under_its_extension(tmp_path):
    p = _write(tmp_path, _stc())
    assert sniff.sniff(str(p)) == "stc"
    assert sniff.sniff(str(_write(tmp_path, _stc(), "a.bin"))) != "stc"


def test_stc_is_a_known_format():
    assert "stc" in sniff.KNOWN_FORMATS


def test_the_blocks_are_read_in_their_fixed_order():
    raw = _stc(samples=3, ornaments=2, patterns=2)
    h = stcmod.parse(raw, len(raw))
    assert h["ok"]
    assert [s["number"] for s in h["samples"]] == [1, 2, 3]
    assert [o["number"] for o in h["ornaments"]] == [0, 1]
    assert h["positions"] == [(0, 0), (1, 0)]
    assert [p["number"] for p in h["patterns"]] == [0, 1]
    assert h["size"] == len(raw)


def test_records_carry_their_own_numbers_so_gaps_are_fine():
    blob = bytearray(_stc(samples=2))
    blob[stcmod.HEADER + stcmod.SAMPLE_RECORD] = 9          # second sample is number 9
    h = stcmod.parse(bytes(blob), len(blob))
    assert [s["number"] for s in h["samples"]] == [1, 9]


def test_a_sample_area_that_is_not_whole_records_is_refused():
    raw = _stc()
    h = stcmod.parse(raw, len(raw))
    blob = bytearray(raw)
    struct.pack_into("<H", blob, stcmod.POSITIONS_PTR_AT, h["positions_at"] - 1)
    assert not stcmod.parse(bytes(blob), len(blob))["ok"]


def test_a_positions_block_that_does_not_end_at_the_ornaments_is_refused():
    raw = _stc(patterns=2)
    blob = bytearray(raw)
    h = stcmod.parse(raw, len(raw))
    blob[h["positions_at"]] = 5                                # says 6 positions
    assert not stcmod.parse(bytes(blob), len(blob))["ok"]


def test_the_walk_tiles_with_every_record_named(tmp_path):
    p = _write(tmp_path, _stc(samples=2, ornaments=1, patterns=2))
    chunks, warns = walker.inspect_stc(str(p))
    ids = [c["id"] for c in chunks]
    assert ids[:4] == ["header", "smp[1]", "smp[2]", "positions"]
    assert "orn[0]" in ids and "patterns" in ids and "chan[0A]" in ids and "chan[1C]" in ids
    assert _tiles(chunks, os.path.getsize(p))
    assert not warns


def test_text_between_the_ornaments_and_the_table_is_a_comment(tmp_path):
    """Some compilers leave a line of author text there: 51 of 832 real files."""
    p = _write(tmp_path, _stc(comment=b"TOV. ZX CLUB 5.       "))
    chunks, _w = walker.inspect_stc(str(p))
    c = next(c for c in chunks if c["id"] == "comment")
    assert "ZX CLUB" in c["summary"] and c["size"] == 22
    assert _tiles(chunks, os.path.getsize(p))


def test_a_size_field_that_disagrees_is_said(tmp_path):
    blob = bytearray(_stc())
    struct.pack_into("<H", blob, stcmod.SIZE_AT, 8536)
    _chunks, warns = walker.inspect_stc(str(_write(tmp_path, bytes(blob))))
    assert any("header says 8536" in w for w in warns)


@pytest.mark.parametrize("n", [10, 0x1B, 0x80, 0xE0])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    from acidcat.core.walk.base import Unsupported
    try:
        walker.inspect_stc(str(_write(tmp_path, _stc()[:n])))
    except Unsupported:
        pass


@pytest.mark.skipif(not os.environ.get("ACIDCAT_STC_CORPUS"),
                    reason="set ACIDCAT_STC_CORPUS to a dir of real .stc files")
def test_real_corpus_walks_completely():
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file
    root = os.environ["ACIDCAT_STC_CORPUS"]
    files = [os.path.join(r, f) for r, _d, fn in os.walk(root) for f in fn
             if f.lower().endswith(".stc")]
    assert len(files) >= 50
    seen = tiled = 0
    for path in files:
        if sniff.sniff(path) != "stc":
            continue
        seen += 1
        _label, chunks, _warns = walk_file(path)
        size = os.path.getsize(path)
        geometry.normalize(chunks, size)
        assert all(geometry.is_trustworthy(c) for c in chunks), path
        tiled += _tiles(chunks, size)
    assert seen >= len(files) * 0.98, "%d of %d not identified" % (len(files) - seen, len(files))
    assert tiled == seen, "%d of %d did not tile" % (seen - tiled, seen)
