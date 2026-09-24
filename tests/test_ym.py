"""Tests for YM: the LHA wrapper, the -lh5- decoder, the YM image, the walk.

The -lh5- streams here are assembled bit by bit from the method's rules
rather than produced by an archiver, so each one exercises a stated rule:
a single-symbol table that costs no bits, canonical codes assigned by
length then symbol, a run of zero lengths, a match that copies the byte
before it. A decoder that gets any of those wrong produces different
bytes, and the CRC in the member header refuses them.
"""
import os
import struct

import pytest

from acidcat.core.codecs import lha
from acidcat.core.formats import ym as ymmod
from acidcat.core.infra import sniff
from acidcat.core.walk import walk_file

import seeds


class _Bits(object):
    def __init__(self):
        self.bits = []

    def put(self, value, n):
        self.bits += [(value >> (n - 1 - i)) & 1 for i in range(n)]

    def code(self, s):
        self.bits += [int(c) for c in s]

    def bytes(self):
        b = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int("".join(map(str, b[i:i + 8])), 2) for i in range(0, len(b), 8))


def _literal_block(data):
    """Every byte a literal: the literal table gives all 256 bytes length 8,
    so a byte's canonical code is the byte itself. Both small tables are
    single-symbol and cost nothing per use."""
    w = _Bits()
    w.put(len(data), 16)
    w.put(0, 5)
    w.put(10, 5)                                # every length is 10 - 2 = 8
    w.put(256, 9)                               # lengths for symbols 0-255
    w.put(0, 4)
    w.put(0, 4)                                 # positions: always 0
    for b in data:
        w.put(b, 8)
    return w.bytes()


def _match_block():
    """'A', a 3-byte match one back, 'B': AAAAB. The literal table has
    A=1 bit, B=2, length-3 match=2; canonical codes 0, 10, 11."""
    w = _Bits()
    w.put(3, 16)                                # three symbols
    # the table that codes the literal table's lengths: symbols 2, 3, 4
    # (a long zero run, length 1, length 2) with lengths 1, 2, 2
    w.put(5, 5)
    for n in (0, 0, 1):
        w.put(n, 3)
    w.put(0, 2)                                 # after the third: no extra zeros
    for n in (2, 2):
        w.put(n, 3)
    # the literal table, 257 lengths
    w.put(257, 9)
    w.code("0")
    w.put(65 - 20, 9)                           # 65 zeros
    w.code("10")                                # A: length 1
    w.code("11")                                # B: length 2
    w.code("0")
    w.put(189 - 20, 9)                          # 189 zeros
    w.code("11")                                # symbol 256: length 2
    w.put(0, 4)
    w.put(0, 4)                                 # positions: always 0
    w.code("0")                                 # A
    w.code("11")                                # match of 3, one back
    w.code("10")                                # B
    return w.bytes()


# -- the decoder ---------------------------------------------------------

def test_a_literal_block_decodes_byte_for_byte():
    data = bytes(range(256)) + b"hello"
    assert lha.unpack_lh5(_literal_block(data), len(data), 1 << 20) == data


def test_a_match_copies_from_one_byte_back():
    assert lha.unpack_lh5(_match_block(), 5, 1 << 20) == b"AAAAB"


def test_a_member_is_checked_against_its_crc():
    raw = seeds.lha_member(b"AAAAB", method=b"-lh5-", packed_body=_match_block())
    h, data = lha.unpack(raw)
    assert data == b"AAAAB" and h["method"] == "-lh5-" and h["checksum_ok"]
    bad = bytearray(raw)
    crc_at = 2 + bad[0] - 2
    bad[crc_at] ^= 0xFF
    with pytest.raises(lha.LhaError, match="CRC"):
        lha.unpack(bytes(bad))


def test_an_incomplete_code_is_refused():
    """Lengths whose codes do not fill the code space are a damaged table,
    not something to decode around."""
    w = _Bits()
    w.put(1, 16)
    w.put(3, 5)
    for n in (1, 2, 0):                         # 1/2 + 1/4: incomplete
        w.put(n, 3)
    w.put(0, 2)
    with pytest.raises(lha.LhaError, match="complete"):
        lha.unpack_lh5(w.bytes() + bytes(8), 1, 1 << 20)


def test_a_claimed_size_over_the_cap_is_refused():
    with pytest.raises(lha.LhaError, match="cap"):
        lha.unpack_lh5(_literal_block(b"x"), 1 << 30, 1 << 20)


def test_crc16_is_arc():
    assert lha.crc16(b"123456789") == 0xBB3D


def test_other_header_levels_and_methods_are_refused():
    raw = bytearray(seeds.lha_member(b"abc"))
    raw[20] = 1
    with pytest.raises(lha.LhaError, match="level 1"):
        lha.parse_header(bytes(raw))
    raw = seeds.lha_member(b"abc", method=b"-lh6-", packed_body=b"xyz")
    with pytest.raises(lha.LhaError, match="-lh6-"):
        lha.unpack(raw)


# -- the image ------------------------------------------------------------

def test_ym5_header_and_layout():
    img = seeds.SEEDS["ym"][0](frames=4)
    y = ymmod.parse(img)
    assert y["ok"] and y["frames"] == 4 and y["registers"] == 16
    assert y["clock"] == 2000000 and y["rate"] == 50 and y["interleaved"]
    assert y["drums"] == [(34, 8)]
    assert [s[3] for s in y["strings"]] == ["SEED", "SEED AUTHOR", ""]
    assert img[y["end_at"]:y["end_at"] + 4] == b"End!"


def test_ym3_counts_the_frames_that_fit():
    y = ymmod.parse(b"YM3!" + bytes(14 * 10 + 3))
    assert y["ok"] and y["frames"] == 10
    assert "3 bytes after the last whole frame" in y["warnings"][0]


def test_ym3b_keeps_its_loop_frame_at_the_end():
    y = ymmod.parse(seeds.SEEDS["ym"][0](version=b"YM3b", frames=4))
    assert y["ok"] and y["frames"] == 4 and y["loop"] == 1


def test_ym4_is_not_guessed_at():
    y = ymmod.parse(b"YM4!LeOnArD!" + bytes(64))
    assert not y["ok"] and "YM4" in y["why"]


def test_a_missing_end_marker_and_short_data_are_said():
    img = seeds.SEEDS["ym"][0](frames=4)
    y = ymmod.parse(img[:-4])
    assert y["ok"] and y["end_at"] is None
    assert any("End!" in w for w in y["warnings"])
    y = ymmod.parse(img[:-4 - 20])
    assert any("says 4 frames; 2 fit" in w for w in y["warnings"])


# -- the walk --------------------------------------------------------------

def _walk(tmp_path, raw, name="a.ym"):
    p = tmp_path / name
    p.write_bytes(raw)
    return walk_file(str(p))


def _tiles(chunks, size):
    pos = 0
    for c in sorted(chunks, key=lambda c: c["offset"]):
        if c["offset"] != pos:
            return False
        pos = c["offset"] + c["size"]
    return pos == size


def test_a_bare_ym_tiles_region_by_region(tmp_path):
    raw = seeds.SEEDS["ym"][0]()
    fmt, chunks, warns = _walk(tmp_path, raw)
    assert fmt.startswith("ST-Sound YM2149")
    assert [c["id"] for c in chunks] == ["header", "digidrum_0", "strings", "registers", "end"]
    assert _tiles(chunks, len(raw)) and not warns
    voices = [f for f in chunks[0]["fields"] if f["name"] == "voices"][0]
    assert voices["value"] == "A"                # only r8 is ever non-zero


def test_a_packed_ym_positions_the_wrapper_and_not_the_image(tmp_path):
    raw = seeds.SEEDS["ym"][0](packed=True)
    fmt, chunks, warns = _walk(tmp_path, raw)
    assert [c["id"] for c in chunks] == ["lha_header", "lh0", "archive_end"]
    assert _tiles(chunks, len(raw)) and not warns
    assert all(f["off"] is not None for f in chunks[0]["fields"])
    assert all(f["off"] is None for f in chunks[1]["fields"])


def test_an_lha_member_that_is_not_a_ym_is_refused(tmp_path):
    from acidcat.core.walk.base import Unsupported
    with pytest.raises(Unsupported, match="not a YM"):
        _walk(tmp_path, seeds.lha_member(b"not a tune at all"))


def test_an_lha_archive_is_a_ym_only_when_it_says_so(tmp_path):
    """LHA holds anything, and the method id is all the head shows. A
    level-0 member named .ym, or in a file named .ym, is claimed; an .lzh
    of something else, or a level-2 header, is not."""
    tune = seeds.SEEDS["ym"][0]()
    for name, member, raw, want in [
        ("a.ym", b"SONG.BIN", seeds.lha_member(tune, name=b"SONG.BIN"), "ym"),
        ("a.lzh", b"SONG.YM", seeds.lha_member(tune, name=b"SONG.YM"), "ym"),
        ("a.lzh", b"GAME.NSF", seeds.lha_member(b"NESM", name=b"GAME.NSF"), None),
    ]:
        p = tmp_path / name
        p.write_bytes(raw)
        assert sniff.sniff(str(p)) == want, (name, member)
    bad = bytearray(seeds.lha_member(tune, name=b"SONG.YM"))
    bad[20] = 2                                 # a level-2 header
    p = tmp_path / "b.lzh"
    p.write_bytes(bytes(bad))
    assert sniff.sniff(str(p)) is None


def test_the_digidrum_listing_cap_is_announced(tmp_path, monkeypatch):
    from acidcat.core.walk import ym as ywalk
    monkeypatch.setattr(ywalk, "_YM_DRUM_LIST_CAP", 2)
    raw = seeds.SEEDS["ym"][0](drums=(b"\x80" * 4,) * 5)
    fmt, chunks, warns = _walk(tmp_path, raw)
    assert any("listing the first 2 of 5 digidrums" in w for w in warns)
    assert _tiles(chunks, len(raw))


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("ACIDCAT_YM_CORPUS"),
                    reason="set ACIDCAT_YM_CORPUS to a dir of real .ym files")
def test_real_ym_files_unpack_and_tile():
    """Every file unpacks with a matching CRC, parses, and tiles -- except
    ST-Sound's sample types, which are refused by name."""
    root = os.environ["ACIDCAT_YM_CORPUS"]
    files = sorted(os.path.join(d, f) for d, _, fs in os.walk(root)
                   for f in fs if f.lower().endswith(".ym"))
    if not files:
        pytest.skip("no .ym files under ACIDCAT_YM_CORPUS")
    # a fixed sample keeps the release tier fast; the whole corpus was walked
    # when the format went in
    import random
    random.Random(7).shuffle(files)
    failed = []
    for path in files[:800]:
        try:
            fmt, chunks, warns = walk_file(path)
        except Exception as e:                  # noqa: BLE001
            if not any(t.decode() in str(e) for t in ymmod.OTHER_TYPES):
                failed.append((path, repr(e)))
            continue
        if not _tiles(chunks, os.path.getsize(path)):
            failed.append((path, "does not tile"))
    assert not failed, failed[:5]


def test_the_sample_types_are_refused_by_name(tmp_path):
    from acidcat.core.walk.base import Unsupported
    raw = b"MIX1LeOnArD!" + bytes(32)
    assert sniff.sniff_bytes(raw[:20]) == "ym"
    with pytest.raises(Unsupported, match="MIX1 is ST-Sound's sample-remix type"):
        _walk(tmp_path, raw)
