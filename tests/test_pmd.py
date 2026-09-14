"""Tests for the PC-98 Professional Music Driver walker.

A PMD file is the compiled form of an MML score, and the score's own
directives -- #Title, #Composer, #Arranger, #PCMFile -- survive in it as a
memo table. The layout comes from the driver source and its author's API
documentation, and the three things a reader gets wrong are pinned here: the
offsets count from byte 1, the part table is twelve words not eleven, and the
memo anchor is the four bytes before the tone block rather than a region.

Verified against 1,515 real files from Modland's PMD archive and the MXDRV
Complete set, plus the pmdmini specimen: all identified, zero crashes, zero
untrustworthy geometry, every one tiled to the byte. Zero of 146,564 files
of everything else pass even the three-byte identification.
"""
import os
import struct

import pytest

from acidcat.core.formats import pmd as pmdmod
from acidcat.core.infra import sniff
from acidcat.core.walk import pmd as walker

import seeds


def _pmd(**kw):
    return seeds.SEEDS["pmd"][0](**kw)


def _write(tmp_path, blob, name="a.m"):
    p = tmp_path / name
    p.write_bytes(blob)
    return p


# ── identification is the driver's own three-byte test ──────────────

def test_the_drivers_three_byte_test_is_the_definition():
    """pmdmini refuses to play anything that fails this, so it is what a PMD
    file IS: byte 0 at most 0x0F, byte 1 either 0x1A or 0x18, byte 2 either
    0 or 0xE6."""
    assert pmdmod.is_pmd(bytes([0x00, 0x1A, 0x00]))
    assert pmdmod.is_pmd(bytes([0x01, 0x1A, 0x00]))
    assert pmdmod.is_pmd(bytes([0x0F, 0x18, 0xE6]))
    assert not pmdmod.is_pmd(bytes([0x10, 0x1A, 0x00]))
    assert not pmdmod.is_pmd(bytes([0x00, 0x1B, 0x00]))
    assert not pmdmod.is_pmd(bytes([0x00, 0x1A, 0x01]))
    assert not pmdmod.is_pmd(b"")


def test_a_valid_file_is_identified_by_extension_and_content(tmp_path):
    assert sniff.sniff(str(_write(tmp_path, _pmd()))) == "pmd"


def test_the_three_byte_test_alone_does_not_identify(tmp_path):
    """Three bytes with no magic in them pass one file in a few thousand by
    chance, and this tree walks millions. The content test is gated on the
    extensions the compiler writes."""
    assert sniff.sniff(str(_write(tmp_path, _pmd(), "a.bin"))) != "pmd"


# ── the three things a reader gets wrong ────────────────────────────

def test_offsets_count_from_byte_one():
    """The driver points its buffer at data+1 and never looks back. A reader
    that forgets is one byte out everywhere, and the numbers still look
    plausible."""
    blob = _pmd()
    h = pmdmod.parse(blob)
    assert h["ok"]
    first = h["parts"][0]["offset"]
    assert first == pmdmod.STREAM_START
    # the stream is at file offset first+1, and it opens with a command byte
    assert blob[1 + first] == 0xF6


def test_the_part_table_is_twelve_words():
    """Eleven parts and then the rhythm address table. Counting eleven puts
    the tone offset two bytes early and leaves a two-byte hole in front of
    every first part -- which is how 683 of 683 real files caught it."""
    assert pmdmod.RHYTHM_TABLE_AT == 22
    assert pmdmod.TONE_OFFSET_AT == 24
    assert pmdmod.STREAM_START == 26
    h = pmdmod.parse(_pmd())
    assert h["rhythm_table_at"] is not None


def test_the_memo_anchor_is_the_four_bytes_before_the_tones(tmp_path):
    """Pointer word, tag, FE -- found by the driver as tone-4. It is not a
    region of its own; the memo TEXT it points at is."""
    blob = _pmd()
    h = pmdmod.parse(blob)
    assert h["memo_at"] == h["tone_at"] - 4
    assert blob[1 + h["memo_at"] + 3] == pmdmod.MEMO_END


# ── the memo table ──────────────────────────────────────────────────

def test_title_and_composer_come_out_of_the_memo():
    h = pmdmod.parse(_pmd(title="TEST TUNE", composer="SOMEONE"))
    assert h["memo"]["title"] == "TEST TUNE"
    assert h["memo"]["composer"] == "SOMEONE"


def test_a_slash_means_a_directive_was_not_written():
    """The compiler writes '/' for a directive the MML did not set. The seed
    leaves #PCMFile and #Arranger unset that way."""
    h = pmdmod.parse(_pmd())
    assert "pcm_file" not in h["memo"]
    assert "arranger" not in h["memo"]


def test_the_tag_byte_says_which_sample_bank_slots_exist():
    """From _getmemo's arithmetic: PCM is always first; 0x42 puts a PPS slot
    in front of it; 0x48 puts a PPZ slot in front of both. Reproduced, not
    read, because the slot order is what decides whether "ZUN" lands in
    composer or in arranger."""
    blob = bytearray(_pmd())
    h = pmdmod.parse(bytes(blob))
    assert h["memo_tag"] == 0x40
    # at 0x40 the table is [pcm, title, composer, arranger]: title is slot 1
    assert h["memo"]["title"] == "SEED"


def test_a_file_with_no_memo_block_is_still_a_file(tmp_path):
    """Thirty-three of 813 real files have none. That is a tune, not damage."""
    blob = bytearray(_pmd())
    h = pmdmod.parse(bytes(blob))
    # at tag 0x40 the driver does not even check the FE; a tag below 0x40 is
    # what "no memo here" looks like
    blob[1 + h["memo_at"] + 2] = 0x00
    h2 = pmdmod.parse(bytes(blob))
    assert h2["ok"]
    assert h2["memo"] == {}
    p = _write(tmp_path, bytes(blob))
    _chunks, warns = walker.inspect_pmd(str(p))
    assert not any("memo" in w for w in warns)


# ── the walk ────────────────────────────────────────────────────────

def test_the_walk_tiles_the_file(tmp_path):
    p = _write(tmp_path, _pmd())
    chunks, _warns = walker.inspect_pmd(str(p))
    end = 0
    for c in sorted(chunks, key=lambda c: c["offset"]):
        assert c["offset"] == end, "gap or overlap before %s" % c["id"]
        end = c["offset"] + c["extent_len"]
    assert end == p.stat().st_size


def test_every_part_is_a_chunk_and_names_its_kind(tmp_path):
    p = _write(tmp_path, _pmd())
    chunks, _warns = walker.inspect_pmd(str(p))
    parts = [c for c in chunks if c["id"].startswith("part[")]
    assert len(parts) == pmdmod.PARTS
    kinds = [c["summary"].split()[0] for c in parts]
    assert kinds == ["FM"] * 6 + ["SSG"] * 3 + ["ADPCM", "rhythm"]


def test_an_empty_rhythm_table_is_not_a_region(tmp_path):
    """A tune with no rhythm patterns points its table at the tone block.
    Emitting a region for it claims the tones' bytes twice; 33 real files."""
    blob = bytearray(_pmd())
    h = pmdmod.parse(bytes(blob))
    struct.pack_into("<H", blob, 1 + pmdmod.RHYTHM_TABLE_AT, h["tone_at"])
    p = _write(tmp_path, bytes(blob))
    chunks, _warns = walker.inspect_pmd(str(p))
    assert not any(c["id"] == "rhythm_table" for c in chunks)


def test_no_embedded_instruments_is_named(tmp_path):
    """MC.EXE without /V writes no tones, and then the tone offset is the
    table size. The driver needs a .FF file, and the walk says so."""
    blob = bytearray(_pmd())
    struct.pack_into("<H", blob, 1 + pmdmod.TONE_OFFSET_AT, pmdmod.STREAM_START)
    h = pmdmod.parse(bytes(blob))
    assert h["has_tones"] is False


@pytest.mark.parametrize("n", [0, 3, 12, 26, 27, 60, 90])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    p = _write(tmp_path, _pmd()[:n])
    try:
        walker.inspect_pmd(str(p))
    except Exception as exc:                  # noqa: BLE001 -- that is the test
        from acidcat.core.walk.base import Unsupported
        if not isinstance(exc, Unsupported):
            pytest.fail("truncation at %d raised %r" % (n, exc))


def test_pmd_is_a_known_format():
    from acidcat.core.walk import _WALKERS
    assert "pmd" in sniff.KNOWN_FORMATS
    assert "pmd" in _WALKERS


# ── opt-in: the real corpus ─────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("ACIDCAT_PMD_CORPUS"),
                    reason="set ACIDCAT_PMD_CORPUS to a dir of real .m files")
def test_real_corpus_walks_completely():
    """Measured over 1,515 files: all identified, zero crashes, zero
    untrustworthy geometry, every one tiled to the byte."""
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file

    root = os.environ["ACIDCAT_PMD_CORPUS"]
    files = [os.path.join(r, f) for r, _d, fn in os.walk(root) for f in fn
             if f.lower().endswith((".m", ".m2"))]
    assert len(files) >= 100, "only %d files" % len(files)
    seen = tiled = titled = 0
    for path in files:
        if sniff.sniff(path) != "pmd":
            continue
        seen += 1
        _label, chunks, _warns = walk_file(path)
        size = os.path.getsize(path)
        geometry.normalize(chunks, size)
        assert all(geometry.is_trustworthy(c) for c in chunks), path
        end = 0
        ok = True
        for c in sorted(chunks, key=lambda c: c["offset"]):
            if c["offset"] != end:
                ok = False
                break
            end = c["offset"] + c["extent_len"]
        tiled += ok and end == size
        titled += any(c["id"] == "memo" for c in chunks)
    assert seen >= len(files) - 5, "%d of %d not identified" % (len(files) - seen, len(files))
    assert tiled == seen, "%d of %d did not tile" % (seen - tiled, seen)
    assert titled >= seen * 0.9, "only %d of %d carry a memo" % (titled, seen)
