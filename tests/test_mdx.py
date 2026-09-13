"""Tests for the Sharp X68000 MDX walker.

MDX has no magic number. A file opens with a Shift-JIS title, so identifying
one is arithmetic: a title terminator, a NUL-terminated sample-bank name, then
an offset table that has to resolve to 9 or 16 channels with every offset
inside the file. That makes the identification itself the thing most worth
testing, because there is no signature to fall back on.

Verified against 27,166 real tunes from the X68000 MDX Master Library: 27,147
identified, none crashed, none produced untrustworthy geometry, and every one
of them was accounted for byte for byte.
"""
import os
import struct

import pytest

from acidcat.core.formats import mdx as mdxmod
from acidcat.core.infra import sniff
from acidcat.core.walk import mdx as walker


def _mdx(title="TEST TUNE", pdx="", channels=9, voices=2, mml_len=16,
         voices_first=False):
    """A structurally valid MDX.

    The channel data has to start immediately after the offset table, because
    that is what makes the channel count derivable. See the invariant test
    below; a builder that put the voice block first produced a file whose
    count resolved to 49 channels and was correctly rejected.
    """
    head = title.encode("shift_jis") + mdxmod.TITLE_END
    head += pdx.encode("shift_jis") + b"\x00"
    base = len(head)
    table = 2 + channels * 2                     # voice word + channel words

    voice_blob = b"".join(bytes([i + 1, 0x38, 0x0F]) + bytes(24)
                          for i in range(voices))
    streams = [bytes(mml_len) if i < 2 else b"\x00\x00" for i in range(channels)]

    if voices_first:
        # the METAL SIGHT layout: voice block immediately after the table,
        # MML streams after that
        voice_rel = table
        pos = table + len(voice_blob)
        mml_rel = []
        for s in streams:
            mml_rel.append(pos)
            pos += len(s)
        tail = voice_blob + b"".join(streams)
    else:
        pos = table
        mml_rel = []
        for s in streams:
            mml_rel.append(pos)
            pos += len(s)
        voice_rel = pos
        tail = b"".join(streams) + voice_blob

    body = struct.pack(">H", voice_rel) + struct.pack(">%dH" % channels, *mml_rel)
    return head + body + tail


def _packed_mdx(title="PACKED TUNE", pdx="bank", stamp=b"LZX 0.32"):
    """A module whose body was compressed after the header was written.

    The title and the PDX name survive in the clear; everything from the
    offset table on is the compressor's output, which is why the table
    resolves to nonsense.
    """
    head = title.encode("shift_jis") + mdxmod.TITLE_END
    head += pdx.encode("shift_jis") + b"\x00"
    # four bytes of packed stream, then the stamp where 438 of 440 real
    # packed modules carry it
    return head + b"`&`2" + stamp + bytes(256)


# ── identification, which is the whole problem ──────────────────────

def test_a_valid_mdx_is_identified(tmp_path):
    p = tmp_path / "a.mdx"
    p.write_bytes(_mdx())
    assert sniff.sniff(str(p)) == "mdx"


def test_identification_needs_more_than_the_shared_sniff_head(tmp_path):
    """The sniffer's common head is 20 bytes. An MDX offset table is never
    inside that -- the title alone is usually longer -- so the check has to
    read the file itself. It did not at first, and identified nothing."""
    p = tmp_path / "long.mdx"
    p.write_bytes(_mdx(title="A" * 200))
    assert len(open(str(p), "rb").read(20)) == 20
    assert sniff.sniff(str(p)) == "mdx"


def test_offsets_are_bounded_by_the_FILE_not_the_buffer():
    """The same bug from the other side. Offsets routinely point past the few
    kilobytes a sniffer reads, so checking them against len(buffer) rejects
    almost every real tune."""
    blob = _mdx(title="B" * 300, mml_len=4096)
    head = blob[:512]
    assert mdxmod.looks_like_mdx(head, len(blob)) is True
    assert mdxmod.looks_like_mdx(head) is False, (
        "without a file size the truncated head cannot resolve, which is "
        "exactly why the size has to be passed")


@pytest.mark.parametrize("blob", [
    b"",
    b"no terminator here at all",
    b"TITLE\x0d\x0a\x1a",                        # no NUL, no table
    b"TITLE\x0d\x0a\x1a\x00",                    # NUL but no table
    b"TITLE\x0d\x0a\x1a\x00" + b"\x00\x00\x00\x03",   # odd first offset
])
def test_things_that_are_not_mdx_are_not_identified(blob):
    assert mdxmod.looks_like_mdx(blob) is False


def test_a_table_resolving_to_an_impossible_channel_count_is_rejected():
    """Only 9 and 16 occur. A file whose first offset implies 12,312 channels
    is not an MDX, and 334 such files turned up in a corpus extracted with a
    deliberately naive method -- so this rejection does real work."""
    blob = bytearray(_mdx())
    base = blob.index(mdxmod.TITLE_END) + 4
    struct.pack_into(">H", blob, base, 0x7000)      # voice block, further out
    struct.pack_into(">H", blob, base + 2, 0x6000)  # first MML stream
    h = mdxmod.parse_header(bytes(blob))
    assert h["ok"] is False and "channels" in h["why"]


# ── the offset base, which is the format's one real trap ────────────

def test_offsets_are_relative_to_the_offset_word_not_the_file(tmp_path):
    """Everything before the table is variable length, so the base moves per
    file. Reading the offsets as absolute puts every tune's data in the wrong
    place, and the error grows with the title."""
    short = _mdx(title="X")
    long_ = _mdx(title="X" * 120)
    hs, hl = mdxmod.parse_header(short), mdxmod.parse_header(long_)

    assert hs["mml_offsets"] == hl["mml_offsets"], \
        "the stored offsets are identical because they are relative"
    assert hl["mml_abs"][0] - hs["mml_abs"][0] == 119, \
        "but the absolute positions differ by exactly the extra title length"
    assert hl["base"] - hs["base"] == 119


def test_the_channel_count_is_derived_not_declared():
    """Nothing stores it. The first MML offset points past the table, so the
    gap between the two IS the table."""
    for n in (mdxmod.CHANNELS_BASE, mdxmod.CHANNELS_MERCURY):
        h = mdxmod.parse_header(_mdx(channels=n))
        assert h["channels"] == n
        assert len(h["mml_abs"]) == n


def test_channel_letters_follow_mxdrv():
    """A-H are FM, P is ADPCM, Q onward are Mercury Unit voices."""
    assert [mdxmod.channel_name(i, 9) for i in range(9)] == list("ABCDEFGHP")
    assert mdxmod.channel_name(8, 16) == "P"
    assert mdxmod.channel_name(9, 16) == "Q"
    assert mdxmod.channel_name(15, 16) == "W"


# ── text ────────────────────────────────────────────────────────────

def test_the_title_decodes_as_shift_jis(tmp_path):
    """The X68000 is a Japanese machine and the titles are Japanese."""
    title = "大魔界村 STAGE 2"
    p = tmp_path / "jp.mdx"
    p.write_bytes(_mdx(title=title))
    chunks, _ = walker.inspect_mdx(str(p))
    assert chunks[0]["fields"][0]["value"] == title


def test_an_undecodable_title_is_still_a_title():
    """Refusing to name the tune is worse than naming it imperfectly."""
    assert mdxmod.decode_title(b"\xff\xfe\x81") != ""


def test_a_bare_nul_means_no_sample_bank(tmp_path):
    p = tmp_path / "nopdx.mdx"
    p.write_bytes(_mdx(pdx=""))
    h = mdxmod.parse_header(p.read_bytes())
    assert h["has_pdx"] is False and h["pdx_name"] == ""

    p2 = tmp_path / "pdx.mdx"
    p2.write_bytes(_mdx(pdx="DRA.PDX"))
    h2 = mdxmod.parse_header(p2.read_bytes())
    assert h2["has_pdx"] is True and h2["pdx_name"] == "DRA.PDX"


# ── voices ──────────────────────────────────────────────────────────

def test_voices_are_27_byte_opm_register_records():
    blob = _mdx(voices=3)
    h = mdxmod.parse_header(blob)
    assert mdxmod.voice_count(blob, h) == 3
    v = mdxmod.parse_voice(blob, h["voice_abs"])
    assert v["number"] == 1
    assert v["feedback"] == 7 and v["connect"] == 0      # 0x38 = FL 7, CON 0
    assert v["slot_mask"] == 0x0F
    assert len(v["tl"]) == 4, "the operator fields are four bytes: M1 C1 M2 C2"


def test_a_voice_region_too_short_for_one_voice_is_still_claimed(tmp_path):
    """26 bytes is one short of a voice. voice_count floors the division and
    returns 0, and skipping the chunk left those bytes belonging to nothing --
    which reads as a cavity rather than as the short block it is."""
    blob = bytearray(_mdx(voices=1))
    p = tmp_path / "short.mdx"
    p.write_bytes(bytes(blob[:-1]))                      # clip one byte off
    chunks, warns = walker.inspect_mdx(str(p))
    assert any(c["id"] == "voices" for c in chunks), \
        "the region exists even when it cannot hold a whole voice"
    assert any("too short" in w for w in warns)


def test_a_voice_offset_of_zero_is_reported_not_followed(tmp_path):
    """It points the voice block at the offset table it is part of. Emitting a
    chunk there overlaps the header and claims the same bytes twice."""
    blob = bytearray(_mdx())
    base = blob.index(mdxmod.TITLE_END) + 4
    struct.pack_into(">H", blob, base, 0)
    p = tmp_path / "degenerate.mdx"
    p.write_bytes(bytes(blob))

    chunks, warns = walker.inspect_mdx(str(p))
    assert any("inside the header" in w for w in warns)
    ids = [c["id"] for c in chunks]
    assert "voices" not in ids


# ── the walk ────────────────────────────────────────────────────────

def test_the_table_ends_at_whichever_block_comes_first():
    """The invariant the channel count depends on, in both layouts.

    (table_end - 2) / 2 yields the channel count, and the table end is
    whichever of the voice block or the first MML stream starts earlier.
    Most tunes put the MML first. Eighteen of 27,166 do not, and reading the
    first MML offset as the table end gave those 399 channels and threw them
    away as not-MDX.
    """
    for n in (mdxmod.CHANNELS_BASE, mdxmod.CHANNELS_MERCURY):
        after = mdxmod.parse_header(_mdx(channels=n))
        assert after["channels"] == n
        assert after["mml_offsets"][0] == 2 + n * 2
        assert after["voice_offset"] > after["mml_offsets"][0]

        before = mdxmod.parse_header(_mdx(channels=n, voices_first=True))
        assert before["channels"] == n,             "the voice block can come first, and then it ends the table"
        assert before["voice_offset"] == 2 + n * 2
        assert before["mml_offsets"][0] > before["voice_offset"]


def test_a_voices_first_module_walks_and_is_identified(tmp_path):
    p = tmp_path / "metal.mdx"
    p.write_bytes(_mdx(channels=9, voices_first=True))
    assert sniff.sniff(str(p)) == "mdx"
    chunks, _warns = walker.inspect_mdx(str(p))
    covered = sum(c["size"] for c in chunks)
    assert covered == p.stat().st_size


# ── packed modules ──────────────────────────────────────────────────

def test_a_packed_module_is_an_mdx_not_an_unknown(tmp_path):
    """440 of 27,166 real modules are LZX- or ZOO-packed. Their bodies cannot
    be walked, but their titles and sample-bank names read perfectly -- so
    "unrecognized file" was the wrong answer to all 440."""
    p = tmp_path / "packed.mdx"
    p.write_bytes(_packed_mdx())
    assert sniff.sniff(str(p)) == "mdx"

    h = mdxmod.parse_header(p.read_bytes())
    assert h["ok"] is False, "the body genuinely cannot be walked"
    assert h["packer"] == "LZX 0.32"
    assert h["title"] == "PACKED TUNE"
    assert h["pdx_name"] == "bank"


def test_a_packed_module_names_its_packed_bytes(tmp_path):
    """The compressed block is not walked, but it is still in the file, so it
    gets a chunk. An unaccounted region reads as a cavity everywhere else in
    acidcat and it should not mean something different here."""
    p = tmp_path / "packed.mdx"
    p.write_bytes(_packed_mdx())
    chunks, _warns = walker.inspect_mdx(str(p))

    assert [c["id"] for c in chunks] == ["header", "packed"]
    assert sum(c["size"] for c in chunks) == p.stat().st_size
    names = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert names["packer"] == "LZX 0.32"
    assert names["title"] == "PACKED TUNE"
    assert names["pdx_name"] == "bank"
    assert any("packed" in w for w in chunks[0]["warnings"])


@pytest.mark.parametrize("stamp", [b"LZX 0.42", b"ZOO", b"LHA", b"LZS"])
def test_every_packer_in_the_table_is_recognized(tmp_path, stamp):
    p = tmp_path / "p.mdx"
    p.write_bytes(_packed_mdx(stamp=stamp))
    assert sniff.sniff(str(p)) == "mdx"


def test_garbage_after_a_title_is_still_not_an_mdx(tmp_path):
    """The packer branch must not become a way in. Without a stamp, a file
    with a title terminator and a nonsense table stays rejected."""
    p = tmp_path / "no.mdx"
    p.write_bytes(_packed_mdx(stamp=b"........"))
    assert sniff.sniff(str(p)) != "mdx"
    h = mdxmod.parse_header(p.read_bytes())
    assert h["packer"] == "" and h["ok"] is False


def test_the_packer_stamp_is_only_looked_for_near_the_table():
    """"LZX" is three ordinary bytes. Finding them anywhere in a file proves
    nothing, so the search is a short window at the table."""
    raw = bytes(4096) + b"LZX 0.32"
    assert mdxmod.packer_stamp(raw, 0) == ""


def test_the_walk_covers_every_byte(tmp_path):
    """Header, channel streams and voice block must tile the file exactly."""
    from acidcat.core.infra import geometry
    blob = _mdx(voices=3)
    p = tmp_path / "cover.mdx"
    p.write_bytes(blob)
    chunks, _ = walker.inspect_mdx(str(p))
    geometry.normalize(chunks, len(blob))

    assert all(geometry.is_trustworthy(c) for c in chunks)
    covered = sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                  for c in chunks)
    assert covered == len(blob), (covered, len(blob), [c["id"] for c in chunks])


@pytest.mark.parametrize("n", [0, 4, 12, 20, 40, 60])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    p = tmp_path / "trunc.mdx"
    p.write_bytes(_mdx()[:n])
    mdxmod.parse_header(p.read_bytes())          # must not raise
    try:
        walker.inspect_mdx(str(p))
    except Exception as exc:                     # noqa: BLE001 - that IS the assertion
        pytest.fail("truncation to %d bytes raised %r" % (n, exc))


def test_an_offset_past_the_end_is_reported(tmp_path):
    blob = bytearray(_mdx())
    base = blob.index(mdxmod.TITLE_END) + 4
    struct.pack_into(">H", blob, base + 2 + 2, 0xF000)   # channel B, far away
    p = tmp_path / "far.mdx"
    p.write_bytes(bytes(blob))
    _chunks, warns = walker.inspect_mdx(str(p))
    assert any("past the end" in w for w in warns), warns


def test_mdx_is_a_known_format():
    assert "mdx" in sniff.KNOWN_FORMATS


# ── opt-in: the real corpus ─────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("ACIDCAT_MDX_CORPUS"),
                    reason="set ACIDCAT_MDX_CORPUS to a dir of real .mdx files")
def test_real_corpus_walks_completely():
    """Measured over 27,166 tunes: 27,147 identified, zero crashes, zero
    untrustworthy geometry, and every identified file covered byte for byte."""
    import glob
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file

    root = os.environ["ACIDCAT_MDX_CORPUS"]
    files = sorted(glob.glob(os.path.join(root, "**", "*.MDX"), recursive=True)
                   + glob.glob(os.path.join(root, "**", "*.mdx"), recursive=True))
    assert files, "no .mdx files under %s" % root

    seen = covered_fully = 0
    for path in files:
        if sniff.sniff(path) != "mdx":
            continue
        seen += 1
        _label, chunks, _warns = walk_file(path)
        size = os.path.getsize(path)
        geometry.normalize(chunks, size)
        assert all(geometry.is_trustworthy(c) for c in chunks), path
        covered = sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                      for c in chunks)
        covered_fully += (covered == size)
    assert seen >= 100, "only %d files identified; that tests almost nothing" % seen
    assert covered_fully == seen, (
        "%d of %d identified tunes were not fully accounted for"
        % (seen - covered_fully, seen))
