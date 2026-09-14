"""Tests for the Sharp X68000 PDX sample-bank walker.

PDX has no magic, no version, no count and no names -- it is a table of
big-endian offset/length pairs and then sample data. So identification is
arithmetic, the same as MDX's, and it is the thing most worth testing: the
first sample has to begin exactly where the table ends, and that is the only
statement the file makes about its own shape.

Verified against 3,418 real banks from the X68000 MDX Master Library, then
8,769 more from the MXDRV Complete archive (8,530 identified): 3,255
identified, none crashed, none produced untrustworthy geometry, and 3,244 of
them were accounted for byte for byte. Against 132,305 files of everything
else, zero false positives.
"""
import os
import struct

import pytest

from acidcat.core.formats import pdx as pdxmod
from acidcat.core.infra import sniff
from acidcat.core.walk import pdx as walker


def _pdx(samples=(64, 128, 32), banks=1, start_slot=0, alias=()):
    """A structurally valid PDX.

    `alias` is a list of (slot, sample index) pairs that point a second slot
    at an existing sample, which is what a real bank does when one hit is
    played from several numbers.
    """
    slots = banks * pdxmod.SLOTS_PER_BANK
    table = bytearray(slots * pdxmod.SLOT)
    pos = len(table)
    placed = []
    for i, length in enumerate(samples):
        slot = start_slot + i
        struct.pack_into(">II", table, slot * pdxmod.SLOT, pos, length)
        placed.append((pos, length))
        pos += length
    for slot, which in alias:
        off, length = placed[which]
        struct.pack_into(">II", table, slot * pdxmod.SLOT, off, length)
    return bytes(table) + b"\x88" * sum(samples)


def _write(tmp_path, blob, name="a.pdx"):
    p = tmp_path / name
    p.write_bytes(blob)
    return p


# ── identification, which is the whole problem ──────────────────────

def test_a_valid_pdx_is_identified(tmp_path):
    assert sniff.sniff(str(_write(tmp_path, _pdx()))) == "pdx"


def test_the_table_size_is_derived_from_the_first_sample():
    """Nothing declares the bank count. The first sample has to begin exactly
    where the table ends, so the smallest offset IS the table's size."""
    for banks in (1, 2, 4):
        blob = _pdx(banks=banks)
        h = pdxmod.parse_table(blob, len(blob))
        assert h["ok"], h["why"]
        assert h["banks"] == banks
        assert h["table_size"] == banks * pdxmod.BANK


def test_banks_stack_well_past_eight():
    """The first corpus topped out at 8 banks and the cap was set there. A
    second corpus twice its size had 9, 10, 12, 13 and 17 -- and rejected all
    22 of them as "not a whole number of banks", which was the cap talking,
    not the arithmetic. The format declares no ceiling."""
    for banks in (9, 13, 17):
        blob = _pdx(banks=banks)
        h = pdxmod.parse_table(blob, len(blob))
        assert h["ok"], "%d banks: %s" % (banks, h["why"])
        assert h["banks"] == banks


def test_a_first_sample_that_is_not_at_a_bank_boundary_is_rejected():
    """768 is the whole of the format's self-description. An offset that is
    not a whole number of banks past zero describes no table."""
    blob = bytearray(_pdx())
    struct.pack_into(">I", blob, 0, pdxmod.BANK + 16)
    h = pdxmod.parse_table(bytes(blob), len(blob))
    assert h["ok"] is False and "whole banks" in h["why"]


def test_a_slot_pointing_past_the_end_is_rejected():
    blob = bytearray(_pdx())
    struct.pack_into(">I", blob, 4, 1 << 30)
    h = pdxmod.parse_table(bytes(blob), len(blob))
    assert h["ok"] is False


def test_an_empty_table_is_not_a_bank():
    blob = bytes(pdxmod.BANK + 64)
    h = pdxmod.parse_table(blob, len(blob))
    assert h["ok"] is False and "empty" in h["why"]


@pytest.mark.parametrize("blob", [
    b"",
    b"RIFF" + b"\x00" * 2048,
    bytes(range(256)) * 8,
])
def test_things_that_are_not_pdx_are_not_identified(tmp_path, blob):
    assert sniff.sniff(str(_write(tmp_path, blob))) != "pdx"


# ── the slot number is the identity ─────────────────────────────────

def test_empty_slots_are_kept_because_the_row_number_is_the_sample_number():
    """A bank with one sample at slot 33 has 95 empty rows in front of it.
    Compacting them would renumber every sample and silently retune the tune
    that plays them."""
    blob = _pdx(samples=(64,), start_slot=33)
    h = pdxmod.parse_table(blob, len(blob))
    assert len(h["slots"]) == pdxmod.SLOTS_PER_BANK
    assert h["used"] == 1
    assert h["slots"][33][1] == 64
    assert h["slots"][0] == (0, 0)


def test_the_walker_names_the_slot_not_the_position(tmp_path):
    p = _write(tmp_path, _pdx(samples=(64,), start_slot=33))
    chunks, _warns = walker.inspect_pdx(str(p))
    sample = [c for c in chunks if c["id"].startswith("sample")][0]
    assert sample["id"] == "sample[33]"
    slot = {f["name"]: f["value"] for f in sample["fields"]}["slot"]
    assert slot == "33"


# ── aliasing, which is the walker's one real trap ───────────────────

def test_slots_that_share_a_sample_produce_one_chunk(tmp_path):
    """947 slot pairs across the corpus point at the same bytes, and every one
    of them is an exact duplicate rather than a window into another sample.
    A chunk per slot would claim those bytes twice and report a file bigger
    than it is."""
    p = _write(tmp_path, _pdx(samples=(64,), alias=[(5, 0), (9, 0)]))
    chunks, _warns = walker.inspect_pdx(str(p))
    samples = [c for c in chunks if c["id"].startswith("sample")]

    assert len(samples) == 1, "three slots, one region"
    assert {f["name"]: f["value"] for f in samples[0]["fields"]}["slot"] \
        == "0, 5, 9"
    assert sum(c["size"] for c in chunks) == p.stat().st_size


# ── the walk ────────────────────────────────────────────────────────

def test_the_walk_covers_every_byte(tmp_path):
    p = _write(tmp_path, _pdx())
    chunks, _warns = walker.inspect_pdx(str(p))
    assert sum(c["size"] for c in chunks) == p.stat().st_size
    assert chunks[0]["id"] == "table"
    assert chunks[0]["size"] == pdxmod.BANK


def test_a_trailing_byte_is_a_chunk_not_a_warning(tmp_path):
    """343 of 3,255 real banks end with one byte no slot reaches, 322 of them
    a NUL. That is a writer rounding up, not damage, so it is named and the
    file still tiles."""
    p = _write(tmp_path, _pdx() + b"\x00")
    chunks, warns = walker.inspect_pdx(str(p))
    tail = [c for c in chunks if c["id"] == "tail"]
    assert len(tail) == 1 and tail[0]["size"] == 1
    assert sum(c["size"] for c in chunks) == p.stat().st_size
    assert not warns


def test_a_long_unreached_tail_does_warn(tmp_path):
    p = _write(tmp_path, _pdx() + b"\x00" * 512)
    _chunks, warns = walker.inspect_pdx(str(p))
    assert any("reached by no slot" in w for w in warns)


@pytest.mark.parametrize("n", [0, 8, 100, 767, 768, 800])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    p = _write(tmp_path, _pdx()[:n])
    try:
        walker.inspect_pdx(str(p))
    except Exception as exc:                      # noqa: BLE001 -- that is the test
        pytest.fail("truncation at %d raised %r" % (n, exc))


def test_a_packed_bank_says_so(tmp_path):
    """54 real banks were compressed after they were written. Nothing readable
    survives -- a PDX is all table -- so the walker is only reachable by
    forcing the format, and what it can honestly say is what was done."""
    blob = b"\x60\x26\x60\x4a" + b"LZX 0.42" + bytes(2048)
    p = _write(tmp_path, blob)
    assert sniff.sniff(str(p)) != "pdx", \
        "nothing in a packed bank proves it is a bank; do not claim it"

    chunks, _warns = walker.inspect_pdx(str(p))
    assert chunks[0]["summary"].endswith("LZX 0.42")
    assert sum(c["size"] for c in chunks) == p.stat().st_size


def test_pdx_is_a_known_format():
    from acidcat.core.walk import _WALKERS
    assert "pdx" in sniff.KNOWN_FORMATS
    assert "pdx" in _WALKERS


# ── opt-in: the real corpus ─────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("ACIDCAT_MDX_CORPUS"),
                    reason="set ACIDCAT_MDX_CORPUS to a dir with real .pdx banks")
def test_real_corpus_walks_completely():
    """Measured over 3,418 banks: 3,255 identified, zero crashes, zero
    untrustworthy geometry. Eleven have an interior region no slot reaches,
    which is why the coverage floor is not 100%."""
    import glob
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file

    root = os.environ["ACIDCAT_MDX_CORPUS"]
    files = sorted(set(glob.glob(os.path.join(root, "**", "*.PDX"), recursive=True)
                       + glob.glob(os.path.join(root, "**", "*.pdx"),
                                   recursive=True)))
    seen = covered_fully = 0
    for path in files:
        if sniff.sniff(path) != "pdx":
            continue
        seen += 1
        _label, chunks, _warns = walk_file(path)
        size = os.path.getsize(path)
        geometry.normalize(chunks, size)
        assert all(geometry.is_trustworthy(c) for c in chunks), path
        covered = sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                      for c in chunks)
        covered_fully += (covered == size)
    assert seen >= 100, "only %d banks identified; that tests almost nothing" % seen
    assert covered_fully >= seen - 20, (
        "%d of %d banks were not fully accounted for" % (seen - covered_fully, seen))
