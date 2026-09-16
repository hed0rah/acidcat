"""PT3: the ZX Spectrum AY module, reached entirely by absolute pointers.

The file tiles by sorting the pointers: each region begins where one says
and ends where the next begins. Checked on Modland's Spectrum directory.
"""

import os
import struct

import pytest

from acidcat.core.formats import pt3 as pt3mod
from acidcat.core.infra import sniff
from acidcat.core.walk import pt3 as walker

import seeds


def _pt3(**kw):
    return seeds.SEEDS["pt3"][0](**kw)


def _write(tmp_path, blob, name="a.pt3"):
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


def test_a_pt3_is_identified_by_its_signature(tmp_path):
    assert sniff.sniff(str(_write(tmp_path, _pt3()))) == "pt3"
    vt = bytearray(_pt3())
    vt[0:30] = b"Vortex Tracker II 1.0 module: "
    assert sniff.sniff(str(_write(tmp_path, bytes(vt), "b.pt3"))) == "pt3"


def test_pt3_is_a_known_format():
    assert "pt3" in sniff.KNOWN_FORMATS


def test_the_header_reads_name_author_and_the_position_list():
    h = pt3mod.parse(_pt3(name="I MUST", author="9", positions=[0, 1, 0]), 10 ** 6)
    assert h["ok"] and h["name"] == "I MUST" and h["author"] == "9"
    assert h["position_list"] == [0, 1, 0] and h["pattern_count"] == 2
    assert h["header_size"] == pt3mod.POSITION_LIST_AT + 3 + 1


def test_the_position_list_stores_pattern_numbers_times_three():
    blob = bytearray(_pt3(positions=[0, 1]))
    assert blob[pt3mod.POSITION_LIST_AT + 1] == 3
    blob[pt3mod.POSITION_LIST_AT + 1] = 4                    # not a multiple of 3
    assert not pt3mod.parse(bytes(blob), len(blob))["ok"]


def test_a_position_count_that_disagrees_with_the_list_is_refused():
    blob = bytearray(_pt3(positions=[0, 1]))
    blob[pt3mod.POSITIONS_AT] = 5
    h = pt3mod.parse(bytes(blob), len(blob))
    assert not h["ok"] and "positions" in h["why"]


def test_a_pointer_outside_the_file_is_a_truncated_module_not_a_refusal(tmp_path):
    """One Modland file points an ornament past its own end. The module is
    still a PT3; the region is absent and the walker says so."""
    blob = bytearray(_pt3())
    struct.pack_into("<H", blob, pt3mod.SAMPLES_AT + 2, 0xFFF0)
    h = pt3mod.parse(bytes(blob), len(blob))
    assert h["ok"] and h["bad_pointers"] == [("sample", 1, 0xFFF0)]
    chunks, warns = walker.inspect_pt3(str(_write(tmp_path, bytes(blob))))
    assert any("truncated" in w for w in warns)
    assert _tiles(chunks, len(blob))


def test_regions_are_the_sorted_pointers_sized_by_the_next():
    raw = _pt3(patterns=1, samples=1, ornaments=1)
    h = pt3mod.parse(raw, len(raw))
    regs = pt3mod.regions(h, len(raw))
    assert regs[0][0] == h["header_size"]
    assert sum(n for _a, n, _k in regs) == len(raw) - h["header_size"]
    kinds = [k for _a, _n, names in regs for k, _i in names]
    assert kinds == ["pattern_table", "sample", "ornament", "channel", "channel", "channel"]


def test_two_patterns_sharing_a_stream_make_one_region_with_both_names():
    blob = bytearray(_pt3(patterns=2))
    h = pt3mod.parse(bytes(blob), len(blob))
    at = struct.unpack_from("<H", blob, h["patterns_at"])[0]
    struct.pack_into("<H", blob, h["patterns_at"] + 6, at)     # pattern 1 ch A = pattern 0 ch A
    h = pt3mod.parse(bytes(blob), len(blob))
    regs = pt3mod.regions(h, len(blob))
    shared = next(names for a, _n, names in regs if a == at)
    assert shared == [("channel", 0), ("channel", 3)]


def test_the_walk_tiles_the_file_with_every_region_named(tmp_path):
    p = _write(tmp_path, _pt3(patterns=2, samples=2, ornaments=1))
    chunks, warns = walker.inspect_pt3(str(p))
    assert _tiles(chunks, os.path.getsize(p))
    ids = [c["id"] for c in chunks]
    assert ids[:2] == ["header", "patterns"]
    assert "smp[1]" in ids and "smp[2]" in ids and "orn[0]" in ids and "chan[5]" in ids
    assert not warns
    smp = next(c for c in chunks if c["id"] == "smp[1]")
    assert "2 rows" in smp["summary"]


def test_a_sample_that_declares_more_than_fits_is_said(tmp_path):
    blob = bytearray(_pt3(samples=2))
    h = pt3mod.parse(bytes(blob), len(blob))
    blob[h["samples"][1] + 1] = 40                            # 40 rows, 162 bytes
    chunks, warns = walker.inspect_pt3(str(_write(tmp_path, bytes(blob))))
    assert any("runs into the next region" in w for w in warns)
    assert _tiles(chunks, len(blob))


def test_bytes_nothing_points_at_are_a_chunk(tmp_path):
    raw = _pt3()
    h = pt3mod.parse(raw, len(raw))
    blob = bytearray(raw[:h["header_size"]]) + bytes(5) + bytearray(raw[h["header_size"]:])
    # every pointer moves by five
    for off in [pt3mod.PATTERNS_PTR_AT] + [pt3mod.SAMPLES_AT + i * 2 for i in range(32)] \
            + [pt3mod.ORNAMENTS_AT + i * 2 for i in range(16)]:
        v = struct.unpack_from("<H", blob, off)[0]
        if v:
            struct.pack_into("<H", blob, off, v + 5)
    pt = h["patterns_at"] + 5
    for i in range(h["pattern_count"] * 3):
        v = struct.unpack_from("<H", blob, pt + i * 2)[0]
        struct.pack_into("<H", blob, pt + i * 2, v + 5)
    chunks, _w = walker.inspect_pt3(str(_write(tmp_path, bytes(blob))))
    assert chunks[1]["id"] == "unpointed" and chunks[1]["size"] == 5
    assert _tiles(chunks, len(blob))


# ── PT2 ─────────────────────────────────────────────────────────────

def _pt2(**kw):
    return seeds.SEEDS["pt2"][0](**kw)


def test_a_pt2_is_identified_by_arithmetic_under_its_extension(tmp_path):
    """No signature: counts agree, pointers inside, first region at the
    header's end. 2,700 of 2,706 real files; the six refused are cut."""
    p = _write(tmp_path, _pt2(), "a.pt2")
    assert sniff.sniff(str(p)) == "pt3"
    chunks, warns = walker.inspect_pt3(str(p))
    assert _tiles(chunks, os.path.getsize(p))
    assert not warns
    head = {f["name"]: f for f in chunks[0]["fields"]}
    assert head["layout"]["value"] == "Pro Tracker 2"
    assert head["name"]["value"] == "SEED" and head["name"]["off"] == 0x65
    smp = next(c for c in chunks if c["id"] == "smp[1]")
    assert "rows of 3 bytes" in smp["summary"]


def test_a_pt2_whose_first_region_is_not_at_the_header_end_is_refused():
    blob = _pt2()
    h = pt3mod.parse_pt2(blob, len(blob))
    assert h["ok"]
    # move every region one byte later without moving the header's end
    shifted = blob[:h["header_size"]] + b"\x00" + blob[h["header_size"]:]
    assert not pt3mod.parse_pt2(shifted, len(shifted))["ok"]


def test_a_pt2_with_a_pointer_outside_is_refused_because_nothing_else_says_pt2():
    blob = bytearray(_pt2())
    struct.pack_into("<H", blob, pt3mod.PT2_SAMPLES_AT + 2, 0xFFF0)
    assert not pt3mod.parse_pt2(bytes(blob), len(blob))["ok"]


@pytest.mark.parametrize("n", [10, 0x60, 0xC8, 0xD0])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    from acidcat.core.walk.base import Unsupported
    try:
        walker.inspect_pt3(str(_write(tmp_path, _pt3()[:n])))
    except Unsupported:
        pass


@pytest.mark.skipif(not os.environ.get("ACIDCAT_PT3_CORPUS"),
                    reason="set ACIDCAT_PT3_CORPUS to a dir of real .pt3 files")
def test_real_corpus_walks_completely():
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file
    root = os.environ["ACIDCAT_PT3_CORPUS"]
    files = [os.path.join(r, f) for r, _d, fn in os.walk(root) for f in fn
             if f.lower().endswith((".pt3", ".pt2"))]
    assert len(files) >= 50
    seen = tiled = 0
    for path in files:
        if sniff.sniff(path) != "pt3":
            continue
        seen += 1
        _label, chunks, _warns = walk_file(path)
        size = os.path.getsize(path)
        geometry.normalize(chunks, size)
        assert all(geometry.is_trustworthy(c) for c in chunks), path
        tiled += _tiles(chunks, size)
    assert seen >= len(files) * 0.98, "%d of %d not identified" % (len(files) - seen, len(files))
    assert tiled == seen, "%d of %d did not tile" % (seen - tiled, seen)
