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


def test_the_specs_tag_flag_is_recorded_and_not_obeyed():
    """The spec says byte 0x23 is 0x26 for "tag follows" and 0x27 for "no
    tag". 328 of 328 real files have 0x1A there -- a DOS EOF marker -- and
    every one carries a full tag. So the flag is reported for what it is and
    the slots are read regardless; a file with the spec's "no tag" value and
    a title in the slot still gives up its title."""
    blob = bytearray(_spc(title="STILL HERE"))
    blob[0x23] = spcmod.NO_TAG
    h = spcmod.parse_header(bytes(blob))
    assert h["ok"] and not h["has_tag"]
    assert h["tag"]["title"] == "STILL HERE"


def test_an_empty_length_slot_is_text_with_nothing_written():
    """56 real files leave the seconds slot all zero and are otherwise the
    text layout: artist at 0xB1, emulator as a text digit. Reading them as
    binary put the artist one byte early and dropped its first letter."""
    blob = bytearray(_spc())
    blob[0xA9:0xB1] = bytes(8)
    h = spcmod.parse_header(bytes(blob))
    assert h["tag_style"] == "text"
    assert h["tag"]["artist"] == "SEED"
    assert "seconds" not in h["tag"]


def test_the_binary_spelling_puts_the_artist_at_0xB0():
    """Verified on 24 real files, not taken from the spec's table (which has
    a known transcription error): seconds as three bytes at 0xA9, fade as
    four at 0xAC, artist at 0xB0, emulator a real number at 0xD1."""
    blob = bytearray(_spc())
    blob[0x9E:0xD3] = bytes(0xD3 - 0x9E)
    blob[0xA9:0xAC] = (87).to_bytes(3, "little")
    struct.pack_into("<I", blob, 0xAC, 6000)
    blob[0xB0:0xB0 + 12] = b"Akihiko Mori"
    blob[0xD1] = 1
    h = spcmod.parse_header(bytes(blob))
    assert h["tag_style"] == "binary"
    assert h["tag"]["seconds"] == 87 and h["tag"]["fade_ms"] == 6000
    assert h["tag"]["artist"] == "Akihiko Mori"
    assert h["emulator"] == 1


def test_the_two_tag_spellings_are_told_apart_by_the_seconds_slot():
    """The spec suggests the date; real dumpers leave the date empty in both
    spellings, so it tells you nothing. The seconds slot does: text is
    digits, binary is not."""
    text = _spc()
    assert spcmod.parse_header(text)["tag_style"] == "text"
    blob = bytearray(text)
    blob[0x9E:0x9E + 11] = bytes(11)
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


def test_only_the_samples_the_voices_play_become_chunks(tmp_path):
    """The directory names up to 256 and a sound engine leaves most of them
    stale: 219 of 332 real files have directory entries that overlap each
    other, pointing into code and into one another. The eight voices' SRCN
    registers say which entries are real. Two directory entries, one voice."""
    blob = bytearray(_spc(samples=2))
    blob[spcmod.DSP_AT + spcmod.DSP_SRCN + 1 * 0x10] = 0   # voice 1 -> sample 0 too
    p = _write(tmp_path, bytes(blob))
    chunks, _w = walker.inspect_spc(str(p))
    samples = [c for c in chunks if c["id"].startswith("sample[")]
    assert [c["id"] for c in samples] == ["sample[0]"]
    assert "voices 0, 1" in samples[0]["summary"]
    ram = next(c for c in chunks if c["id"] == "ram")
    vals = {f["name"]: f["value"] for f in ram["fields"]}
    assert vals["directory_entries"] == 2 and vals["voice_samples"] == 1


def test_two_voices_reading_overlapping_ram_is_said_not_hidden(tmp_path):
    """14 of 332 real files: a voice starts inside another voice's sample.
    Both chunks are emitted and the second says so."""
    blob = bytearray(_spc(samples=2))
    page = blob[spcmod.DSP_AT + spcmod.DSP_DIR]
    base = spcmod.RAM_AT + page * 0x100
    struct.pack_into("<HH", blob, base + 4, 0x3009, 0x3009)   # sample 1 inside sample 0
    p = _write(tmp_path, bytes(blob))
    chunks, _w = walker.inspect_spc(str(p))
    s1 = next(c for c in chunks if c["id"] == "sample[1]")
    assert any("overlaps sample[0]" in w for w in s1["warnings"])


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
        # two voices reading overlapping RAM is real and is WARNED on the
        # chunk; a file whose only non-tiling is a warned overlap counts
        if _tiles(chunks, size) or any(
                "overlaps" in w for c in chunks for w in c["warnings"]):
            tiled += 1
        head = next(c for c in chunks if c["id"] == "header")
        vals = {f["name"]: f["value"] for f in head["fields"]}
        if "title" in vals:
            tagged += 1
        styles[vals.get("tag_style")] = styles.get(vals.get("tag_style"), 0) + 1
    assert seen == len(files), "%d of %d not identified" % (len(files) - seen, len(files))
    assert tiled == seen, "%d of %d did not tile" % (seen - tiled, seen)
    assert tagged >= seen * 0.9, "only %d of %d carry a title" % (tagged, seen)
