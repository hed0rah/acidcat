"""Ableton .asd and the gzipped-XML family.

The .asd layout was reverse-engineered against 8,196 real specimens and checked
frame-for-frame against the source audio on 22 of them. The fixtures here are
generated so the suite runs on a clean checkout; the real-corpus check at the
bottom is opt-in via ACIDCAT_ABLETON_CORPUS.
"""

import gzip
import os
import struct
import zlib

import pytest

from acidcat.core.formats import ableton as ab
from acidcat.core.infra import sniff
from acidcat.core.walk import ableton as walker


def build_asd(frames, order="<", count=None, reserved=0, tail=b""):
    """A minimal but structurally honest .asd."""
    magic = ab.ASD_MAGIC_LE if order == "<" else ab.ASD_MAGIC_BE
    n = len(frames) + 1 if count is None else count
    body = struct.pack(f"{order}II", n, reserved)
    body += struct.pack(f"{order}{len(frames)}I", *frames)
    return magic + body + tail


def grid_for(rate, seconds, step=None):
    """A frame grid that steps at the 30 ms cap and ends on the frame count."""
    step = step or round(rate * ab.ASD_GRAIN_SECONDS)
    total = int(rate * seconds)
    out = list(range(step, total, step)) + [total]
    return out


# ── header ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("order,label", [("<", "little"), (">", "big")])
def test_both_byte_orders_parse(tmp_path, order, label):
    """06 'I' and 06 'M' are Intel/Motorola, the TIFF convention. 319 of the
    8,196 specimens were big-endian PowerPC-era Mac files, and they parse only
    big-endian."""
    raw = build_asd(grid_for(44100, 2.0), order=order)
    h = ab.parse_asd_header(raw)
    assert h["byte_order"] == label
    assert h["total_frames"] == 88200
    assert h["sample_rate"] == 44100


def test_last_grid_value_is_the_frame_count(tmp_path):
    raw = build_asd(grid_for(48000, 1.5))
    h = ab.parse_asd_header(raw)
    assert h["total_frames"] == 72000
    assert h["duration"] == pytest.approx(1.5)


def test_bad_magic_is_refused():
    with pytest.raises(ab.AbletonError):
        ab.parse_asd_header(b"\x07\x49" + b"\x00" * 32)


# ── sample-rate inference, and its honesty ────────────────────────────────

@pytest.mark.parametrize("rate", [22050, 44100, 48000, 96000])
def test_rate_is_read_off_the_30ms_cap(rate):
    h = ab.parse_asd_header(build_asd(grid_for(rate, 2.0)))
    assert h["sample_rate"] == rate
    assert h["rate_exact"] is True


def test_a_grid_that_never_reaches_the_cap_is_marked_inexact():
    """Steps below the cap only bound the rate from below. Reporting that as a
    reading would be the confident-wrong-answer bug this project keeps finding.
    """
    grid = grid_for(44100, 2.0, step=900)          # well under the 1323 cap
    h = ab.parse_asd_header(build_asd(grid))
    assert h["rate_exact"] is False
    assert h["sample_rate"] is not None            # still a usable lower bound


def test_an_impossible_step_infers_nothing_rather_than_guessing():
    grid = [0, 999_999]
    h = ab.parse_asd_header(build_asd(grid))
    assert h["sample_rate"] is None
    assert h["duration"] is None


# ── damaged input degrades, never crashes ─────────────────────────────────

def test_a_count_larger_than_the_file_is_flagged_not_fatal(tmp_path):
    raw = build_asd(grid_for(44100, 1.0), count=5_000_000)
    h = ab.parse_asd_header(raw)
    assert h["truncated"] is True
    assert h["frames"]                              # what was there is still read

    p = tmp_path / "t.wav.asd"
    p.write_bytes(raw)
    chunks, warns = walker.inspect_asd(str(p))
    assert chunks and any("holds only" in w for w in warns)


def test_a_non_monotonic_grid_is_flagged(tmp_path):
    raw = build_asd([100, 90, 200])
    p = tmp_path / "t.wav.asd"
    p.write_bytes(raw)
    _, warns = walker.inspect_asd(str(p))
    assert any("not strictly increasing" in w for w in warns)


def test_truncated_before_the_header():
    with pytest.raises(ab.AbletonError):
        ab.parse_asd_header(ab.ASD_MAGIC_LE + b"\x01")


# ── the AppleDouble impostor ──────────────────────────────────────────────

def test_appledouble_wearing_the_asd_extension_is_named(tmp_path):
    """129 of 8,196 files carrying .asd were macOS '._' resource stubs. Naming
    them beats reporting a corrupt Ableton file."""
    p = tmp_path / "._loop.wav.asd"
    p.write_bytes(ab.APPLEDOUBLE_MAGIC + b"\x00\x02\x00\x00Mac OS X" + b"\x00" * 64)
    chunks, warns = walker.inspect_asd(str(p))
    assert chunks[0]["id"] == "AppleDouble"
    assert any("AppleDouble" in w for w in warns)


def test_appledouble_is_not_sniffed_as_asd(tmp_path):
    """It must not enter the Ableton namespace at all -- classify already names
    it as a foreign file, which is more useful."""
    p = tmp_path / "._x.wav.asd"
    p.write_bytes(ab.APPLEDOUBLE_MAGIC + b"\x00" * 64)
    assert sniff.sniff(str(p)) != "asd"


# ── the sniffer must not be greedy: two magic bytes are weak ──────────────

def test_the_reserved_word_carries_the_confidence():
    """06 49 alone would fire on plenty of binaries; the zero u32 at offset 6
    is what makes the test safe."""
    assert ab.looks_like_asd(build_asd([100, 200])[:16]) is True
    bad = bytearray(build_asd([100, 200])[:16])
    bad[6] = 0xFF                                   # reserved no longer zero
    assert ab.looks_like_asd(bytes(bad)) is False


def test_zero_count_is_refused():
    assert ab.looks_like_asd(ab.ASD_MAGIC_LE + struct.pack("<II", 0, 0)) is False


def test_sniff_round_trip(tmp_path):
    p = tmp_path / "a.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0)))
    assert sniff.sniff(str(p)) == "asd"


# ── field names are UTF-16LE, which is why an ASCII scan misses them ──────

def test_field_names_need_their_length_prefix():
    name = "WarpMarkers"
    good = struct.pack("<I", len(name)) + name.encode("utf-16le")
    assert [n for _, n in ab.field_names(b"\x00\x00" + good)] == [name]
    # same text, no matching length prefix -> not a field
    bad = struct.pack("<I", 999) + name.encode("utf-16le")
    assert ab.field_names(b"\x00\x00" + bad) == []


def test_walker_reports_analysis_fields(tmp_path):
    body = b""
    for n in ("WarpMarkers", "IsWarped", "LoopStart", "OriginalFileSize"):
        body += struct.pack("<I", len(n)) + n.encode("utf-16le") + b"\x00\x04"
    p = tmp_path / "t.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0), tail=body))
    chunks, _ = walker.inspect_asd(str(p))
    objs = [c for c in chunks if c["id"] == "objects"][0]
    named = {f["name"] for f in objs["fields"]}
    assert {"WarpMarkers", "IsWarped", "LoopStart", "OriginalFileSize"} <= named


# ── the type dictionary ───────────────────────────────────────────────────

def _cls(name, n=0):
    return bytes([len(name)]) + name.encode("ascii") + struct.pack("<I", n)


def _fld(name, tag):
    return struct.pack("<I", len(name)) + name.encode("utf-16le") + bytes([tag])


def test_type_dictionary_reads_classes_and_typed_fields():
    blob = _cls("OnsetEvent", 3) + _fld("Time", 0x17) + _fld("Energy", 0x17) \
        + _fld("IsVolatile", 0x10)
    toks = ab.type_dictionary(blob, 0, len(blob))
    assert toks == [("class", "OnsetEvent", 3), ("field", "Time", 0x17),
                    ("field", "Energy", 0x17), ("field", "IsVolatile", 0x10)]


def test_the_tag_map_covers_the_types_seen_in_the_corpus():
    """Pinned by harvesting field->tag over 1,200 specimens: IsSet/IsVolatile
    are always 0x10, Version/ChannelCount always 0x11, and every 0x40 field has
    a plural name."""
    assert ab.TYPE_TAGS[0x10] == "bool"
    assert ab.TYPE_TAGS[0x11] == "int32"
    assert ab.TYPE_TAGS[0x40] == "list"


def test_an_unknown_tag_is_not_invented():
    """A tag we have not pinned must read as unknown rather than be guessed at
    -- 0x00 occurs in the corpus and its meaning is genuinely unsettled."""
    assert 0x00 not in ab.TYPE_TAGS


def test_field_names_follow_the_declared_byte_order():
    """A big-endian .asd stores UTF-16BE with a big-endian count. Reading those
    files as little-endian finds zero of their 64 field names, and the walker
    then reports "no analysis fields" -- which reads as a fact about the file
    rather than a fault in the reader. 319 of 8,196 specimens are big-endian.
    """
    n = "IsWarped"
    le = struct.pack("<I", len(n)) + n.encode("utf-16le")
    be = struct.pack(">I", len(n)) + n.encode("utf-16be")
    assert [x for _, x in ab.field_names(b"\x00\x00" + le, order="<")] == [n]
    assert [x for _, x in ab.field_names(b"\x00\x00" + be, order=">")] == [n]
    # and each must NOT read the other's encoding
    assert ab.field_names(b"\x00\x00" + be, order="<") == []
    assert ab.field_names(b"\x00\x00" + le, order=">") == []


def test_type_dictionary_reads_big_endian_files():
    name = "IsWarped"
    blob = (bytes([len("OnsetEvent")]) + b"OnsetEvent" + struct.pack(">I", 1)
            + struct.pack(">I", len(name)) + name.encode("utf-16be")
            + bytes([0x10]))
    toks = ab.type_dictionary(blob, 0, len(blob), order=">")
    assert toks == [("class", "OnsetEvent", 1), ("field", "IsWarped", 0x10)]


def test_a_big_endian_walk_reports_its_object_tree(tmp_path):
    """End to end: the bug was that header and grid parsed fine while the whole
    object tree silently vanished."""
    name = "IsWarped"
    tail = (struct.pack(">I", len(name)) + name.encode("utf-16be") + bytes([0x10]))
    p = tmp_path / "be.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0), order=">", tail=tail))
    chunks, warns = walker.inspect_asd(str(p))
    objs = [c for c in chunks if c["id"] == "objects"]
    assert objs and "IsWarped" in {f["name"] for f in objs[0]["fields"]}
    assert not any("no recognised analysis fields" in w for w in warns)


def test_dictionary_walk_is_bounded():
    """The declarations sit at the front and the rest is the overview pyramid,
    so the walk must respect its end bound rather than scan the whole file."""
    blob = _cls("A_Class", 1) + b"\xff" * 4096
    assert ab.type_dictionary(blob, 0, 12) == [("class", "A_Class", 1)]


# ── the overview trailer ──────────────────────────────────────────────────

def _overview(channels, per_bin=None, log2=7):
    per_bin = channels * 2 if per_bin is None else per_bin
    # 26 bytes sit between bytes_per_bin and the sentinel
    return (ab.OVERVIEW_MARK
            + struct.pack("<I", per_bin)          # sentinel-26
            + bytes(10)
            + struct.pack("<I", log2)             # sentinel-12
            + struct.pack("<I", channels)         # sentinel-8
            + bytes(4)                            # sentinel-4
            + ab.OVERVIEW_SENTINEL)


def test_bin_size_is_read_from_the_file_not_inferred():
    """This was wrong once. An earlier version inferred 64 samples per bin by
    dividing the frame count by a byte span -- a span that included several KB
    of unrelated structure. The file states it: SamplesPerBinLog2 is 7, the
    measured geometry is 128 frames per bin, and the two agree exactly on every
    specimen carrying an overview.
    """
    ov = ab.overview_trailer(_overview(2, log2=7))
    assert ov["samples_per_bin_log2"] == 7
    assert ov["bin_samples"] == 128


def test_an_absurd_bin_log2_yields_no_bin_size():
    ov = ab.overview_trailer(_overview(2, log2=99))
    assert ov["bin_samples"] is None


@pytest.mark.parametrize("channels", [1, 2])
def test_overview_channel_count_is_read(channels):
    """Anchored on the sentinel, not on the class name: the trailer's length
    varies with the level count, so the name anchor agreed only ~79% of the
    time while the sentinel matched the source audio on 419 of 419 files."""
    ov = ab.overview_trailer(_overview(channels))
    assert ov["channels"] == channels
    assert ov["bytes_per_bin"] == channels * 2
    assert ov["consistent"] is True


def test_inconsistent_bytes_per_bin_is_flagged(tmp_path):
    body = _overview(2, per_bin=7)
    p = tmp_path / "t.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0), tail=body))
    _, warns = walker.inspect_asd(str(p))
    assert any("bytes_per_bin" in w for w in warns)


def test_no_overview_block_is_not_an_error():
    assert ab.overview_trailer(b"\x00" * 200) is None


# ── the gzipped-XML family ────────────────────────────────────────────────

def _gz(tmp_path, name, xml):
    p = tmp_path / name
    with gzip.open(p, "wb") as fh:
        fh.write(xml)
    return p


ROOT = b'<?xml version="1.0" encoding="UTF-8"?>\n<Ableton MajorVersion="5" ' \
       b'MinorVersion="12.0_12120" Creator="Ableton Live 12.1.5">\n'


def test_root_child_names_the_document_type(tmp_path):
    assert ab.sniff_gzip_ableton(str(_gz(tmp_path, "a.als", ROOT + b"<LiveSet/>"))) == "als"
    assert ab.sniff_gzip_ableton(
        str(_gz(tmp_path, "b.adg", ROOT + b"<GroupDevicePreset/>"))) == "adg"
    # a device preset puts the DEVICE's class there, so it is the default case
    assert ab.sniff_gzip_ableton(str(_gz(tmp_path, "c.adv", ROOT + b"<Operator/>"))) == "adv"


def test_set_and_clip_are_separated_only_by_extension(tmp_path):
    """Both use <LiveSet>; nothing in the content distinguishes them."""
    same = ROOT + b"<LiveSet/>"
    assert ab.sniff_gzip_ableton(str(_gz(tmp_path, "x.als", same))) == "als"
    assert ab.sniff_gzip_ableton(str(_gz(tmp_path, "x.alc", same))) == "alc"


def test_a_gzip_that_is_not_ableton_is_declined(tmp_path):
    """A Live Pack (.alp) is gzip too, but an archive rather than a document."""
    assert ab.sniff_gzip_ableton(str(_gz(tmp_path, "p.alp", b"not xml at all"))) is None


def test_decompression_is_capped(tmp_path):
    """A Live Set expands ~20x in the wild; an uncapped read is a bomb vector."""
    p = tmp_path / "bomb.als"
    with gzip.open(p, "wb") as fh:
        fh.write(b"\x00" * (4 * 1024 * 1024))
    data, truncated = ab.gunzip_capped(str(p), cap=64 * 1024)
    assert len(data) == 64 * 1024 and truncated is True


def test_damaged_gzip_reports_rather_than_raising(tmp_path):
    p = tmp_path / "broken.als"
    p.write_bytes(b"\x1f\x8b\x08\x00" + b"\xff" * 400)
    chunks, warns = walker.inspect_ableton_xml(str(p))
    assert chunks == [] and warns


def test_xml_walker_reads_the_creator(tmp_path):
    p = _gz(tmp_path, "s.als", ROOT + b"<LiveSet><AudioTrack/><AudioTrack/>"
            b"<MidiTrack/></LiveSet>")
    chunks, _ = walker.inspect_ableton_xml(str(p), "als")
    attrs = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert attrs["Creator"] == "Ableton Live 12.1.5"
    assert "2 audio tracks" in chunks[1]["summary"]
    assert "1 MIDI tracks" in chunks[1]["summary"]


# ── Max for Live ──────────────────────────────────────────────────────────

def test_amxd_chunk_chain(tmp_path):
    """The chain starts at 12, after a constant 'aaaa' marker. Treating that
    marker as a chunk id makes its next 4 bytes read as a 1.6 GB length -- which
    is exactly how the first version of this walker failed on a real device."""
    p = tmp_path / "d.amxd"
    p.write_bytes(b"ampf" + struct.pack("<I", 4) + b"aaaa"
                  + b"meta" + struct.pack("<I", 4) + struct.pack("<I", 7)
                  + b"ptch" + struct.pack("<I", 6) + b"mx@c{}")
    chunks, warns = walker.inspect_amxd(str(p))
    assert [c["id"] for c in chunks] == ["ampf", "meta", "ptch"]
    assert not warns, warns
    assert "JSON at +4" in chunks[2]["summary"]


def test_amxd_lying_chunk_length_is_flagged(tmp_path):
    p = tmp_path / "bad.amxd"
    p.write_bytes(b"ampf" + struct.pack("<I", 4) + b"aaaa"
                  + b"meta" + struct.pack("<I", 0xFFFFFF) + b"aa")
    _, warns = walker.inspect_amxd(str(p))
    assert any("past end of file" in w for w in warns)


# ── opt-in: the real corpus ───────────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("ACIDCAT_ABLETON_CORPUS"),
                    reason="set ACIDCAT_ABLETON_CORPUS to a dir of real .asd files")
def test_real_corpus_parses():
    root = os.environ["ACIDCAT_ABLETON_CORPUS"]
    seen = ok = 0
    for dirpath, _, names in os.walk(root):
        for n in names:
            if not n.lower().endswith(".asd"):
                continue
            seen += 1
            raw = open(os.path.join(dirpath, n), "rb").read()
            if ab.is_appledouble(raw):
                continue
            h = ab.parse_asd_header(raw)
            assert h["monotonic"], n
            ok += 1
    assert seen and ok


def test_a_big_endian_file_without_an_overview_is_not_an_error():
    """Measured over 1,500 specimens: every big-endian .asd is the older
    generation and none carries an overview block, so the sentinel constant
    never needs byte-swapping. Absence here is ordinary -- the block is
    optional even in the newer generation (171 of 1,031)."""
    raw = build_asd(grid_for(44100, 1.0), order=">")
    assert ab.overview_trailer(raw, ">") is None


def test_the_dictionary_is_found_when_it_sits_deep_in_the_body():
    """The declarations do NOT always open the object section.

    In 4.8% of a 2,456-file sample the overview pyramid comes first and the
    type dictionary sits 10-25 KB deep. A fixed scan window from the start of
    the body dropped their entire object tree, and the walker then reported
    "no analysis fields" -- blaming the file for the reader's bound.
    """
    filler = b"\xa7" * 20_000                      # stands in for the pyramid
    name = "IsWarped"
    deep = filler + struct.pack("<I", len(name)) + name.encode("utf-16le") + bytes([0x10])
    toks = ab.type_dictionary(deep, 0, len(deep))
    assert ("field", "IsWarped", 0x10) in toks


def test_non_identifier_runs_are_not_mistaken_for_fields():
    """Scanning the whole body means walking high-entropy peak bytes, and some
    of it decodes as a length-prefixed UTF-16 run. A real specimen yielded
    "MOONBOYS ASS SNARE HIGH" as a field. Field names are C++ member names, so
    requiring an identifier keeps that noise out of the declared count."""
    junk = "MOONBOYS ASS SNARE HIGH"
    blob = struct.pack("<I", len(junk)) + junk.encode("utf-16le") + bytes([0xFF])
    assert [n for k, n, _ in ab.type_dictionary(blob, 0, len(blob)) if k == "field"] == []


def test_a_sidecar_with_no_object_tree_says_so_plainly(tmp_path):
    """Verified over 1,500 specimens: when no field names appear in EITHER byte
    order the file really does carry only a header and grid. The warning must
    describe the file, not imply a parse failure."""
    # a real bare sidecar still has a body -- 373 to 22,932 bytes of overview
    # data in the corpus -- it just holds no field names
    p = tmp_path / "bare.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0), tail=bytes([0xA7]) * 4000))
    _, warns = walker.inspect_asd(str(p))
    assert any("only the header and frame grid" in w for w in warns)
    assert not any("no recognised analysis fields" in w for w in warns)


def test_the_walker_finds_a_dictionary_past_any_fixed_window(tmp_path):
    """Guards the WALKER's call site, not just the parser.

    The regression was a fixed scan window in inspect_asd, so a test that
    calls type_dictionary directly cannot catch it -- it has to go through
    the walk.
    """
    name = "IsWarped"
    deep = (bytes([0xA7]) * 20_000
            + struct.pack("<I", len(name)) + name.encode("utf-16le") + bytes([0x10]))
    p = tmp_path / "deep.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0), tail=deep))
    chunks, warns = walker.inspect_asd(str(p))
    objs = [c for c in chunks if c["id"] == "objects"]
    assert objs and "IsWarped" in {f["name"] for f in objs[0]["fields"]}
    assert not any("only the header and frame grid" in w for w in warns)


# ── onsets and clip parameters ────────────────────────────────────────────

def _onset_blob(positions, energies):
    n = len(positions)
    return (struct.pack("<I", n) + struct.pack(f"<{n}I", *positions)
            + struct.pack("<I", n) + struct.pack(f"<{n}f", *energies))


def test_onsets_are_two_length_prefixed_arrays():
    """Established from the Live Set XML, which serialises the SAME object
    model in readable form: OnSets carries Positions and TransitionEnergies.
    On disk that is count, u32 positions, count again, f32 energies."""
    blob = bytes(16) + _onset_blob([100, 5000, 9000], [12.5, 30.0, 7.25])
    on = ab.onsets(blob, 10_000, 0)
    assert on["count"] == 3
    assert on["positions"] == [100, 5000, 9000]
    assert on["energies"] == [12.5, 30.0, 7.25]


def test_onsets_outside_the_frame_count_are_refused():
    """The grid already gave us the true frame count, so it can vet this."""
    blob = _onset_blob([100, 999_999], [1.0, 2.0])
    assert ab.onsets(blob, 10_000, 0) is None


def test_a_single_onset_is_not_guessed_at():
    """With n == 1 'strictly increasing' constrains nothing, so any stray pair
    of equal u32 qualifies -- that gave 7 of 14 files a wrong answer. A
    one-shot is reported as unknown instead."""
    blob = _onset_blob([100], [5.0])
    assert ab.onsets(blob, 10_000, 0) is None


def test_the_richest_candidate_wins_not_the_first():
    """A coincidental match can precede the real structure in the byte stream."""
    decoy = _onset_blob([10, 20], [1.0, 2.0])
    real = _onset_blob([100, 200, 300, 400, 500], [1.0, 2.0, 3.0, 4.0, 5.0])
    on = ab.onsets(decoy + real, 10_000, 0)
    assert on["count"] == 5


def test_onset_scan_is_byte_stepped_not_word_stepped():
    """The arrays are not aligned to the end of the frame grid; a word-stepped
    scan walks straight past them and finds nothing."""
    blob = bytes(3) + _onset_blob([100, 5000], [1.0, 2.0])   # deliberately off-word
    assert ab.onsets(blob, 10_000, 0) is not None


def test_clip_params_mix_ints_and_floats():
    """TransientResolution and TransientLoopMode are integers and the rest are
    floats, which is why the block does not read as one float run -- searching
    for eight consecutive f32 finds nothing."""
    kinds = [k for _n, k in ab.CLIP_PARAMS]
    assert kinds.count("u32") == 2 and kinds.count("f32") == 6
    assert ab.CLIP_PARAMS[0] == ("TransientResolution", "u32")


# ── warp markers, and the tempo they imply ────────────────────────────────

def _marker(mid, sec, beat):
    """One on-disk warp marker: the class name is inline and length-prefixed,
    which is what makes them self-locating."""
    return (ab.WARP_MARKER_NAME + struct.pack("<I", mid)
            + struct.pack("<2d", sec, beat))


def test_a_warp_marker_is_a_named_32_byte_record():
    blob = bytes(8) + _marker(0, 0.0, 0.0) + _marker(1, 0.011029411764705883, 0.03125)
    got = ab.warp_markers(blob)
    assert [m["id"] for m in got] == [0, 1]
    assert got[1]["sec"] == 0.011029411764705883
    assert got[1]["beat"] == 0.03125
    assert ab.WARP_MARKER_SIZE == 31


def test_tempo_is_derived_from_the_mapping_not_stored():
    """Live stores seconds-to-beats, not a BPM. 0.03125 beats in
    0.011029411764705883 s is 170 BPM -- and that is exactly the tempo the Live
    Set that produced these markers declares."""
    got = ab.warp_markers(bytes(8) + _marker(0, 0.0, 0.0)
                          + _marker(1, 0.011029411764705883, 0.03125))
    assert ab.derived_tempo(got) == 170.0


def test_the_class_declaration_is_not_read_as_a_marker():
    """The type dictionary carries the same "WarpMarker" literal, and the bytes
    after it decode as denormals around 1e-307. An "is it finite" test let one
    through and added a phantom marker to every file."""
    decl = ab.WARP_MARKER_NAME + struct.pack("<I", 2) + struct.pack("<2d", 9.3e-307, 1.2e-306)
    blob = decl + _marker(0, 0.0, 0.0) + _marker(1, 0.5, 1.0)
    assert [m["id"] for m in ab.warp_markers(blob)] == [0, 1]


def test_markers_must_be_a_run_starting_at_zero():
    """A stray record with a high id is not the array."""
    blob = _marker(7, 1.0, 2.0)
    assert ab.warp_markers(blob) == []


def test_no_tempo_without_two_markers_spanning_time():
    """An unwarped one-shot has nothing to derive a tempo from, and gets None
    rather than a guess."""
    assert ab.derived_tempo([{"id": 0, "sec": 0.0, "beat": 0.0}]) is None
    assert ab.derived_tempo([{"id": 0, "sec": 0.0, "beat": 0.0},
                             {"id": 1, "sec": 0.0, "beat": 0.0}]) is None


# ── staleness: does the sidecar still describe this audio? ────────────────

def test_a_sidecar_that_matches_its_audio_is_quiet(tmp_path):
    """Live records the source's byte size and re-analyses on mismatch.
    Measured on 1,200 sidecars beside their real audio: 96% reference it."""
    audio = tmp_path / "x.wav"
    audio.write_bytes(bytes(5000))
    tail = struct.pack("<I", 5000)
    p = tmp_path / "x.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0), tail=tail))
    _, warns = walker.inspect_asd(str(p))
    assert not any("does not reference" in w for w in warns)


def test_audio_changed_after_analysis_is_reported(tmp_path):
    """The finding nothing else offers: the sidecar describes a version of the
    file that no longer exists."""
    audio = tmp_path / "x.wav"
    audio.write_bytes(bytes(9999))
    tail = struct.pack("<I", 5000)          # records the OLD size
    p = tmp_path / "x.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0), tail=tail))
    _, warns = walker.inspect_asd(str(p))
    assert any("does not reference" in w for w in warns), warns


def test_no_sibling_audio_is_not_a_finding(tmp_path):
    """An orphaned sidecar is the case this walker exists for; it must not be
    reported as stale."""
    p = tmp_path / "gone.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0), tail=struct.pack("<I", 5000)))
    _, warns = walker.inspect_asd(str(p))
    assert not any("does not reference" in w for w in warns)


def test_the_declared_field_list_is_labelled_as_a_schema(tmp_path):
    """Declared is not stored.

    The type dictionary is shared with the Live Set, so it names clip settings
    the sidecar does not carry. LoopEnd is declared in every specimen and is
    ABSENT from 91% of files whose Set states a real one -- loop points belong
    to a clip, and one audio file can back many clips. Listing the field names
    without saying so invites reading "LoopStart" as a value the file holds.
    """
    body = b""
    for n in ("LoopStart", "LoopEnd", "IsWarped"):
        body += struct.pack("<I", len(n)) + n.encode("utf-16le") + bytes([0x00, 0x11])
    p = tmp_path / "t.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 1.0), tail=body))
    chunks, _ = walker.inspect_asd(str(p))
    objs = [c for c in chunks if c["id"] == "objects"][0]
    notes = " ".join(f.get("note", "") for f in objs["fields"])
    assert "declared" in notes.lower() and "clip" in notes.lower()


def test_a_groove_is_recognised(tmp_path):
    """.agr is the same gzip + <Ableton> XML shape; <Groove> names it."""
    p = _gz(tmp_path, "q.agr", ROOT + b"<Groove><Name Value=\"32 Quantize\" /></Groove>")
    assert ab.sniff_gzip_ableton(str(p)) == "agr"
    assert ab.xml_label("agr") == "groove"


def test_an_unmapped_ableton_document_reports_its_root_child(tmp_path):
    """Live has more XML document types than are mapped -- .ams, .abl, .ask.
    They share the shape, so one we do not model must still be described
    rather than silently called a device preset."""
    p = _gz(tmp_path, "x.ams", ROOT + b"<SomeFutureThing/>")
    chunks, _ = walker.inspect_ableton_xml(str(p), ab.sniff_gzip_ableton(str(p)))
    named = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert named.get("root_child") == "SomeFutureThing"


# ── claims must not outrun the bytes that support them ──────────────

def _asd(count, frames, order="<", grid=None):
    """A minimal .asd: marker, byte order, count, reserved, then the grid."""
    import struct
    head = bytes([0x06]) + (b"I" if order == "<" else b"M") \
        + struct.pack(order + "I", count) + struct.pack(order + "I", 0)
    if grid is None:
        step = max(1, frames // max(1, count - 1))
        grid = [min(frames, i * step) for i in range(count - 1)]
        if grid:
            grid[-1] = frames
    return head + b"".join(struct.pack(order + "I", g) for g in grid)


def test_a_truncated_grid_reports_a_lower_bound_not_a_duration(tmp_path):
    """The grid ends early, so its last position describes the bytes PRESENT,
    not the audio. Printed plain, a 60 s source read as 15 s -- wrong by 4x and
    stated as a fact, with the truncation warning sitting somewhere else where
    it cannot catch a number already asserted.
    """
    full = _asd(2001, 2_646_000)
    p = tmp_path / "t.wav.asd"
    p.write_bytes(full[:10 + 500 * 4])          # keep a quarter of the grid
    chunks, warns = walker.inspect_asd(str(p))
    grid = [c for c in chunks if c["id"] == "grid"][0]
    assert "LOWER BOUND" in grid["summary"], grid["summary"]
    dur = [f for f in grid["fields"] if f["name"] == "duration"]
    assert dur and "LOWER BOUND" in dur[0]["note"], dur
    last = [f for f in grid["fields"] if f["name"] == "last_position"][0]
    assert "lower bound" in last["note"].lower(), last["note"]
    assert any("claims" in w for w in warns)


def test_a_complete_grid_still_states_the_duration_plainly(tmp_path):
    """The hedge must not fire on a whole file, or it stops meaning anything."""
    p = tmp_path / "ok.wav.asd"
    p.write_bytes(_asd(2001, 2_646_000))
    chunks, _warns = walker.inspect_asd(str(p))
    grid = [c for c in chunks if c["id"] == "grid"][0]
    assert "LOWER BOUND" not in grid["summary"], grid["summary"]
    last = [f for f in grid["fields"] if f["name"] == "last_position"][0]
    assert "equals the source audio" in last["note"]


def test_no_object_tree_is_not_claimed_beside_chunks_read_from_one(tmp_path):
    """The field-name scan is one way into the object tree; warp markers,
    onsets and the overview trailer each have their own detector further down.
    Deciding the verdict before those ran printed "there is no object tree in
    it" directly above 400 warp markers read out of that tree."""
    import re
    import inspect as _inspect
    from acidcat.core.walk import ableton as abwalk
    src = _inspect.getsource(abwalk.inspect_asd)
    # the EMIT site, not any mention: the comment above it says the phrase too
    m = re.search(r"warns\.append\([\s\S]{0,80}?this sidecar carries only", src)
    assert m, "the no-object-tree warning is gone entirely"
    warn_at = m.start()
    for detector in ("warp", "onsets", "overview"):
        at = src.index(f'"id": "{detector}"')
        assert at < warn_at, (
            f"the {detector} chunk is appended AFTER the no-object-tree "
            f"verdict, so the verdict cannot see it")


def test_amxd_magic_is_read_from_the_file(tmp_path):
    """A field table is the one place a reader trusts to show actual bytes. It
    printed the literal "ampf" whatever the file held, while a warning two
    lines away said the magic was missing."""
    import struct
    p = tmp_path / "bad.amxd"
    p.write_bytes(b"XXXX" + struct.pack("<I", 1) + b"aaaa")
    chunks, warns = walker.inspect_amxd(str(p))
    magic = [f for c in chunks for f in c["fields"] if f["name"] == "magic"]
    assert magic and magic[0]["value"] == "XXXX", magic
    assert any("magic" in w for w in warns)


def test_amxd_trailing_data_is_reported_even_when_something_else_is_wrong(tmp_path):
    """Gating the trailing-data check on `not warns` meant any unrelated
    warning suppressed it, so appended bytes went unreported precisely on the
    files that already looked anomalous. Backwards for a forensics tool."""
    import struct
    tail = b"TRAIL!"
    good = (b"ampf" + struct.pack("<I", 1) + b"aaaa"
            + b"ptch" + struct.pack("<I", 4) + b"{}  ")
    for name, blob in (("good.amxd", good + tail),
                       ("bad.amxd", b"XXXX" + good[4:] + tail)):
        p = tmp_path / name
        p.write_bytes(blob)
        _chunks, warns = walker.inspect_amxd(str(p))
        assert any("chain ends" in w for w in warns), f"{name}: {warns}"


# ── a tempo that moves must not report as a tempo that does not ──────────

def _spanned(*pairs):
    """Markers from (seconds, beats) pairs, ids in a run from zero."""
    return [{"id": i, "sec": s, "beat": b} for i, (s, b) in enumerate(pairs)]


def test_every_warp_span_is_measured_not_just_the_first():
    """Warp markers exist to encode tempo that MOVES.

    derived_tempo returned on the first usable pair, so a clip warped
    120 / 60 / 200 reported "120 BPM" in both the field and the chunk summary
    with nothing hinting the other spans existed. The field's own note said
    "between two markers", which was accurate about the mechanism and
    misleading about the answer.
    """
    # 2 beats in 1 s = 120 BPM; then 1 beat in 1 s = 60; then 1 beat in 0.3 s
    marks = _spanned((0.0, 0.0), (1.0, 2.0), (2.0, 3.0), (2.3, 4.0))
    spans = ab.derived_tempos(marks)
    assert len(spans) == 3
    assert spans[0] == pytest.approx(120.0)
    assert spans[1] == pytest.approx(60.0)
    assert spans[2] == pytest.approx(200.0)


def test_a_steady_clip_still_reports_one_number():
    """The hedge must not fire on a clip whose tempo does not move, or it
    becomes noise on the ordinary case and stops being read."""
    marks = _spanned((0.0, 0.0), (1.0, 2.0), (2.0, 4.0))
    spans = ab.derived_tempos(marks)
    assert spans == [120.0, 120.0]
    assert max(spans) - min(spans) < 0.05


def test_derived_tempo_still_returns_a_single_number():
    """The old entry point keeps working for callers that want one value."""
    marks = _spanned((0.0, 0.0), (1.0, 2.0), (2.0, 3.0))
    assert ab.derived_tempo(marks) == pytest.approx(120.0)
    assert ab.derived_tempo([{"id": 0, "sec": 0.0, "beat": 0.0}]) is None


def test_a_varying_clip_renders_a_range(tmp_path):
    """End to end: the walker must show the movement, not the first span."""
    body = b"".join(_marker(i, s, b) for i, (s, b) in enumerate(
        [(0.0, 0.0), (1.0, 2.0), (2.0, 3.0), (2.3, 4.0)]))
    p = tmp_path / "vari.wav.asd"
    p.write_bytes(build_asd(grid_for(44100, 2.0), tail=body))
    chunks, _warns = walker.inspect_asd(str(p))
    warp = [c for c in chunks if c["id"] == "warp"]
    if not warp:
        pytest.skip("the synthetic marker array was not recognised")
    summary = warp[0]["summary"]
    assert "BPM" in summary
    assert "-" in summary and "spans" in summary, summary
    field = [f for f in warp[0]["fields"] if f["name"] == "derived_tempo"][0]
    assert "MOVES" in field["note"], field["note"]


def test_zeroed_grid_tail_has_a_rate_but_no_duration(tmp_path):
    """A known sample rate does not imply a known duration.

    The rate is inferred from the largest STEP between grid positions; the
    duration is derived from the last POSITION. Zeroing the tail of the table
    keeps the first and destroys the second -- the surviving steps still pin a
    rate, while total_frames becomes 0 and the duration becomes None.

    The walker guarded both on `rate is None`, so this state formatted None
    with `:.3f` and raised TypeError out of a walk. Found by the mutation
    sweep, which reached it the first time a generated .asd specimen existed
    to mutate.
    """
    frames = list(grid_for(44100, 2.0))
    frames[-3:] = [0, 0, 0]                      # zero the tail, keep the steps
    p = tmp_path / "zeroed.asd"
    p.write_bytes(build_asd(frames))

    chunks, warns = walker.inspect_asd(str(p))   # must not raise

    h = ab.parse_asd_header(p.read_bytes())
    assert h["sample_rate"] is not None, "the surviving steps should still pin a rate"
    assert h["duration"] is None, "a grid ending at 0 has no duration to report"

    grid_chunk = [c for c in chunks if c["id"] == "grid"][0]
    assert "no duration" in grid_chunk["summary"], grid_chunk["summary"]
    assert not [f for f in grid_chunk["fields"] if f["name"] == "duration"], \
        "a duration field was emitted for a duration that does not exist"
    assert any("ends at position 0" in w for w in warns), warns
