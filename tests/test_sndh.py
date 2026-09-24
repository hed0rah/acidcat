"""Tests for SNDH: the Pack-Ice depacker, the tag header, the walk.

The ICE stream here is assembled from the format's rules: a literal run
long enough to need every length field, framed by the bit bytes that
carry the flags. What the depacker gets right on real files was measured
against an independent implementation over the whole downloaded corpus;
what these pin is the refusals, which no real file exercises.
"""
import os
import struct

import pytest

from acidcat.core.codecs import ice
from acidcat.core.formats import sndh as sndhmod
from acidcat.core.infra import sniff
from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported

import seeds


def _image(**kw):
    return seeds.SEEDS["sndh"][0](**kw)


# -- Pack-Ice --------------------------------------------------------------

def test_a_literal_stream_unpacks_to_its_image():
    img = _image()
    packed = seeds.ice_literal(img)
    assert ice.header(packed) == ("ICE!", len(packed), len(img))
    assert ice.unpack(packed, 1 << 20) == img


def test_a_stream_that_runs_out_is_refused():
    packed = bytearray(seeds.ice_literal(_image()))
    struct.pack_into(">I", packed, 8, len(_image()) + 40)   # claim more than it holds
    with pytest.raises(ice.IceError):
        ice.unpack(bytes(packed), 1 << 20)


def test_a_claimed_size_over_the_cap_is_refused():
    with pytest.raises(ice.IceError, match="cap"):
        ice.unpack(seeds.ice_literal(_image()), 16)


def test_picture_mode_is_refused_rather_than_guessed():
    """The flag after the image says the packer also reordered a picture's
    bitplanes; undoing that is not implemented, so the result is not
    returned as if it were the data."""
    packed = bytearray(seeds.ice_literal(_image()))
    packed[-3] |= 0x20                           # the bit after the 17 run bits
    with pytest.raises(ice.IceError, match="picture"):
        ice.unpack(bytes(packed), 1 << 20)


# -- the header --------------------------------------------------------------

def test_tags_and_entries():
    s = sndhmod.parse(_image())
    assert s["ok"] and s["hdns"]
    assert [e[:2] for e in s["entries"]] == [("init", 68), ("exit", 70), ("play", 72)]
    assert s["text"] == {"title": "SEED", "composer": "SEED COMPOSER"}
    assert s["subtunes"] == 2 and s["default"] == 1
    assert s["timer"] == ("timer C", 50) and s["times"] == [60, 61]


def test_without_hdns_the_header_ends_where_the_tags_do():
    img = _image(hdns=False)
    s = sndhmod.parse(img)
    assert s["ok"] and not s["hdns"]
    assert s["header_end"] == 64 and img[64:66] == b"\x4e\x75"
    assert not s["warnings"]


def test_subtune_names_are_offsets_from_the_tag_start():
    """Real files measure them from the '!#SN' itself, not from after it
    as the spec's prose says; the names only decode that way."""
    names = b"First\x00Second\x00"
    sn = b"!#SN" + struct.pack(">HH", 8, 14) + names
    img = (struct.pack(">Hh", 0x6000, 0) * 3 + b"SNDH" + b"##02" + sn
           + b"\x00" * ((16 + 4 + len(sn)) & 1) + b"HDNS")
    s = sndhmod.parse(img)
    assert s["names"] == ["First", "Second"]


def test_a_names_table_after_an_odd_tag_skips_its_pad_byte():
    """The offset table is word-aligned: a writer that put the tag at an
    odd address pads one byte after it, and the offsets still count from
    the tag's first byte."""
    names = b"yatzy\x00end\x00"
    sn = b"!#SN" + b"\x00" + struct.pack(">HH", 9, 15) + names
    img = (struct.pack(">Hh", 0x6000, 0) * 3 + b"SNDH" + b"##02" + b"\x00" + sn
           + b"\x00" * ((21 + len(sn)) & 1) + b"HDNS")
    assert (16 + 4 + 1) & 1                     # the tag sits at an odd address
    s = sndhmod.parse(img)
    assert s["names"] == ["yatzy", "end"] and not s["warnings"]


def test_an_rts_entry_is_an_entry_with_nothing_to_do():
    img = bytearray(_image())
    img[4:8] = b"\x4e\x75\x4e\x75"
    s = sndhmod.parse(bytes(img))
    assert s["entries"][1][2] == "rts" and not s["warnings"]


def test_a_table_before_its_count_and_a_bad_default_are_said():
    img = (struct.pack(">Hh", 0x6000, 0) * 3 + b"SNDH" + b"TIME\x00\x10"
           + b"##02" + b"!#05" + b"HDNS")
    s = sndhmod.parse(img)
    assert any("before ##" in w for w in s["warnings"])
    assert any("default subtune 5" in w for w in s["warnings"])


def test_frms_is_frames_per_subtune():
    img = (struct.pack(">Hh", 0x6000, 0) * 3 + b"SNDH" + b"##02"
           + b"FRMS" + struct.pack(">II", 3000, 0) + b"HDNS")
    assert sndhmod.parse(img)["frames"] == [3000, 0]


# -- the walk ----------------------------------------------------------------

def _walk(tmp_path, raw):
    p = tmp_path / "a.sndh"
    p.write_bytes(raw)
    return walk_file(str(p))


def _tiles(chunks, size):
    pos = 0
    for c in sorted(chunks, key=lambda c: c["offset"]):
        if c["offset"] != pos:
            return False
        pos = c["offset"] + c["size"]
    return pos == size


def test_a_bare_sndh_tiles(tmp_path):
    raw = _image()
    assert sniff.sniff_bytes(raw[:20]) == "sndh"
    fmt, chunks, warns = _walk(tmp_path, raw)
    assert [c["id"] for c in chunks] == ["entries", "header", "player"]
    assert _tiles(chunks, len(raw)) and not warns
    init = chunks[0]["fields"][0]
    assert init["xref"] == 68                  # the branch target, followable


def test_a_packed_sndh_positions_the_ice_header_only(tmp_path):
    raw = _image(packed=True)
    assert sniff.sniff_bytes(raw[:20]) == "sndh"
    fmt, chunks, warns = _walk(tmp_path, raw)
    assert [c["id"] for c in chunks] == ["ice_header", "ice"]
    assert _tiles(chunks, len(raw)) and not warns
    assert all(f["off"] is not None for f in chunks[0]["fields"])
    assert all(f["off"] is None for f in chunks[1]["fields"])


def test_ice_data_that_is_not_sndh_is_refused(tmp_path):
    with pytest.raises(Unsupported, match="not SNDH"):
        _walk(tmp_path, seeds.ice_literal(b"just some bytes, no tune"))


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("ACIDCAT_SNDH_CORPUS"),
                    reason="set ACIDCAT_SNDH_CORPUS to a dir of real .sndh files")
def test_real_sndh_files_unpack_and_tile():
    root = os.environ["ACIDCAT_SNDH_CORPUS"]
    files = sorted(os.path.join(d, f) for d, _, fs in os.walk(root)
                   for f in fs if f.lower().endswith(".sndh"))
    if not files:
        pytest.skip("no .sndh files under ACIDCAT_SNDH_CORPUS")
    # a fixed sample keeps the release tier fast; the whole corpus was walked
    # when the format went in
    import random
    random.Random(7).shuffle(files)
    failed = []
    for path in files[:800]:
        try:
            fmt, chunks, warns = walk_file(path)
        except Exception as e:                  # noqa: BLE001
            failed.append((path, repr(e)))
            continue
        if not _tiles(chunks, os.path.getsize(path)):
            failed.append((path, "does not tile"))
    assert not failed, failed[:5]
