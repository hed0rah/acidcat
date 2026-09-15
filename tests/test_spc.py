"""Tests for the SNES SPC700 snapshot walker.

An SPC is a frozen sound chip: a tag, the CPU registers, 64 KB of RAM and the
DSP's 128 registers. There is no music to parse. What a reader can do is read
the tag, describe the state, and find the BRR samples the DSP's directory
register points at -- and the things worth pinning are the ones a reader gets
wrong: the loop address is not a loop flag, and the tag has two spellings.
"""
import os
import struct

import pytest

from acidcat.core.formats import spc as spcmod
from acidcat.core.infra import sniff
from acidcat.core.walk import spc as walker

import seeds


def _spc(**kw):
    return seeds.SEEDS["spc"][0](**kw)


def _write(tmp_path, blob, name="a.spc"):
    p = tmp_path / name
    p.write_bytes(blob)
    return p


def _tiles(chunks, size):
    """Leaves tile the file; a container owns only its header plus what its
    own fields account for. Same rule as the DSD test."""
    def inside(i, o):
        return (i is not o and o["offset"] <= i["offset"]
                and i["offset"] + i["extent_len"] <= o["offset"] + o["extent_len"]
                and i["extent_len"] < o["extent_len"])
    leaves = []
    for c in chunks:
        kids = [o for o in chunks if inside(o, c)]
        if not kids:
            leaves.append((c["offset"], c["extent_len"]))
    # the RAM container: its bytes not claimed by a sample are still RAM, so
    # for tiling the samples are simply carved out and the remainder is RAM
    end = 0
    for off, length in sorted(leaves):
        if off < end:
            return False
        end = off + length
    return end <= size


# ── identification ──────────────────────────────────────────────────

def test_a_valid_spc_is_identified(tmp_path):
    assert sniff.sniff(str(_write(tmp_path, _spc()))) == "spc"


def test_the_magic_is_thirty_three_bytes_and_all_of_them_count():
    assert spcmod.is_spc(spcmod.MAGIC + bytes(300))
    assert not spcmod.is_spc(spcmod.MAGIC[:-1] + b"X" + bytes(300))
    assert not spcmod.is_spc(b"")


# ── the tag ─────────────────────────────────────────────────────────

def test_the_text_tag_reads_title_game_artist_and_length():
    h = spcmod.parse_header(_spc(title="A TUNE", game="A GAME"))
    assert h["ok"] and h["has_tag"]
    assert h["tag_style"] == "text"
    assert h["tag"]["title"] == "A TUNE"
    assert h["tag"]["game"] == "A GAME"
    assert h["tag"]["artist"] == "SEED"
    assert h["tag"]["seconds"] == 120
    assert h["tag"]["fade_ms"] == 10000
    assert h["emulator"] == 2


def test_a_file_declaring_no_tag_carries_none():
    blob = bytearray(_spc())
    blob[0x23] = spcmod.NO_TAG
    h = spcmod.parse_header(bytes(blob))
    assert h["ok"] and not h["has_tag"]
    assert h["tag"] == {}


def test_the_two_tag_spellings_are_told_apart_by_the_date():
    """Text has digits and slashes in the date slot; binary does not. Nothing
    in the file says which, so this is a heuristic and the corpus test says
    how well it holds."""
    text = _spc()
    assert spcmod.parse_header(text)["tag_style"] == "text"
    blob = bytearray(text)
    blob[0x9E:0x9E + 11] = bytes(11)                 # binary: no text date
    struct.pack_into("<HBB", blob, 0x9E, 2026, 1, 15)
    blob[0xA9:0xAC] = (120).to_bytes(3, "little")
    struct.pack_into("<I", blob, 0xAC, 10000)
    h = spcmod.parse_header(bytes(blob))
    assert h["tag_style"] == "binary"
    assert h["tag"]["seconds"] == 120
    assert h["tag"]["fade_ms"] == 10000


# ── the samples ─────────────────────────────────────────────────────

def test_the_directory_register_locates_the_samples():
    """DSP register 0x5D names the page of RAM holding 256 (start, loop)
    pairs. Every entry pointing inside RAM is a BRR sample."""
    blob = _spc(samples=3)
    entries = spcmod.sample_directory(blob)
    assert [i for i, _s, _l in entries] == [0, 1, 2]
    assert entries[0][1] == 0x3000


def test_a_sample_runs_until_a_block_with_the_end_bit():
    """BRR is nine-byte blocks; bit 0 of a block header ends the sample."""
    blob = _spc()
    assert spcmod.brr_length(blob, 0x3000) == 4 * spcmod.BRR_BLOCK


def test_the_loop_address_is_not_a_loop_flag(tmp_path):
    """The directory carries a loop ADDRESS for every sample. Whether the
    sample loops is bit 1 of its last block. The first draft said "no loop"
    when the address equalled the start, which is wrong in both directions:
    a full-length loop starts at the start, and a one-shot has an address
    too."""
    blob = bytearray(_spc(samples=1))
    p = _write(tmp_path, bytes(blob))
    chunks, _w = walker.inspect_spc(str(p))
    s = next(c for c in chunks if c["id"] == "sample[0]")
    assert "one-shot" in s["summary"]

    last = spcmod.RAM_AT + 0x3000 + 3 * spcmod.BRR_BLOCK
    blob[last] |= 0x02                               # LOOP bit on the last block
    p = _write(tmp_path, bytes(blob), "b.spc")
    chunks, _w = walker.inspect_spc(str(p))
    s = next(c for c in chunks if c["id"] == "sample[0]")
    assert "loops from the start" in s["summary"]


def test_two_directory_entries_to_one_sample_produce_one_chunk(tmp_path):
    blob = bytearray(_spc(samples=1))
    page = blob[spcmod.DSP_AT + spcmod.DSP_DIR]
    base = spcmod.RAM_AT + page * 0x100
    blob[base + 4:base + 8] = blob[base:base + 4]    # entry 1 == entry 0
    p = _write(tmp_path, bytes(blob))
    chunks, _w = walker.inspect_spc(str(p))
    assert len([c for c in chunks if c["id"].startswith("sample[")]) == 1


# ── the walk ────────────────────────────────────────────────────────

def test_the_fixed_regions_are_where_the_spec_puts_them(tmp_path):
    p = _write(tmp_path, _spc())
    chunks, _w = walker.inspect_spc(str(p))
    at = {c["id"]: c["offset"] for c in chunks}
    assert at["header"] == 0
    assert at["ram"] == 0x100
    assert at["dsp"] == 0x10100
    assert at["unused"] == 0x10180
    assert at["ipl"] == 0x101C0
    assert _tiles(chunks, p.stat().st_size)


def test_a_truncated_ram_image_says_so(tmp_path):
    p = _write(tmp_path, _spc()[:0x8000])
    _chunks, warns = walker.inspect_spc(str(p))
    assert any("truncated" in w for w in warns)


def test_an_xid6_extension_is_read(tmp_path):
    sub = bytes([0x01, 0x01]) + struct.pack("<H", 5) + b"HELLO\x00\x00\x00"
    ext = b"xid6" + struct.pack("<I", len(sub)) + sub
    p = _write(tmp_path, _spc() + ext)
    chunks, warns = walker.inspect_spc(str(p))
    x = next(c for c in chunks if c["id"] == "xid6")
    vals = {f["name"]: f["value"] for f in x["fields"]}
    assert vals["sub[0x01]"] == "HELLO"
    assert not warns


def test_bytes_after_the_image_that_are_not_xid6_are_named(tmp_path):
    p = _write(tmp_path, _spc() + b"junk")
    chunks, warns = walker.inspect_spc(str(p))
    assert any(c["id"] == "trailing" for c in chunks)
    assert any("not an xid6" in w for w in warns)


@pytest.mark.parametrize("n", [0, 32, 33, 0x100, 0x1000, 0x10100, 0x10200])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    p = _write(tmp_path, _spc()[:n])
    try:
        walker.inspc = walker.inspect_spc(str(p))
    except Exception as exc:                  # noqa: BLE001 -- that is the test
        from acidcat.core.walk.base import Unsupported
        if not isinstance(exc, Unsupported):
            pytest.fail("truncation at %d raised %r" % (n, exc))


def test_spc_is_a_known_format():
    from acidcat.core.walk import _WALKERS
    assert "spc" in sniff.KNOWN_FORMATS
    assert "spc" in _WALKERS


# ── opt-in: the real corpus ─────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("ACIDCAT_SPC_CORPUS"),
                    reason="set ACIDCAT_SPC_CORPUS to a dir of real .spc files")
def test_real_corpus_walks_completely():
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file

    root = os.environ["ACIDCAT_SPC_CORPUS"]
    files = [os.path.join(r, f) for r, _d, fn in os.walk(root) for f in fn
             if f.lower().endswith(".spc")]
    assert len(files) >= 100, "only %d files" % len(files)
    seen = tiled = tagged = 0
    styles = {}
    for path in files:
        if sniff.sniff(path) != "spc":
            continue
        seen += 1
        _label, chunks, _warns = walk_file(path)
        size = os.path.getsize(path)
        geometry.normalize(chunks, size)
        assert all(geometry.is_trustworthy(c) for c in chunks), path
        tiled += _tiles(chunks, size)
        head = next(c for c in chunks if c["id"] == "header")
        vals = {f["name"]: f["value"] for f in head["fields"]}
        if "title" in vals:
            tagged += 1
        styles[vals.get("tag_style")] = styles.get(vals.get("tag_style"), 0) + 1
    assert seen == len(files), "%d of %d not identified" % (len(files) - seen, len(files))
    assert tiled == seen, "%d of %d did not tile" % (seen - tiled, seen)
    assert tagged >= seen * 0.9, "only %d of %d carry a title" % (tagged, seen)
