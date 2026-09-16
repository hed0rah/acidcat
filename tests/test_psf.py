"""Tests for the Portable Sound Format walker.

One container, eight machines: PSF1, PSF2, SSF, DSF, USF, GSF, SNSF, QSF. The
container is the same for all of them and the program inside is not, so the
walker proves what the container proves -- size, checksum, that the program
inflates -- and decodes the program's own header only on GSF, where it is
documented and was verified on real files.

Verified on 691 GSF files from Modland: all identified, every CRC32 matching,
every program inflating, every file tiled to the byte, 669 minis and 22
libraries told apart correctly.
"""
import os
import struct
import zlib

import pytest

from acidcat.core.formats import psf as psfmod
from acidcat.core.infra import sniff
from acidcat.core.walk import psf as walker

import seeds


def _psf(**kw):
    return seeds.SEEDS["psf"][0](**kw)


def _write(tmp_path, blob, name="a.minigsf"):
    p = tmp_path / name
    p.write_bytes(blob)
    return p


# ── the container ───────────────────────────────────────────────────

def test_a_valid_psf_is_identified(tmp_path):
    assert sniff.sniff(str(_write(tmp_path, _psf()))) == "psf"


def test_identification_needs_a_known_version_byte(tmp_path):
    """"PSF" opens text too. The version byte has to name a machine."""
    blob = bytearray(_psf())
    blob[3] = 0x99
    assert sniff.sniff(str(_write(tmp_path, bytes(blob)))) != "psf"


@pytest.mark.parametrize("version,short", [
    (0x01, "PSF1"), (0x02, "PSF2"), (0x11, "SSF"), (0x12, "DSF"),
    (0x21, "USF"), (0x22, "GSF"), (0x23, "SNSF"), (0x24, "2SF"), (0x25, "NCSF"),
    (0x41, "QSF")])
def test_every_machine_is_named(tmp_path, version, short):
    h = psfmod.parse(_psf(version=version, rom=b"\x00" * 16), 10 ** 6)
    assert h["ok"] and h["platform"][0] == short


def test_the_crc_is_checked_and_a_mismatch_is_said(tmp_path):
    """Most formats give a reader nothing to check against. PSF hands over a
    CRC32 of the program, so "intact" is a fact and not a guess. 691 of 691
    real files match theirs."""
    good = _psf()
    assert psfmod.parse(good, len(good))["crc_ok"] is True
    blob = bytearray(good)
    blob[psfmod.HEADER + 3] ^= 0xFF                  # one byte of the program
    p = _write(tmp_path, bytes(blob))
    _chunks, warns = walker.inspect_psf(str(p))
    assert any("CRC32 does not match" in w for w in warns)


def test_the_program_inflates_and_a_bad_one_is_said(tmp_path):
    blob = bytearray(_psf())
    at = psfmod.HEADER
    n = struct.unpack_from("<I", blob, 8)[0]
    blob[at:at + n] = b"\x00" * n                     # not zlib
    struct.pack_into("<I", blob, 12, zlib.crc32(bytes(blob[at:at + n])) & 0xFFFFFFFF)
    p = _write(tmp_path, bytes(blob))
    _chunks, warns = walker.inspect_psf(str(p))
    assert any("does not inflate" in w for w in warns)


def test_a_program_running_past_the_file_is_reported(tmp_path):
    blob = bytearray(_psf())
    struct.pack_into("<I", blob, 8, 0x7FFFFFFF)
    h = psfmod.parse(bytes(blob), len(blob))
    assert h["ok"] is False and "past the end" in h["why"]


# ── minis and libraries ─────────────────────────────────────────────

def test_a_mini_is_a_few_bytes_and_a_library_reference():
    """A soundtrack shares one engine and sample set. Each mini is the song
    number patched over the library; the _lib tag names the library. On 669
    real minis the program was one or two bytes."""
    h = psfmod.parse(_psf(rom=bytes([7, 0])), 10 ** 6)
    assert h["gsf"]["length"] == 2
    assert h["gsf"]["consistent"]
    assert h["libs"] == ["seed.gsflib"]


def test_a_missing_library_is_named(tmp_path):
    p = _write(tmp_path, _psf(lib="nowhere.gsflib"))
    _chunks, warns = walker.inspect_psf(str(p))
    assert any("nowhere.gsflib" in w and "not beside" in w for w in warns)


def test_a_library_beside_the_mini_is_not_a_warning(tmp_path):
    _write(tmp_path, _psf(tags=False, rom=bytes(64)), "seed.gsflib")
    p = _write(tmp_path, _psf())
    _chunks, warns = walker.inspect_psf(str(p))
    assert not any("not beside" in w for w in warns)


def test_a_library_carries_no_tags_and_that_is_not_a_warning(tmp_path):
    """22 of 22 real libraries have no [TAG] block. A library is data."""
    p = _write(tmp_path, _psf(tags=False, rom=bytes(64)), "seed.gsflib")
    chunks, warns = walker.inspect_psf(str(p))
    assert not any("no [TAG]" in w for w in warns)
    assert "library" in next(c for c in chunks if c["id"] == "program")["summary"]


def test_a_mini_without_tags_is_worth_saying(tmp_path):
    p = _write(tmp_path, _psf(tags=False))
    _chunks, warns = walker.inspect_psf(str(p))
    assert any("no [TAG]" in w for w in warns)


# ── the GBA program header ──────────────────────────────────────────

def test_the_gba_header_is_entry_offset_length():
    """Twelve bytes then exactly `length` bytes of ROM, on 509 of 509 real
    files with zero mismatches."""
    h = psfmod.parse(_psf(rom=bytes(40)), 10 ** 6)
    g = h["gsf"]
    assert g["entry"] == 0x08000000
    assert g["offset"] == 0x08001000
    assert g["length"] == 40 and g["rom_bytes"] == 40


def test_a_gba_header_that_disagrees_with_its_rom_is_said(tmp_path):
    program = struct.pack("<III", 0x08000000, 0x08001000, 99) + bytes(10)
    comp = zlib.compress(program)
    blob = (b"PSF\x22" + struct.pack("<III", 0, len(comp), zlib.crc32(comp) & 0xFFFFFFFF)
            + comp)
    p = _write(tmp_path, blob)
    _chunks, warns = walker.inspect_psf(str(p))
    assert any("declares 99 bytes of ROM and 10 follow" in w for w in warns)


def test_other_machines_programs_are_reported_not_guessed():
    """Only GSF's program header is decoded. A PSF1 program is a stated size
    that inflates, and nothing more is claimed about it."""
    h = psfmod.parse(_psf(version=0x01, rom=bytes(200)), 10 ** 6)
    assert h["gsf"] is None
    assert h["inflated_size"] == 200


# ── tags ────────────────────────────────────────────────────────────

def test_tags_are_key_value_lines(tmp_path):
    p = _write(tmp_path, _psf(title="A TUNE"))
    chunks, _w = walker.inspect_psf(str(p))
    t = next(c for c in chunks if c["id"] == "tags")
    vals = {f["name"]: f["value"] for f in t["fields"]}
    assert vals["title"] == "A TUNE"
    assert vals["artist"] == "NOBODY"
    assert vals["_lib"] == "seed.gsflib"


def test_a_repeated_key_is_a_multi_line_value():
    blob = _psf(tags=False) + b"[TAG]\ncomment=one\ncomment=two\n"
    h = psfmod.parse(blob, len(blob))
    assert h["tags"]["comment"] == "one\ntwo"


def test_the_walk_tiles(tmp_path):
    p = _write(tmp_path, _psf())
    chunks, _w = walker.inspect_psf(str(p))
    assert sum(c["size"] for c in chunks) == p.stat().st_size


@pytest.mark.parametrize("n", [0, 3, 4, 16, 20, 40])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    p = _write(tmp_path, _psf()[:n])
    try:
        walker.inspect_psf(str(p))
    except Exception as exc:                  # noqa: BLE001 -- that is the test
        from acidcat.core.walk.base import Unsupported
        if not isinstance(exc, Unsupported):
            pytest.fail("truncation at %d raised %r" % (n, exc))


def test_psf_is_a_known_format():
    from acidcat.core.walk import _WALKERS
    assert "psf" in sniff.KNOWN_FORMATS
    assert "psf" in _WALKERS


# ── opt-in: the real corpus ─────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("ACIDCAT_PSF_CORPUS"),
                    reason="set ACIDCAT_PSF_CORPUS to a dir of real PSF-family files")
def test_real_corpus_walks_completely():
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file

    root = os.environ["ACIDCAT_PSF_CORPUS"]
    files = [os.path.join(r, f) for r, _d, fn in os.walk(root) for f in fn
             if "psf" in f.lower() or "sf" in os.path.splitext(f)[1].lower()]
    assert len(files) >= 100, "only %d files" % len(files)
    seen = tiled = crc = 0
    for path in files:
        if sniff.sniff(path) != "psf":
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
        head = next(c for c in chunks if c["id"] == "header")
        crc += any("matches" in (f.get("note") or "") for f in head["fields"]
                   if f["name"] == "crc32")
    assert seen >= len(files) - 5, "%d of %d not identified" % (len(files) - seen, len(files))
    assert tiled == seen, "%d of %d did not tile" % (seen - tiled, seen)
    assert crc == seen, "%d of %d failed their CRC" % (seen - crc, seen)


# ── the DS (2SF) ────────────────────────────────────────────────────

def test_a_2sf_program_header_is_offset_and_length_with_no_entry(tmp_path):
    """GSF's 12-byte header without the entry point. 3,283 of 3,283 real
    files: inflated size == 8 + the declared length."""
    h = psfmod.parse(_psf(version=0x24), 10 ** 6)
    assert h["platform"][0] == "2SF"
    assert h["gsf"]["entry"] is None and h["gsf"]["length"] == 2
    assert h["gsf"]["consistent"]
    chunks, warns = walker.inspect_psf(str(_write(tmp_path, _psf(version=0x24), "a.mini2sf")))
    prog = next(c for c in chunks if c["id"] == "program")
    assert "mini: 2 bytes patched" in prog["summary"]
    assert not any(f["name"] == "entry_point" for f in prog["fields"])
    assert warns == ["names library 'seed.gsflib' and it is not beside this "
                     "file, so the tune cannot play"]


def test_a_2sf_save_block_is_a_patch_into_the_save_state(tmp_path):
    """"SAVE", zlib size, CRC32, zlib; inflated, (offset, length, data).
    219 of 219 real files, minis patch four bytes."""
    blob = _psf(version=0x24, save=bytes(4))
    h = psfmod.parse(blob, len(blob))
    sv = h["save"]
    assert sv["ok"] and sv["crc_ok"] and sv["fits"]
    assert (sv["offset"], sv["length"], sv["consistent"]) == (0xE8, 4, True)
    chunks, warns = walker.inspect_psf(str(_write(tmp_path, blob, "a.mini2sf")))
    res = next(c for c in chunks if c["id"] == "reserved")
    assert res["summary"] == "save-state patch: 4 bytes at 0x000000E8"
    assert not any("SAVE" in w for w in warns)


def test_a_2sf_save_block_crc_mismatch_is_said(tmp_path):
    blob = bytearray(_psf(version=0x24, save=bytes(4)))
    blob[psfmod.HEADER + 8] ^= 0xFF
    _chunks, warns = walker.inspect_psf(str(_write(tmp_path, bytes(blob), "a.mini2sf")))
    assert any("SAVE block's CRC32" in w for w in warns)


def test_a_mini_with_no_program_at_all_is_not_broken(tmp_path):
    """Two real DS minis carry a SAVE patch and a tag and no program: the
    library holds everything. That is not a program that fails to inflate."""
    blob = _psf(version=0x24, rom=b"", save=bytes(4))
    chunks, warns = walker.inspect_psf(str(_write(tmp_path, blob, "a.mini2sf")))
    prog = next(c for c in chunks if c["id"] == "program")
    assert prog["size"] == 0 and "no program" in prog["summary"]
    assert not any("inflate" in w for w in warns)


def test_an_empty_tag_block_is_a_tag_block(tmp_path):
    blob = _psf(tags=False) + b"[TAG]"
    chunks, warns = walker.inspect_psf(str(_write(tmp_path, blob)))
    assert chunks[-1]["id"] == "tags" and "empty" in chunks[-1]["summary"]
    assert not warns


def test_inflation_is_counted_not_held():
    """A DS library inflates to 64 MB and a cartridge can be 512. The
    reader keeps the first 16 bytes and the count, never the stream."""
    import zlib
    big = zlib.compress(struct.pack("<II", 0, 3 * 1024 * 1024) + bytes(3 * 1024 * 1024))
    size, head = psfmod._inflate_counting(big)
    assert size == 8 + 3 * 1024 * 1024 and len(head) == 16
    assert psfmod._inflate_counting(b"not zlib at all") == (None, b"")
    old = psfmod.INFLATE_CAP
    psfmod.INFLATE_CAP = 1024
    try:
        assert psfmod._inflate_counting(big) == (None, b"")
    finally:
        psfmod.INFLATE_CAP = old
