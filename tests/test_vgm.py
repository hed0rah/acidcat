"""VGM: a register log with a header of chip clocks and a GD3 tag.

Built from the vgmrips spec and checked on Modland's Video Game Music
directory: the stream walk lands on the GD3, the GD3 ends at EOF, every
loop points inside the stream, and the waits add up to the header's
sample count on all but a handful.
"""

import gzip
import os
import struct

import pytest

from acidcat.core.formats import vgm as vgmmod
from acidcat.core.infra import sniff
from acidcat.core.walk import vgm as walker

import seeds


def _vgm(**kw):
    return seeds.SEEDS["vgm"][0](**kw)


def _write(tmp_path, blob, name="a.vgm"):
    p = tmp_path / name
    p.write_bytes(blob)
    return p


def _tiles(chunks, size):
    """Leaf chunks tile the file; a container (the command stream around
    its data blocks) owns its header only."""
    def inside(a, b):
        return (a is not b and b["offset"] <= a["offset"]
                and a["offset"] + a["size"] <= b["offset"] + b["size"]
                and a["size"] < b["size"])
    spans = []
    for c in chunks:
        kids = [o for o in chunks if inside(o, c)]
        if not kids:
            spans.append((c["offset"], c["size"]))
    spans.sort()
    pos = 0
    for off, n in spans:
        if off != pos:
            return False
        pos += n
    return pos == size


# ── identification ──────────────────────────────────────────────────

def test_a_vgm_is_identified_by_its_magic(tmp_path):
    assert sniff.sniff(str(_write(tmp_path, _vgm()))) == "vgm"


def test_a_vgz_is_identified_by_the_magic_inside_the_gzip(tmp_path):
    p = _write(tmp_path, gzip.compress(_vgm()), "a.vgz")
    assert sniff.sniff(str(p)) == "vgm"
    q = _write(tmp_path, gzip.compress(b"not a vgm at all"), "b.vgz")
    assert sniff.sniff(str(q)) != "vgm"


def test_vgm_is_a_known_format():
    assert "vgm" in sniff.KNOWN_FORMATS


# ── the header ──────────────────────────────────────────────────────

def test_the_version_is_bcd():
    assert vgmmod.version_text(0x00000151) == "1.51"
    assert vgmmod.version_text(0x00000101) == "1.01"
    assert vgmmod.version_text(0x00000172) == "1.72"


def test_offsets_are_relative_to_their_own_field():
    h = vgmmod.parse_header(_vgm())
    raw = _vgm()
    assert h["eof"] == 0x04 + struct.unpack_from("<I", raw, 0x04)[0] == len(raw)
    assert h["gd3_at"] == 0x14 + struct.unpack_from("<I", raw, 0x14)[0]
    assert h["loop_at"] == 0x1C + struct.unpack_from("<I", raw, 0x1C)[0]


def test_a_chip_is_present_when_its_clock_is_non_zero():
    h = vgmmod.parse_header(_vgm(chips={"SN76489": 3579545, "YM2612": 7670454}))
    assert [(c["name"], c["clock"]) for c in h["chips"]] == [
        ("SN76489", 3579545), ("YM2612", 7670454)]


def test_a_clock_field_the_version_predates_is_not_read():
    """1.01 has no YM2612 field; bytes at 0x2C are whatever the writer left.
    Reading them as a clock would invent a chip."""
    blob = bytearray(_vgm(version=0x101, chips={"SN76489": 3579545}))
    struct.pack_into("<I", blob, 0x2C, 7670454)
    h = vgmmod.parse_header(bytes(blob))
    assert [c["name"] for c in h["chips"]] == ["SN76489"]


def test_the_dual_chip_bit_is_read_from_1_51():
    h = vgmmod.parse_header(_vgm(version=0x151, chips={"SN76489": 3579545 | 0x40000000}))
    assert h["chips"][0]["dual"] and h["chips"][0]["clock"] == 3579545
    h = vgmmod.parse_header(_vgm(version=0x150, chips={"SN76489": 3579545 | 0x40000000}))
    assert not h["chips"][0]["dual"]


def test_the_data_offset_is_0x40_before_1_50_and_a_field_after():
    assert vgmmod.parse_header(_vgm(version=0x101))["data_at"] == 0x40
    assert vgmmod.parse_header(_vgm(version=0x151))["data_at"] == 0x80


# ── the stream ──────────────────────────────────────────────────────

def test_the_stream_is_walked_to_its_end_marker_and_waits_are_summed():
    raw = _vgm()
    h = vgmmod.parse_header(raw)
    w = vgmmod.walk_commands(raw, h["data_at"], h["gd3_at"], 10 ** 6)
    assert w["ended"] and w["end"] == h["gd3_at"]
    assert w["waits"] == h["total_samples"]
    assert w["writes"] == {"SN76489": 4}


def test_reserved_opcode_ranges_have_the_lengths_the_spec_fixes():
    # 0x30-0x3F one operand, 0xC0-0xDF three, 0xE1-0xFF four
    stream = bytes([0x3A, 1, 0xC9, 1, 2, 3, 0xF0, 1, 2, 3, 4, 0x66])
    w = vgmmod.walk_commands(stream, 0, len(stream), 100)
    assert w["ended"] and w["commands"] == 4 and w["end"] == len(stream)


def test_a_data_block_is_skipped_by_its_length_and_recorded():
    block = bytes([0x67, 0x66, 0x00]) + struct.pack("<I", 5) + b"ABCDE"
    stream = block + bytes([0x62, 0x66])
    w = vgmmod.walk_commands(stream, 0, len(stream), 100)
    assert w["blocks"] == [(7, 0x00, 5)] and w["ended"] and w["waits"] == 735


def test_0x64_redefines_a_wait_command():
    """1.70: 0x64 cc nn nn makes every following 0x62 (or 0x63) wait nn
    samples. Two Game Gear rips on Modland use it."""
    stream = bytes([0x62, 0x64, 0x62, 0x10, 0x00, 0x62, 0x63, 0x66])
    w = vgmmod.walk_commands(stream, 0, len(stream), 100)
    assert w["ended"] and w["waits"] == 735 + 16 + 882


def test_an_unknown_opcode_stops_the_walk_and_says_where():
    stream = bytes([0x62, 0x69, 0x66])           # 0x69 is not defined
    w = vgmmod.walk_commands(stream, 0, len(stream), 100)
    assert not w["ended"] and "0x69" in w["why"]


def test_a_stream_without_an_end_marker_does_not_run_past_end():
    stream = bytes([0x50, 0x9F, 0x61])           # the wait's operands are missing
    w = vgmmod.walk_commands(stream, 0, len(stream), 100)
    assert not w["ended"] and w["end"] == len(stream)


# ── the tag ─────────────────────────────────────────────────────────

def test_gd3_is_eleven_utf16_strings():
    raw = _vgm(title="Green Hill Zone", game="Sonic")
    h = vgmmod.parse_header(raw)
    g = vgmmod.parse_gd3(raw, h["gd3_at"])
    assert g["ok"] and g["fields"]["title"] == "Green Hill Zone"
    assert g["fields"]["game"] == "Sonic" and "title_jp" not in g["fields"]
    assert h["gd3_at"] + g["size"] == len(raw)


# ── the walker ──────────────────────────────────────────────────────

def test_a_vgm_walks_into_header_stream_and_tag_that_tile(tmp_path):
    p = _write(tmp_path, _vgm())
    chunks, warns = walker.inspect_vgm(str(p))
    assert [c["id"] for c in chunks] == ["header", "commands", "Gd3"]
    assert _tiles(chunks, os.path.getsize(p))
    assert not warns
    head = chunks[0]
    assert "SN76489" in head["summary"] and "SEED" in head["summary"]
    assert any(f["name"] == "SN76489" and "MHz" in f["value"] for f in head["fields"])


def test_data_blocks_become_chunks_inside_the_stream(tmp_path):
    p = _write(tmp_path, _vgm(pcm=b"\x80" * 64))
    chunks, warns = walker.inspect_vgm(str(p))
    blk = next(c for c in chunks if c["id"] == "block[0]")
    assert blk["size"] == 7 + 64 and "YM2612 PCM" in blk["summary"]
    # the block opens the stream, so the commands run once, after it
    assert [c["id"] for c in chunks] == ["header", "block[0]", "commands", "Gd3"]
    assert _tiles(chunks, os.path.getsize(p))
    assert not warns


def test_a_vgz_is_one_chunk_with_the_same_facts(tmp_path):
    p = _write(tmp_path, gzip.compress(_vgm(title="Packed")), "a.vgz")
    chunks, warns = walker.inspect_vgm(str(p))
    assert len(chunks) == 1 and chunks[0]["id"] == "VGZ"
    assert chunks[0]["size"] == os.path.getsize(p)
    names = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert names["container"] == "gzip" and names["title"] == "Packed"
    assert all(f["off"] is None for f in chunks[0]["fields"])
    assert not warns


def test_the_header_and_the_waits_disagreeing_is_said(tmp_path):
    blob = bytearray(_vgm())
    struct.pack_into("<I", blob, 0x18, 12345)
    _chunks, warns = walker.inspect_vgm(str(_write(tmp_path, bytes(blob))))
    assert any("waits add up" in w for w in warns)


def test_writes_to_a_chip_with_no_clock_are_said(tmp_path):
    blob = bytearray(_vgm())
    struct.pack_into("<I", blob, 0x0C, 0)                     # SN76489 clock off
    _chunks, warns = walker.inspect_vgm(str(_write(tmp_path, bytes(blob))))
    assert any("whose clock is zero" in w for w in warns)


def test_a_loop_outside_the_stream_is_said(tmp_path):
    blob = bytearray(_vgm())
    struct.pack_into("<I", blob, 0x1C, 1)                     # loop at 0x1D
    _chunks, warns = walker.inspect_vgm(str(_write(tmp_path, bytes(blob))))
    assert any("loop point" in w for w in warns)


@pytest.mark.parametrize("n", [3, 0x20, 0x40, 0x48, 0x60])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    from acidcat.core.walk.base import Unsupported
    p = _write(tmp_path, _vgm()[:n])
    try:
        walker.inspect_vgm(str(p))
    except Unsupported:
        pass


# ── the real thing ──────────────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("ACIDCAT_VGM_CORPUS"),
                    reason="set ACIDCAT_VGM_CORPUS to a dir of real .vgm/.vgz files")
def test_real_corpus_walks_completely():
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file
    root = os.environ["ACIDCAT_VGM_CORPUS"]
    files = [os.path.join(r, f) for r, _d, fn in os.walk(root) for f in fn
             if f.lower().endswith((".vgm", ".vgz"))]
    assert len(files) >= 50
    seen = clean = 0
    for path in files:
        if sniff.sniff(path) != "vgm":
            continue
        seen += 1
        _label, chunks, warns = walk_file(path)
        geometry.normalize(chunks, os.path.getsize(path))
        assert all(geometry.is_trustworthy(c) for c in chunks), path
        clean += not warns
    # one modland file named .vgz is a PSGMOD module, not gzip and not a VGM
    assert seen >= len(files) - 1, "%d of %d not identified" % (len(files) - seen, len(files))
    assert clean >= seen * 0.95, "%d of %d carry warnings" % (seen - clean, seen)
