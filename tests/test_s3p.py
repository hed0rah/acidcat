"""Tests for the Akai S1000/S3000 program walker.

.s3p is not a file layout. It is a recording of the MIDI System Exclusive dump
the sampler sends, with a length written in front of each message, so the two
things most worth testing are the two things a file reader would not expect:
the payload is nibble-split, and the numbers inside the blocks are the
sampler's own memory addresses rather than offsets in the file.

Verified against 1,670 real programs and 29,600 keygroups: all 1,670
identified, none crashed, none produced untrustworthy geometry, every one
accounted for byte for byte, and 51,212 velocity zones decoded.
"""
import os
import struct

import pytest

from acidcat.core.formats import akai as akaimod
from acidcat.core.infra import sniff
from acidcat.core.walk import akai as walker

import seeds


def _s3p(**kw):
    return seeds.SEEDS["s3p"][0](**kw)


def _write(tmp_path, blob, name="a.s3p"):
    p = tmp_path / name
    p.write_bytes(blob)
    return p


# ── the nibble split, which is the whole trap ───────────────────────

def test_a_data_byte_travels_as_two_bytes_low_nibble_first():
    """SysEx cannot carry a byte with bit 7 set, so every byte is sent as two.
    A reader that forgets does not fail loudly -- it gets plausible garbage."""
    assert akaimod.unnibble(bytes([0x06, 0x09])) == bytes([0x96])
    assert akaimod.unnibble(bytes([0x0F, 0x00, 0x00, 0x01])) == bytes([0x0F, 0x10])


def test_an_odd_nibble_count_drops_the_half_byte_rather_than_guessing():
    assert akaimod.unnibble(bytes([0x06, 0x09, 0x0F])) == bytes([0x96])


def test_a_payload_byte_with_bit_7_set_is_masked_not_raised():
    """No real message can carry one -- that is the whole reason for the
    split -- so this only happens to damaged or forged files. Rejoining them
    arithmetically overflows a byte, which the fuzzer found on its first pass.
    Zero of 1,670 real programs have one."""
    assert akaimod.unnibble(bytes([0xFF, 0xFF])) == bytes([0xFF])
    assert akaimod.has_high_bits(bytes([0x0F, 0x80])) is True
    assert akaimod.has_high_bits(bytes([0x0F, 0x0F])) is False


def test_a_masked_message_says_its_contents_are_not_trustworthy(tmp_path):
    blob = bytearray(_s3p())
    blob[akaimod.HEADER + 4 + akaimod.FRAME_PREFIX + 2] = 0x80
    p = _write(tmp_path, bytes(blob))
    _chunks, warns = walker.inspect_s3p(str(p))
    assert any("bit 7 set" in w and "not trustworthy" in w for w in warns)


def test_the_block_is_192_on_the_wire_for_150_of_content():
    blob = _s3p()
    h = akaimod.parse_program(blob, len(blob))
    assert len(h["program"]) == akaimod.BLOCK == 192
    assert akaimod.BLOCK_USED == 150
    assert h["program"][akaimod.BLOCK_USED:] == bytes(
        akaimod.BLOCK - akaimod.BLOCK_USED), \
        "the transfer is padded; measured zero in all 1,670 real programs"


# ── names are the sampler's charset, not ASCII ──────────────────────

def test_a_name_decodes_through_the_akai_charset():
    """Read as ASCII the same bytes are control codes. 11 is 'A', not a tab."""
    raw = bytes([akaimod.CHARSET.index(c) for c in "ABC 123"])
    assert akaimod.akai_name(raw) == "ABC 123"


def test_a_code_outside_the_charset_is_visible_not_dropped():
    """A wrong offset should look wrong. Silently dropping bad codes makes a
    misread name look like a short name."""
    assert akaimod.akai_name(bytes([200, 11, 12])) == "?AB"


def test_the_program_name_survives_the_round_trip(tmp_path):
    p = _write(tmp_path, _s3p(name="TEST PROG"))
    chunks, _warns = walker.inspect_s3p(str(p))
    prog = [c for c in chunks if c["id"] == "program"][0]
    assert {f["name"]: f["value"] for f in prog["fields"]}["name"] == "TEST PROG"


# ── the cross-check that settles the layout ─────────────────────────

def test_the_program_block_count_and_the_message_count_agree(tmp_path):
    """GROUPS lives inside the program block; the message count lives in the
    container. They are written by different parts of the sampler and agreed
    in 1,670 of 1,670 real files, which is what makes the block offsets
    trustworthy."""
    blob = _s3p(keygroups=5)
    h = akaimod.parse_program(blob, len(blob))
    assert h["program"][42] == 5
    assert len(h["keygroups"]) == 5
    assert h["declared_keygroups"] == 5


def test_a_disagreement_between_the_two_counts_is_reported(tmp_path):
    blob = bytearray(_s3p(keygroups=3))
    struct.pack_into(">I", blob, len(akaimod.MAGIC), 9)   # the file header lies
    p = _write(tmp_path, bytes(blob))
    _chunks, warns = walker.inspect_s3p(str(p))
    assert any("declares 9 keygroups" in w for w in warns)


# ── internal pointers are not file offsets ──────────────────────────

def test_the_keygroup_address_is_reported_as_an_address_not_followed():
    """KGRP1@ is a location in the sampler's RAM. It is 150 in every real file
    because that is where the first keygroup sat in memory, which looks
    temptingly like a file offset and is not one."""
    blob = _s3p()
    h = akaimod.parse_program(blob, len(blob))
    fields = dict((n, (v, note)) for n, v, note in
                  akaimod.program_fields(h["program"]))
    value, note = fields["kgrp1_addr"]
    assert value == akaimod.BLOCK_USED
    assert "meaningless in a file" in note


# ── zones ───────────────────────────────────────────────────────────

def test_a_keygroup_has_four_zone_slots_and_keeps_the_empty_ones():
    blob = _s3p()
    h = akaimod.parse_program(blob, len(blob))
    zones = akaimod.keygroup_zones(h["keygroups"][0][2])
    assert [z["zone"] for z in zones] == [1, 2, 3, 4]
    assert [z["at"] for z in zones] == [34, 58, 82, 106], \
        "zones are 24 bytes apart, which is what 29,600 real keygroups show"
    assert zones[0]["sample"] == "SEED SAMPLE"
    assert zones[1]["sample"] == ""


def test_only_zones_that_name_a_sample_are_listed(tmp_path):
    p = _write(tmp_path, _s3p())
    chunks, _warns = walker.inspect_s3p(str(p))
    kg = [c for c in chunks if c["id"].startswith("keygroup")][0]
    assert kg["summary"].endswith("1 zone(s)")
    assert len([f for f in kg["fields"] if f["name"].startswith("zone[")]) == 1


# ── the walk ────────────────────────────────────────────────────────

def test_a_valid_program_is_identified(tmp_path):
    assert sniff.sniff(str(_write(tmp_path, _s3p()))) == "s3p"


def test_the_walk_covers_every_byte(tmp_path):
    p = _write(tmp_path, _s3p(keygroups=4))
    chunks, _warns = walker.inspect_s3p(str(p))
    assert sum(c["size"] for c in chunks) == p.stat().st_size
    assert [c["id"] for c in chunks[:2]] == ["header", "program"]


def test_trailing_bytes_after_the_last_message_are_reported(tmp_path):
    p = _write(tmp_path, _s3p() + b"junk trailing bytes")
    _chunks, warns = walker.inspect_s3p(str(p))
    assert any("not part of any SysEx frame" in w for w in warns)


@pytest.mark.parametrize("n", [0, 8, 12, 16, 100, 400, 600])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    blob = _s3p()[:n]
    p = _write(tmp_path, blob)
    try:
        walker.inspect_s3p(str(p))
    except Exception as exc:                  # noqa: BLE001 -- that is the test
        from acidcat.core.walk.base import Unsupported
        if not isinstance(exc, Unsupported):
            pytest.fail("truncation at %d raised %r" % (n, exc))


def test_a_frame_that_is_not_akai_sysex_ends_the_walk(tmp_path):
    blob = bytearray(_s3p())
    blob[akaimod.HEADER + 4] = 0x00           # where F0 should be
    p = _write(tmp_path, bytes(blob))
    _chunks, warns = walker.inspect_s3p(str(p))
    assert any("did not resolve" in w for w in warns)


def test_things_that_are_not_s3p_are_not_identified(tmp_path):
    for blob in (b"", b"PSYSSS31" + bytes(64), b"RIFF" + bytes(64)):
        assert sniff.sniff(str(_write(tmp_path, blob, "x.s3p"))) != "s3p"


def test_the_magic_alone_is_not_enough(tmp_path):
    """A file that opens PSYSSS30 and then holds no Akai message is not one."""
    assert akaimod.looks_like_s3p(akaimod.MAGIC + bytes(64), 72) is False


def test_s3p_is_a_known_format():
    from acidcat.core.walk import _WALKERS
    assert "s3p" in sniff.KNOWN_FORMATS
    assert "s3p" in _WALKERS


# ── opt-in: the real corpus ─────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("ACIDCAT_S3P_CORPUS"),
                    reason="set ACIDCAT_S3P_CORPUS to a dir of real .s3p files")
def test_real_corpus_walks_completely():
    """Measured over 1,670 programs: all identified, zero crashes, zero
    untrustworthy geometry, every one covered byte for byte."""
    import glob
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file

    root = os.environ["ACIDCAT_S3P_CORPUS"]
    files = sorted(glob.glob(os.path.join(root, "**", "*.s3p"), recursive=True)
                   + glob.glob(os.path.join(root, "**", "*.S3P"),
                               recursive=True))
    seen = covered = named = 0
    for path in set(files):
        if sniff.sniff(path) != "s3p":
            continue
        seen += 1
        _label, chunks, _warns = walk_file(path)
        size = os.path.getsize(path)
        geometry.normalize(chunks, size)
        assert all(geometry.is_trustworthy(c) for c in chunks), path
        covered += (sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                        for c in chunks) == size)
        for c in chunks:
            if c["id"] == "program":
                named += bool({f["name"]: f["value"]
                               for f in c["fields"]}.get("name"))
    assert seen >= 100, "only %d programs identified" % seen
    assert covered == seen, "%d of %d not fully accounted for" % (seen - covered, seen)
    assert named == seen, "%d of %d programs had no name" % (seen - named, seen)
