"""Tracker-module walkers (MOD/XM/IT) on synthetic ground-truth files, plus the
IT/MP4/WAV pointer (xref) annotations."""
import struct

from acidcat.core.infra import sniff
from acidcat.core.walk import tracker as wtk


# ── MOD ────────────────────────────────────────────────────────────

def _make_mod():
    title = b"TEST".ljust(20, b"\x00")
    smp_hdrs = []
    for i in range(31):
        name = b"snare".ljust(22, b"\x00") if i == 0 else b"\x00" * 22
        length_words = 10 if i == 0 else 0          # 10 words = 20 bytes
        smp_hdrs.append(name + struct.pack(">H", length_words)
                        + bytes([0, 64]) + struct.pack(">HH", 0, 1))
    order = bytes([0]) + b"\x00" * 127              # one pattern, index 0
    body = title + b"".join(smp_hdrs) + bytes([1, 127]) + order + b"M.K."
    body += b"\x00" * (64 * 4 * 4)                  # one 4-channel pattern
    body += bytes(range(20))                        # sample 0 PCM
    return body


def test_mod_detect_and_walk(tmp_path):
    p = tmp_path / "x.mod"
    p.write_bytes(_make_mod())
    assert sniff.sniff(str(p)) == "mod"
    chunks, warns = wtk.inspect_mod(str(p))
    head = chunks[0]
    assert head["id"] == "MOD"
    assert "4ch" in head["summary"] and "M.K." in head["summary"]
    smp = [c for c in chunks if c["id"].startswith("smp")]
    assert len(smp) == 1
    # sample data sits right after the single 1024-byte pattern
    assert smp[0]["offset"] == 1084 + 1024
    assert smp[0]["size"] == 20
    assert not warns


def _make_mod15(samples=((b"ST-01:DigDug", 10),), patterns=1, trailing=0,
                tempo=120, loop=(0, 1)):
    """The 15-instrument Soundtracker layout: 20-byte title, 15 headers,
    song length, tempo, 128 orders, patterns at 600. No magic anywhere."""
    title = b"OLD".ljust(20, b"\x00")
    hdrs = []
    for i in range(15):
        name, words = samples[i] if i < len(samples) else (b"", 0)
        hdrs.append(name.ljust(22, b"\x00") + struct.pack(">H", words)
                    + bytes([0, 64]) + struct.pack(">HH", *loop))
    order = bytes(range(patterns)).ljust(128, b"\x00")
    body = title + b"".join(hdrs) + bytes([patterns, tempo]) + order
    assert len(body) == 600
    body += b"\x00" * (1024 * patterns)
    for _name, words in samples:
        body += bytes(range(words * 2))
    return body + b"\x00" * trailing


def test_a_15_instrument_module_is_identified_by_arithmetic(tmp_path):
    """No magic: 600 + patterns * 1024 + samples has to be the file size.
    1,914 of 1,935 real ones add up; zero false positives in 214,483 files
    that are not one."""
    p = tmp_path / "old.mod"
    p.write_bytes(_make_mod15())
    assert sniff.sniff(str(p)) == "mod"
    chunks, warns = wtk.inspect_mod(str(p))
    head = chunks[0]
    assert "Soundtracker MOD, 15 instruments" in head["summary"]
    fields = {f["name"]: f for f in head["fields"]}
    assert fields["instruments"]["value"] == 15
    assert fields["song_length"]["off"] == 470
    assert fields["tempo"]["off"] == 471 and fields["tempo"]["value"] == 120
    assert "magic" not in fields
    assert chunks[1]["id"] == "order" and chunks[1]["offset"] == 472
    smp = [c for c in chunks if c["id"].startswith("smp")]
    assert len(smp) == 1 and smp[0]["offset"] == 600 + 1024 and smp[0]["size"] == 20
    assert not warns


def test_the_pattern_count_comes_from_every_order_slot():
    """121 of 1,935 real files keep patterns past the song length."""
    from acidcat.core.formats import tracker as tk
    blob = bytearray(_make_mod15(patterns=3))
    blob[470] = 1                                  # song plays one position
    assert tk.is_mod15(bytes(blob), len(blob))
    assert tk.parse_mod(bytes(blob))["num_patterns"] == 3


def test_a_size_that_does_not_add_up_is_not_a_module(tmp_path):
    from acidcat.core.formats import tracker as tk
    good = _make_mod15()
    assert tk.is_mod15(good, len(good))
    assert not tk.is_mod15(good, len(good) - 1)              # truncated
    assert not tk.is_mod15(good, len(good) + 1025)           # too much after
    assert tk.is_mod15(good, len(good) + 1024)               # a little is tolerated
    bad = bytearray(good)
    bad[20 + 25] = 65                                        # volume past 64
    assert not tk.is_mod15(bytes(bad), len(bad))
    # a real ProTracker file is never read as the old layout
    assert not tk.is_mod15(_make_mod(), len(_make_mod()))


def test_trailing_bytes_are_said_on_the_container(tmp_path):
    p = tmp_path / "old.mod"
    p.write_bytes(_make_mod15(trailing=8))
    chunks, _w = wtk.inspect_mod(str(p))
    assert any("8 bytes after the last sample" in w for w in chunks[0]["warnings"])


def test_the_old_layout_keeps_its_repeat_point_in_bytes():
    """Ultimate Soundtracker wrote bytes, ProTracker words. 858 real looped
    samples fit only as bytes; none fit only as words."""
    from acidcat.core.formats import tracker as tk
    old = tk.parse_mod(_make_mod15(loop=(6, 2)))
    assert old["samples"][0]["loop_start"] == 6
    new = tk.parse_mod(_make_mod())
    assert new["instruments"] == 31


# ── XM ─────────────────────────────────────────────────────────────

def _make_xm():
    hdr = b"Extended Module: "
    hdr += b"song".ljust(20, b"\x00") + b"\x1a" + b"acidcat".ljust(20, b"\x00")
    hdr += struct.pack("<H", 0x0104)
    hdr += struct.pack("<I", 276)                   # header size from offset 60
    hdr += struct.pack("<HHHHHHHH", 1, 0, 4, 1, 1, 0, 6, 125)  # body fields
    hdr += b"\x00" * 256                            # order table
    # one pattern: header len 9, packing 0, rows 64, packed 0 bytes
    hdr += struct.pack("<IBHH", 9, 0, 64, 0)
    # one instrument: minimal 29-byte header, 1 sample
    hdr += struct.pack("<I", 29) + b"lead".ljust(22, b"\x00") + bytes([0]) \
        + struct.pack("<H", 1)
    # one 40-byte sample header: length 20, type 0 (8-bit), name at +18
    hdr += struct.pack("<I", 20) + b"\x00" * 14 + b"kick".ljust(22, b"\x00")
    hdr += bytes(range(20))                         # sample PCM
    return hdr


def test_xm_detect_and_walk(tmp_path):
    p = tmp_path / "x.xm"
    p.write_bytes(_make_xm())
    assert sniff.sniff(str(p)) == "xm"
    chunks, warns = wtk.inspect_xm(str(p))
    assert chunks[0]["id"] == "XM"
    assert "8ch" not in chunks[0]["summary"] and "4ch" in chunks[0]["summary"]
    smp = [c for c in chunks if c["id"].startswith("smp")]
    assert len(smp) == 1 and smp[0]["size"] == 20
    assert not warns


# ── IT (with xref) ─────────────────────────────────────────────────

def _make_it():
    body = b"IMPM" + b"song".ljust(26, b"\x00")
    body += struct.pack("<H", 0)                    # philight
    body += struct.pack("<HHHH", 2, 0, 1, 0)        # ord/ins/smp/pat
    body += struct.pack("<HH", 0x0214, 0x0200)      # cwt/cmwt
    body += struct.pack("<H", 0x0009)               # flags
    body += struct.pack("<H", 0)                    # special
    body += bytes([128, 48, 6, 125, 128, 0])        # gv,mv,is,it,sep,pwd
    body += struct.pack("<H", 0) + struct.pack("<I", 0) + b"\x00" * 4  # msg + reserved
    body += b"\x00" * 128                            # channel pan + vol
    body += bytes([0, 0])                            # order (ordnum=2)
    # sample-offset table (1 entry) points at the IMPS header
    imps_off = 194 + 4                              # after the 4-byte table
    body += struct.pack("<I", imps_off)
    assert len(body) == imps_off
    # IMPS header (80 bytes)
    imps = b"IMPS" + b"kick.wav".ljust(12, b"\x00") + bytes([0, 64, 0x01, 64])
    imps += b"kick".ljust(26, b"\x00") + bytes([0, 32])
    imps += struct.pack("<I", 20)                   # length (points)
    imps += struct.pack("<II", 0, 0)                # loop
    imps += struct.pack("<I", 8000)                 # C5
    imps += struct.pack("<II", 0, 0)                # sus loop
    data_off = imps_off + 80
    imps += struct.pack("<I", data_off)             # sample pointer
    imps += bytes([0, 0, 0, 0])                     # vibrato
    assert len(imps) == 80
    body += imps + bytes(range(20))
    return body, imps_off, data_off


def test_it_detect_walk_and_xref(tmp_path):
    raw, imps_off, data_off = _make_it()
    p = tmp_path / "x.it"
    p.write_bytes(raw)
    assert sniff.sniff(str(p)) == "it"
    chunks, warns = wtk.inspect_it(str(p))
    assert chunks[0]["id"] == "IMPM"
    # the sample-offset table entry xrefs the IMPS header
    tbl = next(c for c in chunks if c["id"] == "smp_offsets")
    assert tbl["fields"][0]["xref"] == imps_off
    # the IMPS header's sample_pointer xrefs the PCM
    smp = next(c for c in chunks if c["id"].startswith("smp["))
    ptr = next(f for f in smp["fields"] if f["name"] == "sample_pointer")
    assert ptr["xref"] == data_off
    assert not warns


def test_truncated_headers_degrade(tmp_path):
    """A valid magic followed by a header too short for the fixed struct must
    degrade with a warning, never raise -- the crash class the external audit
    found (walk_file has no top-level guard, so a raise crashes audit/info)."""
    s3m = (b"\x00" * 0x1C + b"\x1a\x10" + b"\x00" * 14 + b"SCRM").ljust(50, b"\x00")
    cases = [
        ("t.xm", b"Extended Module: " + b"\x00" * 30, wtk.inspect_xm),
        ("t.it", b"IMPM" + b"\x00" * 20, wtk.inspect_it),
        ("t.s3m", s3m, wtk.inspect_s3m),
    ]
    for fn, data, inspect in cases:
        p = tmp_path / fn
        p.write_bytes(data)
        chunks, warns = inspect(str(p))              # must not raise
        assert chunks and warns
        assert any("truncated" in w for w in warns)


ZERO = bytes([0])


# -- Scream Tracker 2 -------------------------------------------------

def _stm(song=b"a song", tracker=b"!Scream!", ftype=2, major=2, minor=21,
         tempo=96, patterns=1, gvol=64, instruments=None, order=(0, 99)):
    """A Scream Tracker 2 module, laid out as the format fixes it."""
    head = (song.ljust(20, ZERO) + tracker.ljust(8, ZERO)
            + bytes([0x1A, ftype, major, minor, tempo, patterns, gvol])
            + bytes(13))
    assert len(head) == 48, len(head)
    table = b""
    for i in range(31):
        spec = (instruments or {}).get(i, {})
        table += (spec.get("name", b"").ljust(12, ZERO)
                  + bytes([0, spec.get("disk", 0)])
                  + struct.pack("<HHHH", 0, spec.get("length", 0),
                                spec.get("loop_start", 0),
                                spec.get("loop_end", 65535))
                  + bytes([spec.get("volume", 64), 0])
                  + struct.pack("<H", spec.get("c2spd", 8363))
                  + bytes(6))
    orders = bytes(order).ljust(128, ZERO)
    return head + table + orders + bytes(patterns * 1024)


class TestScreamTracker2:
    """STM is the format S3M grew out of, and the one real tracker module on
    these drives acidcat could not open.

    Its header is fixed-offset throughout -- no pointer table, nothing
    variable before the instruments -- which is why the whole thing can be
    read without seeking.
    """

    def test_the_header_from_the_real_specimen(self, tmp_path):
        p = tmp_path / "a.stm"
        p.write_bytes(_stm(song=b"FuCKiN' RoTZooI!!!!!", tempo=95, patterns=8))
        chunks, warns = wtk.inspect_stm(str(p))
        f = {x["name"]: x for x in chunks[0]["fields"]}
        assert f["song_name"]["value"] == "FuCKiN' RoTZooI!!!!!"
        assert f["tracker"]["value"] == "!Scream!"
        assert f["version"]["value"] == "2.21"
        assert f["initial_tempo"]["value"] == 95
        assert f["global_volume"]["note"].endswith("full")
        assert not warns

    def test_a_non_scream_writer_is_reported_as_itself(self, tmp_path):
        """The tracker field is eight free-form characters and several
        programs wrote their own name into it, so it is reported rather than
        used as a magic."""
        p = tmp_path / "b.stm"
        p.write_bytes(_stm(tracker=b"BMOD2STM"))
        chunks, _w = wtk.inspect_stm(str(p))
        assert next(x for x in chunks[0]["fields"]
                    if x["name"] == "tracker")["value"] == "BMOD2STM"

    def test_instrument_names_are_read(self, tmp_path):
        """Scene modules use the instrument table as liner notes, so the
        names are often the only text in the file worth having."""
        p = tmp_path / "c.stm"
        p.write_bytes(_stm(instruments={0: {"name": b"By:", "length": 100},
                                        1: {"name": b"The", "length": 50}}))
        chunks, _w = wtk.inspect_stm(str(p))
        names = [c["fields"][0]["value"] for c in chunks[1:]]
        assert names[:2] == ["By:", "The"]

    def test_no_loop_is_named_not_printed_as_65535(self, tmp_path):
        """65535 in a loop end means no loop, the same sentinel the other
        trackers of the era use. Read as a length it gives a sample 64 KB
        longer than itself."""
        p = tmp_path / "d.stm"
        p.write_bytes(_stm(instruments={0: {"name": b"x", "length": 10}}))
        chunks, _w = wtk.inspect_stm(str(p))
        end = next(x for x in chunks[1]["fields"] if x["name"] == "loop_end")
        assert end["note"] == "no loop"

    def test_sample_data_past_the_end_is_reported(self, tmp_path):
        p = tmp_path / "e.stm"
        p.write_bytes(_stm(instruments={0: {"name": b"big", "length": 60000}}))
        chunks, _w = wtk.inspect_stm(str(p))
        assert any("sample data runs to" in w for w in chunks[0]["warnings"])

    def test_an_order_naming_a_pattern_that_does_not_exist(self, tmp_path):
        p = tmp_path / "f.stm"
        p.write_bytes(_stm(patterns=2, order=(0, 1, 9, 99)))
        chunks, _w = wtk.inspect_stm(str(p))
        assert any("plays pattern 9" in w for w in chunks[0]["warnings"])

    def test_a_song_that_claims_samples_is_contradicting_itself(self, tmp_path):
        """file_type 1 is a song: the patterns without the samples. One that
        declares sample lengths is making two claims that cannot both hold."""
        p = tmp_path / "g.stm"
        p.write_bytes(_stm(ftype=1, instruments={0: {"name": b"s", "length": 8}}))
        chunks, _w = wtk.inspect_stm(str(p))
        assert any("no samples" in w for w in chunks[0]["warnings"])

    def test_a_volume_outside_the_range(self, tmp_path):
        p = tmp_path / "h.stm"
        p.write_bytes(_stm(instruments={0: {"name": b"v", "length": 4,
                                            "volume": 200}}))
        chunks, _w = wtk.inspect_stm(str(p))
        assert any("outside the 0-64 range" in w for w in chunks[1]["warnings"])


# ── the real thing ──────────────────────────────────────────────────

import os
import pytest


@pytest.mark.skipif(not os.environ.get("ACIDCAT_SOUNDTRACKER_CORPUS"),
                    reason="set ACIDCAT_SOUNDTRACKER_CORPUS to a dir of 15-instrument .mod files")
def test_real_soundtracker_corpus_walks_completely():
    """Modland's Soundtracker directory: 1,935 files. 1,914 add up (or carry
    under 1 KB after the last sample); 4 are ProTracker files with a tag;
    the rest are truncated or carry more than 1 KB of trailing bytes."""
    from acidcat.core.infra import geometry
    from acidcat.core.walk import walk_file
    root = os.environ["ACIDCAT_SOUNDTRACKER_CORPUS"]
    files = [os.path.join(r, f) for r, _d, fn in os.walk(root) for f in fn
             if f.lower().endswith(".mod")]
    assert len(files) >= 100
    seen = old = 0
    for path in files:
        if sniff.sniff(path) != "mod":
            continue
        seen += 1
        _label, chunks, _w = walk_file(path)
        geometry.normalize(chunks, os.path.getsize(path))
        assert all(geometry.is_trustworthy(c) for c in chunks), path
        old += any(f["name"] == "instruments" for f in chunks[0]["fields"])
    assert seen >= len(files) * 0.98, "%d of %d not identified" % (len(files) - seen, len(files))
    assert old >= seen - 10, "%d read as ProTracker" % (seen - old)
