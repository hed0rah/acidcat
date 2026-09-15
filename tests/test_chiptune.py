"""Tests for the chiptune containers: NSF, NSFe and SAP.

A caveat that belongs at the top rather than buried, because it changes what a
green run here means: THERE ARE NO NSF OR SAP SPECIMENS. Every fixture below is
synthetic, built from the specification. So these tests prove the walker agrees
with my reading of the spec -- and if I misread a field, the fixture encodes the
same misreading and the test passes anyway. That is a check which cannot fail on
the input it is given, which is the exact bug class this repo keeps finding.

What that means in practice: the assertions here are about STRUCTURE the spec
states unambiguously (offsets, widths, the inclusive end address, the length
order in an NSFe chunk header) rather than about values only a corpus could
settle. Where the specification itself is single-source or disputed, the test
says so rather than pinning a number that might be wrong.

Sources: NESdev wiki, Kevin Horton's nsfspec.txt, Disch's NSFe Revision 2,
asap.sourceforge.net. No GPL player source was consulted.
"""
import os
import struct

import pytest

from acidcat.core.infra import geometry, sniff
from acidcat.core.walk import chiptune


# ── builders ────────────────────────────────────────────────────────

def _nsf(ver=1, songs=3, start=1, load=0x8000, init=0x8003, play=0x8006,
         title=b"Test Tune", artist=b"Artist", copyright=b"2026",
         chips=0x00, region=0x00, banks=bytes(8), nsf2flags=0x00,
         data_len=0, body=b"\xea" * 64, ntsc=16666, pal=20000):
    h = bytearray(0x80)
    h[0:5] = b"NESM\x1a"
    h[5], h[6], h[7] = ver, songs, start
    struct.pack_into("<HHH", h, 8, load, init, play)
    h[0x0E:0x0E + len(title)] = title
    h[0x2E:0x2E + len(artist)] = artist
    h[0x4E:0x4E + len(copyright)] = copyright
    struct.pack_into("<H", h, 0x6E, ntsc)
    h[0x70:0x78] = banks
    struct.pack_into("<H", h, 0x78, pal)
    h[0x7A], h[0x7B], h[0x7C] = region, chips, nsf2flags
    h[0x7D] = data_len & 0xFF
    h[0x7E] = (data_len >> 8) & 0xFF
    h[0x7F] = (data_len >> 16) & 0xFF
    return bytes(h) + body


def _chunk(fourcc, data):
    """An NSFe chunk: LENGTH FIRST, then the FourCC. Reverse of RIFF."""
    return struct.pack("<I", len(data)) + fourcc + data


def _info(load=0x8000, init=0x8003, play=0x8006, region=0, chips=0,
          songs=3, start=0):
    return (struct.pack("<HHH", load, init, play)
            + bytes([region, chips, songs, start]))


def _nsfe(chunks=None):
    if chunks is None:
        chunks = [_chunk(b"INFO", _info()), _chunk(b"DATA", b"\xea" * 32),
                  _chunk(b"NEND", b"")]
    return b"NSFE" + b"".join(chunks)


def _sap(tags=None, blocks=((0x0F80, b"\xea" * 16),), eol="\r\n", ffff=True):
    if tags is None:
        tags = ['AUTHOR "Jakub Husak"', 'NAME "Inside"', 'DATE "1990"',
                "SONGS 1", "TYPE B", "INIT 0F80", "PLAYER 247F", "TIME 06:37.62"]
    head = "SAP" + eol + "".join(t + eol for t in tags)
    body = b"\xff\xff" if ffff else b""
    for start, data in blocks:
        body += struct.pack("<HH", start, start + len(data) - 1) + data
    return head.encode("latin-1") + body


def _w(tmp_path, name, blob):
    p = tmp_path / name
    p.write_bytes(blob)
    return str(p)


def _gbs(songs=4, first=1, load=0x0400, init=0x0400, play=0x0410,
         title="SEED", author="NOBODY", copyright="2026", code=64):
    """A GBS: the 112-byte header and a code blob. Same shape as NSF."""
    h = bytearray(0x70)
    h[0:3] = b"GBS"
    h[3] = 1
    h[4], h[5] = songs, first
    struct.pack_into("<HHHH", h, 6, load, init, play, 0xFFFE)
    for off, text in ((0x10, title), (0x30, author), (0x50, copyright)):
        h[off:off + len(text)] = text.encode("latin-1")
    return bytes(h) + bytes([0xC9]) * code


ALL = [("nsf", _nsf, chiptune.inspect_nsf),
       ("nsfe", _nsfe, chiptune.inspect_nsfe),
       ("sap", _sap, chiptune.inspect_sap),
       ("gbs", _gbs, chiptune.inspect_gbs)]


# ── the three, held to the same contract ────────────────────────────

@pytest.mark.parametrize("fmt,build,walk", ALL, ids=[a[0] for a in ALL])
def test_each_is_identified_and_walked(tmp_path, fmt, build, walk):
    path = _w(tmp_path, "t." + fmt, build())
    assert sniff.sniff(path) == fmt
    chunks, _warns = walk(path)
    assert chunks


@pytest.mark.parametrize("fmt,build,walk", ALL, ids=[a[0] for a in ALL])
def test_every_byte_is_accounted_for(tmp_path, fmt, build, walk):
    blob = build()
    path = _w(tmp_path, "t." + fmt, blob)
    chunks, _warns = walk(path)
    geometry.normalize(chunks, len(blob))
    assert all(geometry.is_trustworthy(c) for c in chunks)
    covered = sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                  for c in chunks)
    assert covered == len(blob), (covered, len(blob))


@pytest.mark.parametrize("fmt,build,walk", ALL, ids=[a[0] for a in ALL])
@pytest.mark.parametrize("n", [0, 4, 8, 64, 127, 128])
def test_truncation_at_any_depth_does_not_raise(tmp_path, fmt, build, walk, n):
    path = _w(tmp_path, "cut." + fmt, build()[:n])
    try:
        walk(path)
    except Exception as exc:               # noqa: BLE001 - that IS the assertion
        pytest.fail("%s truncated to %d bytes raised %r" % (fmt, n, exc))


# ── NSF: the fields that do not mean what they look like ────────────

def test_the_load_address_stops_being_an_address_when_banked(tmp_path):
    """The one genuine trap in the NSF header. With any bank byte non-zero the
    low 12 bits of the load address are a count of padding bytes at the start of
    the ROM, not a place to put it. There is no flag saying so -- the all-zero
    bank array IS the flag."""
    path = _w(tmp_path, "b.nsf", _nsf(load=0x8123, banks=bytes([0, 1, 2, 3, 4, 5, 6, 7])))
    chunks, _ = chiptune.inspect_nsf(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["romPadding"] == "291", "0x123 = 291 bytes of padding"


def test_an_unbanked_file_reports_no_padding(tmp_path):
    """The control. romPadding appearing on a file with an all-zero bank array
    would mean the walker read a pad count out of a plain address."""
    path = _w(tmp_path, "u.nsf", _nsf(load=0x8123, banks=bytes(8)))
    chunks, _ = chiptune.inspect_nsf(path)
    assert "romPadding" not in {f["name"] for f in chunks[0]["fields"]}


def test_string_slots_are_fixed_width_not_nul_scanned(tmp_path):
    """A slot is always 32 bytes; the NUL ends the text inside it. A walker that
    scans for the NUL instead of capping at the slot runs a 32-non-NUL title
    straight into the artist field."""
    path = _w(tmp_path, "s.nsf", _nsf(title=b"A" * 32, artist=b"Artist"))
    chunks, warns = chiptune.inspect_nsf(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["title"] == "A" * 32, "title must stop at the slot boundary"
    assert got["artist"] == "Artist", "the artist slot must be unaffected"
    assert any("no terminator" in w for w in warns)


def test_dirty_padding_after_the_terminator_is_reported(tmp_path):
    """Rippers leave remnants of a previous string after the NUL. Legal, and
    forensically interesting, so it is said rather than silently trimmed."""
    title = b"Short\x00LEFTOVER"
    path = _w(tmp_path, "d.nsf", _nsf(title=title))
    chunks, _ = chiptune.inspect_nsf(path)
    note = [f["note"] for f in chunks[0]["fields"] if f["name"] == "title"][0]
    assert "after the terminator" in note


@pytest.mark.parametrize("bit,name", [(0, "VRC6"), (1, "VRC7"), (2, "FDS"),
                                      (3, "MMC5"), (4, "Namco 163"),
                                      (5, "Sunsoft 5B")])
def test_each_expansion_chip_bit(tmp_path, bit, name):
    path = _w(tmp_path, "c.nsf", _nsf(chips=1 << bit))
    chunks, _ = chiptune.inspect_nsf(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["expansion"] == name


def test_multiple_expansion_chips_are_all_named(tmp_path):
    path = _w(tmp_path, "m.nsf", _nsf(chips=0x01 | 0x04))
    chunks, _ = chiptune.inspect_nsf(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["expansion"] == "VRC6, FDS"


def test_an_fds_load_below_8000_is_not_flagged_but_a_plain_one_is(tmp_path):
    """FDS rips legitimately load below $8000 to fill $6000-$7FFF. The same
    address without the FDS bit is suspect. A check that fires on both, or on
    neither, would be telling the reader nothing."""
    fds = _w(tmp_path, "fds.nsf", _nsf(load=0x6000, chips=0x04))
    plain = _w(tmp_path, "plain.nsf", _nsf(load=0x6000, chips=0x00))
    _c, fds_warns = chiptune.inspect_nsf(fds)
    _c, plain_warns = chiptune.inspect_nsf(plain)
    assert not any("below $8000" in w for w in fds_warns), fds_warns
    assert any("below $8000" in w for w in plain_warns), plain_warns


def test_version_1_reserved_byte_is_reported_not_parsed(tmp_path):
    """$7C is NSF2 feature flags in a v2 file and reserved in a v1 file. The
    spec says ignore it when version is 1 -- so it must not be decoded as flags,
    but a non-zero reserved byte is still a fingerprint worth surfacing."""
    path = _w(tmp_path, "r.nsf", _nsf(ver=1, nsf2flags=0x90))
    chunks, _ = chiptune.inspect_nsf(path)
    names = {f["name"] for f in chunks[0]["fields"]}
    assert "reserved" in names and "nsf2Flags" not in names


def test_version_2_decodes_the_same_byte_as_flags(tmp_path):
    """The control for the above: same byte, different version, different
    meaning."""
    path = _w(tmp_path, "v2.nsf", _nsf(ver=2, nsf2flags=0x90))
    chunks, _ = chiptune.inspect_nsf(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert "IRQ" in got["nsf2Flags"] and "mandatory metadata" in got["nsf2Flags"]


def test_a_version_beyond_2_is_corruption_not_a_newer_format(tmp_path):
    """NSF has only ever had version bytes 1 and 2. There is no 1.01/1.02/1.03
    ladder -- that is PSID, a different format this repo also reads. So a high
    version byte means damage, and being permissive about it would be wrong."""
    path = _w(tmp_path, "v9.nsf", _nsf(ver=9))
    _chunks, warns = chiptune.inspect_nsf(path)
    assert any("only 1 and 2" in w for w in warns), warns


def test_a_declared_data_length_past_the_end_is_refused(tmp_path):
    path = _w(tmp_path, "long.nsf", _nsf(data_len=0xFFFFFF))
    chunks, warns = chiptune.inspect_nsf(path)
    assert any("past the end" in w for w in warns), warns
    size = os.path.getsize(path)
    geometry.normalize(chunks, size)
    assert all(geometry.is_trustworthy(c) for c in chunks)


def test_nsf2_mandatory_metadata_with_no_length_is_a_contradiction(tmp_path):
    path = _w(tmp_path, "x.nsf", _nsf(ver=2, nsf2flags=0x80, data_len=0))
    _chunks, warns = chiptune.inspect_nsf(path)
    assert any("no stated boundary" in w for w in warns), warns


def test_all_unknown_strings_is_a_bare_rip_not_damage(tmp_path):
    path = _w(tmp_path, "q.nsf", _nsf(title=b"<?>", artist=b"<?>", copyright=b"<?>"))
    _chunks, warns = chiptune.inspect_nsf(path)
    assert any("bare rip" in w for w in warns), warns


def test_a_zero_speed_word_is_only_flagged_for_the_declared_region(tmp_path):
    """A PAL speed of zero on an NTSC-only tune is harmless: nothing reads it.
    Flagging it anyway trains the reader to ignore the warning."""
    ntsc = _w(tmp_path, "n.nsf", _nsf(region=0x00, pal=0))
    palf = _w(tmp_path, "p.nsf", _nsf(region=0x01, pal=0))
    _c, ntsc_warns = chiptune.inspect_nsf(ntsc)
    _c, pal_warns = chiptune.inspect_nsf(palf)
    assert not any("PAL speed" in w for w in ntsc_warns), ntsc_warns
    assert any("PAL speed" in w for w in pal_warns), pal_warns


# ── NSFe: length before FourCC, and capitalisation carries meaning ──

def test_chunk_length_precedes_the_fourcc(tmp_path):
    """The NSFe trap. RIFF and IFF put the ID first; NSFe puts the length first.
    Plumbing borrowed from either reads a FourCC as a size and walks into
    nothing. This asserts the walker got the order right by giving DATA a length
    that would be absurd if read as an ID."""
    blob = _nsfe([_chunk(b"INFO", _info()), _chunk(b"DATA", b"\xea" * 300),
                  _chunk(b"NEND", b"")])
    path = _w(tmp_path, "o.nsfe", blob)
    chunks, warns = chiptune.inspect_nsfe(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["DATA"] == "300 bytes"
    assert not warns


def test_an_unknown_capitalised_chunk_is_surfaced_loudly(tmp_path):
    """Mandatoriness is encoded in the FIRST BYTE'S CASE. A-Z means a player
    that does not understand the chunk must refuse the file, so an unknown one
    is the format saying "you do not understand me"."""
    blob = _nsfe([_chunk(b"INFO", _info()), _chunk(b"DATA", b"\xea"),
                  _chunk(b"ZZZZ", b"x"), _chunk(b"NEND", b"")])
    path = _w(tmp_path, "z.nsfe", blob)
    _chunks, warns = chiptune.inspect_nsfe(path)
    assert any("MANDATORY" in w and "ZZZZ" in w for w in warns), warns


def test_an_unknown_lowercase_chunk_is_skippable_and_quiet(tmp_path):
    """The control that makes the case rule mean something. Same unknown chunk,
    lowercase initial, no complaint."""
    blob = _nsfe([_chunk(b"INFO", _info()), _chunk(b"DATA", b"\xea"),
                  _chunk(b"zzzz", b"x"), _chunk(b"NEND", b"")])
    path = _w(tmp_path, "l.nsfe", blob)
    _chunks, warns = chiptune.inspect_nsfe(path)
    assert not any("MANDATORY" in w for w in warns), warns


def test_a_chunk_length_past_the_end_stops_the_walk(tmp_path):
    """A 32-bit length can claim 4 GB. This is the primary structural check and
    the primary denial-of-service vector."""
    blob = b"NSFE" + struct.pack("<I", 0x7FFFFFFF) + b"DATA" + b"\xea" * 8
    path = _w(tmp_path, "big.nsfe", blob)
    _chunks, warns = chiptune.inspect_nsfe(path)
    assert any("runs past the end" in w for w in warns), warns


def test_missing_required_chunks_are_named(tmp_path):
    path = _w(tmp_path, "bare.nsfe", _nsfe([_chunk(b"auth", b"x\x00")]))
    _chunks, warns = chiptune.inspect_nsfe(path)
    for need in ("INFO", "DATA", "NEND"):
        assert any(need in w for w in warns), (need, warns)


def test_data_before_info_is_flagged(tmp_path):
    blob = _nsfe([_chunk(b"DATA", b"\xea"), _chunk(b"INFO", _info()),
                  _chunk(b"NEND", b"")])
    path = _w(tmp_path, "ord.nsfe", blob)
    _chunks, warns = chiptune.inspect_nsfe(path)
    assert any("before INFO" in w for w in warns), warns


def test_bytes_after_nend_are_reported(tmp_path):
    blob = _nsfe([_chunk(b"INFO", _info()), _chunk(b"DATA", b"\xea"),
                  _chunk(b"NEND", b"")]) + b"junkjunk"
    path = _w(tmp_path, "tail.nsfe", blob)
    _chunks, warns = chiptune.inspect_nsfe(path)
    assert any("follow NEND" in w for w in warns), warns


# ── SAP: text then a self-describing binary ─────────────────────────

def test_the_block_end_address_is_inclusive(tmp_path):
    """`end - start` loses the last byte of every block in the file, and the
    file still parses, so nothing complains. 0x0F80..0x0F8F is 16 bytes."""
    path = _w(tmp_path, "i.sap", _sap(blocks=((0x0F80, b"\xea" * 16),)))
    chunks, _ = chiptune.inspect_sap(path)
    blk = [f for f in chunks[1]["fields"] if f["name"] == "block 1"][0]
    assert blk["value"] == "$0F80-$0F8F"
    assert "16 bytes" in blk["note"]


def test_the_text_header_ends_at_the_first_ff_ff(tmp_path):
    """The spec defines NO end-of-header marker. The boundary is derived: the
    permitted character set tops out at 0x7C so 0xFF cannot occur in the header,
    and the binary half must open FF FF."""
    blob = _sap()
    path = _w(tmp_path, "b.sap", blob)
    chunks, _ = chiptune.inspect_sap(path)
    assert chunks[1]["offset"] == blob.index(b"\xff\xff")


def test_a_sap_with_no_binary_half_says_so(tmp_path):
    path = _w(tmp_path, "n.sap", _sap(blocks=(), ffff=False))
    chunks, warns = chiptune.inspect_sap(path)
    assert len(chunks) == 1
    assert any("never begins" in w for w in warns), warns


@pytest.mark.parametrize("typ,desc", [("B", "standard"), ("C", "Chaos"),
                                      ("D", "digitised"), ("S", "SoftSynth"),
                                      ("R", "raw POKEY")])
def test_every_defined_player_type_is_known(tmp_path, typ, desc):
    tags = ['AUTHOR "x"', "TYPE " + typ, "SONGS 1", "TIME 01:00"]
    tags.append("MUSIC 2000" if typ == "C" else "INIT 0F80")
    if typ in ("B", "C"):
        tags.append("PLAYER 3000")
    path = _w(tmp_path, "t.sap", _sap(tags=tags))
    chunks, _warns = chiptune.inspect_sap(path)
    note = [f for f in chunks[0]["fields"] if f["name"] == "type"][0]["note"]
    assert desc in note


def test_type_c_must_not_carry_init(tmp_path):
    """The spec says INIT is INVALID for type C, not merely unnecessary."""
    tags = ['AUTHOR "x"', "TYPE C", "MUSIC 2000", "INIT 0F80", "SONGS 1",
            "TIME 01:00"]
    path = _w(tmp_path, "c.sap", _sap(tags=tags))
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("must not carry INIT" in w for w in warns), warns


def test_type_b_requires_init(tmp_path):
    tags = ['AUTHOR "x"', "TYPE B", "PLAYER 3000", "SONGS 1", "TIME 01:00"]
    path = _w(tmp_path, "nb.sap", _sap(tags=tags))
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("requires INIT" in w for w in warns), warns


def test_one_time_line_per_subsong(tmp_path):
    tags = ['AUTHOR "x"', "TYPE B", "INIT 0F80", "PLAYER 3000", "SONGS 3",
            "TIME 01:00", "TIME 02:00"]
    path = _w(tmp_path, "tm.sap", _sap(tags=tags))
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("2 TIME line(s) for 3 song(s)" in w for w in warns), warns


def test_a_block_ending_before_it_starts_is_refused(tmp_path):
    head = b"SAP\r\nAUTHOR \"x\"\r\nTYPE B\r\nINIT 0F80\r\nPLAYER 3000\r\n"
    body = b"\xff\xff" + struct.pack("<HH", 0x2000, 0x1000) + b"\xea" * 8
    path = _w(tmp_path, "rev.sap", head + body)
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("before it starts" in w for w in warns), warns


def test_a_block_running_past_the_end_is_reported_not_raised(tmp_path):
    """The spec calls this malformed but notes players accept it, so it is a
    warning and the walk still returns what it read."""
    head = b"SAP\r\nAUTHOR \"x\"\r\nTYPE B\r\nINIT 0F80\r\nPLAYER 3000\r\n"
    body = b"\xff\xff" + struct.pack("<HH", 0x2000, 0x2FFF) + b"\xea" * 8
    path = _w(tmp_path, "cut.sap", head + body)
    chunks, warns = chiptune.inspect_sap(path)
    assert any("ends mid-block" in w for w in warns), warns
    assert len(chunks) == 2


def test_a_block_loading_into_hardware_registers_is_flagged(tmp_path):
    head = b"SAP\r\nAUTHOR \"x\"\r\nTYPE B\r\nINIT 0F80\r\nPLAYER 3000\r\n"
    body = b"\xff\xff" + struct.pack("<HH", 0xD200, 0xD20F) + b"\xea" * 16
    path = _w(tmp_path, "hw.sap", head + body)
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("hardware register" in w for w in warns), warns


def test_covox_only_accepts_d600(tmp_path):
    tags = ['AUTHOR "x"', "TYPE B", "INIT 0F80", "PLAYER 3000", "SONGS 1",
            "TIME 01:00", "COVOX D700"]
    path = _w(tmp_path, "cv.sap", _sap(tags=tags))
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("D600" in w for w in warns), warns


def test_bare_lf_line_endings_are_parsed_but_reported(tmp_path):
    """The spec asks for CR LF and says nothing about tolerance. Real files are
    unmeasured here, so the walker parses tolerantly and reports the deviation
    rather than refusing -- the forensic signal without the false negative."""
    blob = _sap(eol="\n")
    blob = b"SAP\r\n" + blob[blob.index(b"\n") + 1:]
    path = _w(tmp_path, "lf.sap", blob)
    chunks, warns = chiptune.inspect_sap(path)
    assert chunks[0]["fields"], "the header still parsed"
    assert any("CR LF" in w for w in warns), warns


def test_a_byte_outside_the_atascii_set_is_flagged(tmp_path):
    blob = _sap(tags=['AUTHOR "x"', "TYPE B", "INIT 0F80", "PLAYER 3000",
                      "NAME \"a~b\""])
    path = _w(tmp_path, "as.sap", blob)
    _chunks, warns = chiptune.inspect_sap(path)
    assert any("ATASCII" in w for w in warns), warns


def test_a_file_that_is_not_an_nsf_is_refused(tmp_path):
    """Found by the corpus, not by me.

    The NSFe and SAP walkers both check their magic; this one only checked
    length, so any file of at least 128 bytes parsed and produced a confident
    title, artist and load address out of whatever bytes were there. A real
    corpus of 13,042 .nsf files held 50 that are HTML error pages or macOS
    AppleDouble resource forks wearing the extension, and every one of them
    walked without complaint.
    """
    p = tmp_path / "notreally.nsf"
    p.write_bytes(b"<html><head><title>404</title></head>" + b"x" * 200)
    chunks, warns = chiptune.inspect_nsf(str(p))
    assert "not an NSF" in chunks[0]["summary"]
    assert not chunks[0]["fields"], "no field may be reported from non-NSF bytes"
    assert any("4E 45 53 4D 1A" in w for w in warns), warns


def test_the_three_walkers_all_check_their_magic(tmp_path):
    """The invariant behind the bug above: a walker that does not verify its
    signature will describe anything. Asserted for all three so the next one
    added cannot quietly skip it."""
    junk = b"\x99" * 512
    for name, walk in (("x.nsf", chiptune.inspect_nsf),
                       ("x.nsfe", chiptune.inspect_nsfe),
                       ("x.sap", chiptune.inspect_sap)):
        p = tmp_path / name
        p.write_bytes(junk)
        chunks, warns = walk(str(p))
        assert warns, "%s accepted 512 junk bytes silently" % name
        assert not chunks[0]["fields"], "%s reported fields from junk" % name


# ── GBS: NSF with nothing reserved ──────────────────────────────────

def test_gbs_header_decodes(tmp_path):
    chunks, warns = chiptune.inspect_gbs(_w(tmp_path, "t.gbs", _gbs()))
    vals = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert vals["songs"] == 4 and vals["firstSong"] == 1
    assert vals["load"] == "$0400" and vals["play"] == "$0410"
    assert vals["title"] == "SEED" and vals["author"] == "NOBODY"
    assert not warns


def test_gbs_needs_the_version_byte_to_be_identified(tmp_path):
    """"GBS" opens ordinary text too. The version byte is checked with it,
    and every real file is version 1."""
    blob = bytearray(_gbs())
    blob[3] = 2
    assert sniff.sniff(_w(tmp_path, "t.gbs", bytes(blob))) != "gbs"
    _chunks, warns = chiptune.inspect_gbs(_w(tmp_path, "u.gbs", bytes(blob)))
    assert any("version 2" in w for w in warns)


def test_gbs_addresses_outside_the_cartridge_window_warn(tmp_path):
    """Load, init and play must fall in $0400-$7FFF, the cartridge ROM window
    once the boot ROM is paged out. The spec says so in as many words."""
    _chunks, warns = chiptune.inspect_gbs(_w(tmp_path, "t.gbs", _gbs(load=0x0070)))
    assert any("outside the cartridge window" in w and "load" in w for w in warns)


def test_gbs_code_past_ffff_warns(tmp_path):
    _chunks, warns = chiptune.inspect_gbs(
        _w(tmp_path, "t.gbs", _gbs(load=0x7000, code=0x9010)))
    assert any("past $FFFF" in w for w in warns)


def test_gbs_first_song_outside_the_count_warns(tmp_path):
    _chunks, warns = chiptune.inspect_gbs(_w(tmp_path, "t.gbs", _gbs(songs=3, first=7)))
    assert any("first song 7" in w for w in warns)


def test_gbs_a_full_slot_with_no_nul_is_named_not_run_into(tmp_path):
    """The three text slots are always 32 bytes. A slot with no NUL is
    reported, and its text does not run into the next slot."""
    blob = _gbs(title="X" * 32)
    chunks, warns = chiptune.inspect_gbs(_w(tmp_path, "t.gbs", blob))
    vals = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert vals["title"] == "X" * 32
    assert vals["author"] == "NOBODY"
    assert any("title fills all 32 bytes" in w for w in warns)


def test_gbs_code_chunk_covers_the_rest_and_names_its_range(tmp_path):
    blob = _gbs(load=0x1000, code=0x200)
    chunks, _warns = chiptune.inspect_gbs(_w(tmp_path, "t.gbs", blob))
    code = next(c for c in chunks if c["id"] == "code")
    assert code["offset"] == 0x70 and code["size"] == 0x200
    assert "$1000-$11FF" in code["summary"]


# ── opt-in: the real corpora ────────────────────────────────────────
#
# Everything above this line is synthetic and therefore only proves the walker
# agrees with my reading of the spec. These four are the ones that can tell me
# the reading was wrong.
#
#   ACIDCAT_NSF_CORPUS   a tree of .nsf / .nsfe (the MrNorbert1994 or Gr8NSF set)
#   ACIDCAT_SAP_CORPUS   a tree of .sap (ASMA)

def _corpus(var, exts):
    import glob
    root = os.environ.get(var)
    if not root:
        return []
    out = []
    for e in exts:
        out += glob.glob(os.path.join(root, "**", "*" + e), recursive=True)
    return sorted(out)


NSF_CORPUS = "ACIDCAT_NSF_CORPUS"
SAP_CORPUS = "ACIDCAT_SAP_CORPUS"
GBS_CORPUS = "ACIDCAT_GBS_CORPUS"


@pytest.mark.skipif(not os.environ.get(GBS_CORPUS),
                    reason="set ACIDCAT_GBS_CORPUS to a dir of real .gbs files")
def test_real_gbs_corpus_walks_completely():
    from acidcat.core.walk import walk_file
    files = _corpus(GBS_CORPUS, [".gbs", ".GBS"])
    assert len(files) >= 30, "only %d files" % len(files)
    seen = tiled = 0
    for path in files:
        if sniff.sniff(path) != "gbs":
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
    assert seen == len(files), "%d of %d not identified" % (len(files) - seen, len(files))
    assert tiled == seen, "%d of %d did not tile" % (seen - tiled, seen)


@pytest.mark.skipif(not os.environ.get(NSF_CORPUS),
                    reason="set ACIDCAT_NSF_CORPUS to a dir of real .nsf/.nsfe")
def test_the_real_nsf_corpus_walks_completely():
    """Measured on 13,042 .nsf and 1,682 .nsfe: nothing raised, nothing
    produced untrustworthy geometry, and every file was covered end to end.

    Coverage rather than a crash count, because a walker that quietly stopped
    early would pass the second and fail the first.

    This corpus is also where the walker's worst bug was found. `inspect_nsf`
    checked its length and not its magic, so 50 files that are HTML error pages
    and macOS AppleDouble forks wearing a .nsf extension all parsed, and
    reported a title and a load address out of bytes that were never a header.
    """
    from acidcat.core.walk import walk_file
    files = _corpus(NSF_CORPUS, (".nsf", ".nsfe"))
    assert files, "no .nsf/.nsfe under $" + NSF_CORPUS
    walked = 0
    for p in files:
        if sniff.sniff(p) not in ("nsf", "nsfe"):
            continue                       # impostors: correctly refused
        _label, chunks, _warns = walk_file(p)
        size = os.path.getsize(p)
        geometry.normalize(chunks, size)
        assert all(geometry.is_trustworthy(c) for c in chunks), p
        covered = sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                      for c in chunks)
        assert covered == size, (p, covered, size)
        walked += 1
    assert walked > len(files) * 0.9, (
        "only %d of %d walked; a sweep that skipped most of the corpus reports "
        "the same clean result as one that checked it" % (walked, len(files)))


@pytest.mark.skipif(not os.environ.get(SAP_CORPUS),
                    reason="set ACIDCAT_SAP_CORPUS to a dir of real .sap files")
def test_the_real_sap_corpus_walks_completely():
    """Measured on ASMA, 6,335 tunes: nothing raised, geometry sound on all.

    The claim worth pinning is the boundary. SAP defines no end-of-header
    marker at all -- the rule that the text stops at the first FF FF is derived
    from the character set, not quoted from the spec. Every file in ASMA splits
    where that rule says it does.
    """
    from acidcat.core.walk import walk_file
    files = _corpus(SAP_CORPUS, (".sap",))
    assert files, "no .sap under $" + SAP_CORPUS
    for p in files:
        _label, chunks, _warns = walk_file(p)
        size = os.path.getsize(p)
        geometry.normalize(chunks, size)
        assert all(geometry.is_trustworthy(c) for c in chunks), p
        covered = sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                      for c in chunks)
        assert covered == size, (p, covered, size)


@pytest.mark.skipif(not os.environ.get(NSF_CORPUS),
                    reason="set ACIDCAT_NSF_CORPUS to a dir of real .nsf/.nsfe")
def test_every_real_nsfe_carries_nend():
    """Both specs call NEND mandatory and neither had been measured.

    1,682 of 1,682 carry it, so treating its absence as damage rather than as a
    tolerated omission is justified by the corpus rather than only by the text.
    """
    import glob
    root = os.environ[NSF_CORPUS]
    files = sorted(glob.glob(os.path.join(root, "**", "*.nsfe"), recursive=True))
    assert files, "no .nsfe under $" + NSF_CORPUS
    missing = []
    for p in files:
        chunks, _warns = chiptune.inspect_nsfe(p)
        if "NEND" not in {f["name"] for f in chunks[0]["fields"]}:
            missing.append(p)
    assert not missing, "%d of %d NSFe files carry no NEND: %s" % (
        len(missing), len(files), [os.path.basename(m) for m in missing[:5]])


@pytest.mark.skipif(not os.environ.get(NSF_CORPUS),
                    reason="set ACIDCAT_NSF_CORPUS to a dir of real .nsf/.nsfe")
def test_the_walker_refuses_files_that_only_wear_the_extension():
    """The regression guard for the magic-check bug, held against the real set.

    A corpus is full of files that are not what their name says. Anything the
    sniffer will not call an NSF must not produce fields from the walker either
    -- otherwise the two disagree, and the walker is the one making things up.
    """
    files = _corpus(NSF_CORPUS, (".nsf",))
    assert files, "no .nsf under $" + NSF_CORPUS
    impostors = [p for p in files if sniff.sniff(p) != "nsf"]
    for p in impostors:
        chunks, warns = chiptune.inspect_nsf(p)
        assert not chunks[0]["fields"], (
            "%s is not an NSF but the walker described one" % os.path.basename(p))
        assert warns, os.path.basename(p)
