"""tests for acidcat.core.formats.riff and the WAV walker behaviors that
replaced the legacy parse_riff/get_duration parsers."""

import struct
import time

import pytest
from acidcat.core.formats.riff import iter_chunks, get_riff_info, effective_acid_beats
from acidcat.core.walk.wav import inspect_wav


def _fmt_chunk():
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 44100 * 2, 2, 16)
    return b"fmt " + struct.pack("<I", 16) + fmt


def _data_chunk(n=4):
    return b"data" + struct.pack("<I", n) + b"\x00" * n


def _wav_bytes(*chunks):
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _chunk_by_id(chunks, cid):
    return next(c for c in chunks if c["id"] == cid)


class TestGetRiffInfo:
    def test_valid_wav(self, minimal_wav):
        info = get_riff_info(minimal_wav)
        assert info is not None
        assert info["type"] == "WAVE"
        assert info["size"] > 0

    def test_not_riff(self, not_riff):
        assert get_riff_info(not_riff) is None

    def test_empty_file(self, empty_file):
        assert get_riff_info(empty_file) is None

    def test_truncated(self, truncated_riff):
        # starts with RIFF but is too short -- still returns info (header is valid)
        info = get_riff_info(truncated_riff)
        assert info is not None
        assert info["type"] == "WAVE"


class TestIterChunks:
    def test_valid_wav_yields_fmt_and_data(self, minimal_wav):
        chunks = list(iter_chunks(minimal_wav))
        ids = [c[0] for c in chunks]
        assert "fmt " in ids
        assert "data" in ids

    def test_chunk_offsets_are_positive(self, minimal_wav):
        for cid, offset, size in iter_chunks(minimal_wav):
            assert offset >= 12  # first chunk starts after 12-byte RIFF header

    def test_not_riff_yields_nothing(self, not_riff):
        assert list(iter_chunks(not_riff)) == []

    def test_empty_file_yields_nothing(self, empty_file):
        assert list(iter_chunks(empty_file)) == []

    def test_truncated_yields_partial(self, truncated_riff):
        # may yield nothing or partial -- should not raise
        result = list(iter_chunks(truncated_riff))
        assert isinstance(result, list)


class TestWalkerDuration:
    """the walker's ctx duration replaced core/riff.get_duration."""

    def test_silent_wav(self, silent_wav):
        ctx = {}
        inspect_wav(silent_wav, ctx=ctx)
        assert abs(ctx["duration"] - 0.1) < 0.01  # 4410 samples at 44100 Hz

    def test_minimal_wav(self, minimal_wav):
        ctx = {}
        inspect_wav(minimal_wav, ctx=ctx)
        assert ctx["duration"] >= 0


class TestWavWalker:
    """safety and acid-chunk behaviors carried over from the retired
    parse_riff test suite, asserted against the walker."""

    def test_huge_num_cues_does_not_hang(self, tmp_path):
        """B-7 lineage: a cue chunk advertising 0xFFFFFFFF cue points with a
        4-byte payload must not iterate billions of times. The walker caps
        the count against the payload's actual record capacity and warns.
        """
        cue_payload = struct.pack("<I", 0xFFFFFFFF)
        cue_chunk = b"cue " + struct.pack("<I", len(cue_payload)) + cue_payload
        wav_path = tmp_path / "huge_cue.wav"
        wav_path.write_bytes(_wav_bytes(_fmt_chunk(), _data_chunk(), cue_chunk))

        t0 = time.perf_counter()
        chunks, _ = inspect_wav(str(wav_path))
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, f"walker took {elapsed:.2f}s on a 4-billion-cue claim"

        cue = _chunk_by_id(chunks, "cue ")
        # zero record capacity: no cue[i] fields may be synthesized
        assert not any(f["name"].startswith("cue[") for f in cue["fields"])
        assert any("declares 4294967295 cue points" in w for w in cue["warnings"])

    def test_acid_chunk_spec_layout(self, tmp_path):
        """the acid chunk layout is, per libsndfile and field-verified
        against real ACIDized packs: flags u32, root u16, q1 u16, q2 f32,
        num_beats u32, meter denom u16, meter numer u16, tempo f32. an old
        parser unpacked '<IHHIII f' and reported acid_beats=0 on every
        spec-conformant file; real loops must surface their beat count.
        """
        acid_payload = struct.pack("<IHHfIHHf", 0x05, 60, 0x8000, 0.0, 15, 4, 4, 122.0)
        acid_chunk = b"acid" + struct.pack("<I", 24) + acid_payload
        wav = tmp_path / "acid_loop.wav"
        wav.write_bytes(_wav_bytes(_fmt_chunk(), _data_chunk(), acid_chunk))

        ctx = {}
        chunks, _ = inspect_wav(str(wav), ctx=ctx)
        assert ctx["acid_beats"] == 15
        assert ctx["acid_root"] == 60
        assert ctx["acid_bpm"] == 122.0
        acid = _chunk_by_id(chunks, "acid")
        fields = {f["name"]: f["value"] for f in acid["fields"]}
        assert fields["num_beats"] == 15
        assert fields["meter_numerator"] == 4
        assert fields["meter_denominator"] == 4
        assert fields["tempo"] == 122.0

    def test_acid_chunk_padded_past_24_bytes(self, tmp_path):
        """some taggers pad the acid chunk past its 24-byte layout; an
        exact-length struct.unpack raised on the extra bytes and BPM,
        beats, and root were silently lost. trailing bytes are ignored.
        """
        acid_payload = struct.pack(
            "<IHHfIHHf", 0x05, 60, 0x8000, 0.0, 8, 4, 4, 120.0,
        ) + b"\x00" * 4  # 28 bytes: 24-byte layout + 4 pad bytes
        acid_chunk = b"acid" + struct.pack("<I", len(acid_payload)) + acid_payload
        wav = tmp_path / "padded_acid.wav"
        wav.write_bytes(_wav_bytes(_fmt_chunk(), _data_chunk(), acid_chunk))

        ctx = {}
        inspect_wav(str(wav), ctx=ctx)
        assert ctx["acid_bpm"] == 120.0
        assert ctx["acid_beats"] == 8
        assert ctx["acid_root"] == 60

    def test_acid_chunk_short_degrades_not_garbage(self, tmp_path):
        """an acid chunk under 24 bytes cannot hold the layout; the walker
        must degrade with a truncation warning, not emit partial values."""
        acid_chunk = b"acid" + struct.pack("<I", 12) + b"\x00" * 12
        wav = tmp_path / "short_acid.wav"
        wav.write_bytes(_wav_bytes(_fmt_chunk(), acid_chunk))

        ctx = {}
        chunks, _ = inspect_wav(str(wav), ctx=ctx)
        acid = _chunk_by_id(chunks, "acid")
        assert acid["fields"] == []
        assert acid["summary"] == "truncated"
        assert any("acid payload is 12 bytes" in w for w in acid["warnings"])
        assert "acid_bpm" not in ctx

    def test_acid_one_shot_flag_surfaces(self, tmp_path):
        """the walker reports facts: raw beats plus the one-shot flag.
        vetting is the consumer's job via effective_acid_beats.
        """
        # flags 0x03 = one-shot + root set, boilerplate beats/tempo
        acid_payload = struct.pack("<IHHfIHHf", 0x03, 47, 0x8000, 0.0, 8, 4, 4, 120.0)
        acid_chunk = b"acid" + struct.pack("<I", 24) + acid_payload
        wav = tmp_path / "oneshot.wav"
        wav.write_bytes(_wav_bytes(_fmt_chunk(), _data_chunk(), acid_chunk))

        ctx = {}
        inspect_wav(str(wav), ctx=ctx)
        assert ctx["acid_beats"] == 8
        assert ctx["acid_one_shot"] is True
        assert ctx["acid_root"] == 47

    def test_drum_loop_if_present(self):
        import os
        from conftest import SAMPLE_WAV
        if not os.path.isfile(SAMPLE_WAV):
            pytest.skip("Drum_Loop.wav not present")
        chunks, _ = inspect_wav(SAMPLE_WAV)
        ids = [c["id"] for c in chunks]
        assert "fmt " in ids
        assert "data" in ids


class TestEffectiveAcidBeats:
    """vetting policy, calibrated on 400 real ACIDized files
    (2026-06-11): flag clear means beats are ~93% trustworthy; flag
    set is a coin flip between accurate loops and batch-tagger
    boilerplate, so the duration cross-check decides.
    """

    def test_flag_clear_trusts_beats(self):
        meta = {"acid_beats": 16, "acid_one_shot": False, "bpm": 120.0}
        assert effective_acid_beats(meta, 0.5) == 16

    def test_one_shot_boilerplate_is_dropped(self):
        # 0.12s hat claiming 8 beats at 120 bpm (4s): bogus
        meta = {"acid_beats": 8, "acid_one_shot": True, "bpm": 120.0}
        assert effective_acid_beats(meta, 0.12) is None

    def test_one_shot_with_reconciling_beats_is_kept(self):
        # 8.0s file claiming 16 beats at 120 bpm (8s): vendor set the
        # one-shot bit on a real loop, the beat count is accurate
        meta = {"acid_beats": 16, "acid_one_shot": True, "bpm": 120.0}
        assert effective_acid_beats(meta, 8.0) == 16

    def test_one_shot_without_duration_is_dropped(self):
        meta = {"acid_beats": 8, "acid_one_shot": True, "bpm": 120.0}
        assert effective_acid_beats(meta, None) is None

    def test_no_beats_is_none(self):
        meta = {"acid_beats": 0, "acid_one_shot": False, "bpm": 120.0}
        assert effective_acid_beats(meta, 4.0) is None


class TestStreamingSizeSentinels:
    """A WAV written to a pipe is not a damaged WAV.

    A writer streaming to stdout cannot know its length in advance, so it puts a
    placeholder in the data chunk's size field and the reader reads to end of
    file. Three are in circulation, and all three used to be reported in exactly
    the words a genuinely truncated file gets -- so the tool could not tell a
    streamed file from a broken one, and said the same thing about both.

    The zero case was worse than noisy. Taken literally it reported a file
    holding real audio as EMPTY, with no warning at all, which is a silent wrong
    answer rather than a loud one.
    """

    def _walk(self, tmp_path, declared, payload=b"\x00" * 800):
        blob = _wav_bytes(_fmt_chunk(),
                          b"data" + struct.pack("<I", declared) + payload)
        p = tmp_path / "s.wav"
        p.write_bytes(blob)
        chunks, file_warns = inspect_wav(str(p))
        return _chunk_by_id(chunks, "data"), file_warns

    @pytest.mark.parametrize("declared,name", [
        (0, "zero"),
        (0xFFFFFFFF, "the 32-bit maximum"),
        (0x7FFFF000, "the largest sample-aligned 31-bit value"),
    ])
    def test_a_sentinel_reports_the_bytes_that_are_there(self, tmp_path,
                                                         declared, name):
        data, file_warns = self._walk(tmp_path, declared)
        assert "streaming placeholder" in data["summary"]
        assert "800 bytes" in data["summary"], data["summary"]
        assert not file_warns, "a streamed file is not a size mismatch"

    def test_a_genuinely_wrong_size_is_still_reported(self, tmp_path):
        """The control. If every odd size were excused, the check would be
        excusing corruption along with convention."""
        data, file_warns = self._walk(tmp_path, 999_999)
        assert "declared" in data["summary"] and "only" in data["summary"]
        assert any("but only" in w for w in file_warns), file_warns
        assert "streaming placeholder" not in data["summary"]

    def test_a_zero_size_with_no_payload_is_still_empty(self, tmp_path):
        """The control that matters most, because it is the one that keeps the
        zero sentinel honest. Zero means "placeholder" only when bytes follow;
        with nothing after it, the chunk really is empty and must say so."""
        data, _fw = self._walk(tmp_path, 0, payload=b"")
        assert "streaming placeholder" not in data["summary"]
        assert any("empty" in w for w in data["warnings"]), data["warnings"]

    def test_the_walker_does_not_claim_both_at_once(self, tmp_path):
        """It briefly reported a streamed file as carrying a payload to end of
        file AND as being an empty data chunk, which cannot both be true."""
        data, _fw = self._walk(tmp_path, 0)
        joined = " ".join(data["warnings"])
        assert not ("placeholder" in joined and "empty" in joined), joined

    def test_duration_comes_from_the_bytes_present(self, tmp_path):
        """A sentinel says nothing about length, so the frame count has to be
        derived from what is actually there rather than from the field."""
        # the fmt above is mono 16-bit at 44100, so block_align is 2 and one
        # second is 88,200 bytes rather than the 176,400 a stereo file would use
        data, _fw = self._walk(tmp_path, 0xFFFFFFFF, payload=b"\x00" * 88_200)
        assert "1.000 s" in data["summary"], data["summary"]


# ── clm, strc and id3: three chunks that were walked and not read ───
#
# Found by walking 266 real files from a working sample library and counting
# what came back as "unparsed": clm in 43, strc in the loop material, an id3
# tag inside RIFF in 8. The last is the sharpest -- acidcat has parsed ID3 at
# the front of an MP3 since before 1.0 and was not applying it here, so the
# same tag was read in one container and ignored in another.

def _chunk(cid, payload):
    return (cid + struct.pack("<I", len(payload)) + payload
            + (b"\x00" if len(payload) % 2 else b""))


def _clm(text):
    return _chunk(b"clm ", text.encode("latin-1"))


def _strc(slices, stride=32, header=28, count=None):
    """An ACID slice table. The stride is what a real file varies, so it is a
    parameter here: sixteen of seventeen measured files use 32-byte records and
    one older writer does not."""
    n = len(slices) if count is None else count
    body = b""
    for pos in slices:
        rec = bytearray(stride)
        struct.pack_into("<I", rec, 8, pos)      # the ascending position column
        body += bytes(rec)
    head = struct.pack("<7I", header, n, 65, 5, 1, 0, 0)
    return _chunk(b"strc", head + body)


def _id3_chunk(frames=(("TIT2", "Test Title"), ("TPE1", "Test Artist"))):
    """An ID3v2.3 tag as a RIFF chunk, which is where a DAW puts one."""
    body = b""
    for fid, text in frames:
        payload = b"\x00" + text.encode("latin-1")
        body += fid.encode("ascii") + struct.pack(">I", len(payload)) + b"\x00\x00" + payload
    size = len(body)
    synch = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F,
                   (size >> 7) & 0x7F, size & 0x7F])
    return _chunk(b"id3 ", b"ID3" + bytes([3, 0, 0]) + synch + body)


def _wav_with(extra, frames=4096, ch=1, bits=16, rate=44100):
    align = ch * bits // 8
    fmt = struct.pack("<HHIIHH", 1, ch, rate, rate * align, align, bits)
    body = (b"WAVE" + _chunk(b"fmt ", fmt) + extra
            + _chunk(b"data", b"\x00" * (frames * align)))
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _walk_bytes(tmp_path, data, name="t.wav"):
    from acidcat.core.walk import walk_file
    p = tmp_path / name
    p.write_bytes(data)
    return walk_file(str(p))


def _chunk_named(chunks, cid):
    return next(c for c in chunks if c["id"] == cid)


def test_clm_reports_the_wavetable_frame_size(tmp_path):
    """A Serum wavetable is a plain WAV whose samples are N frames of a fixed
    length end to end. Nothing in fmt or data says so: without clm the file
    looks like one long waveform."""
    data = _wav_with(_clm("<!>2048 00000000 wavetable (www.xferrecords.com)"),
                     frames=2048 * 11)
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    clm = _chunk_named(chunks, "clm ")
    f = {x["name"]: x["value"] for x in clm["fields"]}
    assert f["frame_size"] == "2,048"
    assert f["writer"] == "Xfer Records (Serum)"


def test_clm_derives_the_frame_count_from_the_data_length(tmp_path):
    """The count is the number a reader wants and neither chunk holds it: it is
    the data length divided by the frame size. clm is written BEFORE data, so
    it cannot be known when clm is parsed."""
    data = _wav_with(_clm("<!>2048 00000000 wavetable"), frames=2048 * 11)
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    clm = _chunk_named(chunks, "clm ")
    assert "11 frames of 2,048 samples" in clm["summary"], clm["summary"]


def test_a_data_length_that_is_not_whole_frames_is_flagged(tmp_path):
    """A wavetable whose data does not divide by its frame size is either
    truncated or not the wavetable it says it is. Reporting a rounded-down
    frame count and nothing else would hide that."""
    data = _wav_with(_clm("<!>2048 00000000 wavetable"), frames=2048 * 3 + 100)
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    clm = _chunk_named(chunks, "clm ")
    assert any("not a whole number" in w for w in clm["warnings"]), clm["warnings"]


def test_strc_reads_the_slice_positions(tmp_path):
    data = _wav_with(_strc([0, 21000, 42000, 63000]), frames=84000)
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    strc = _chunk_named(chunks, "strc")
    f = {x["name"]: x["value"] for x in strc["fields"]}
    assert f["slices"] == 4
    assert f["slice[1]"] == "21,000"


def test_strc_derives_the_tempo_from_an_even_grid(tmp_path):
    """Verified against a real 126 BPM loop before this was written: sixteen
    markers 21,000 samples apart at 44.1 kHz is 0.4762 s, which is 126.00 BPM,
    and the markers span the whole data chunk exactly."""
    data = _wav_with(_strc([i * 21000 for i in range(16)]), frames=16 * 21000)
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    strc = _chunk_named(chunks, "strc")
    assert "126.00 BPM" in strc["summary"], strc["summary"]


def test_an_uneven_grid_claims_no_tempo(tmp_path):
    """Unevenly spaced markers are a transient map, not a beat grid. A tempo
    derived from the first gap would be a number that means nothing, and it
    would look exactly as authoritative as the real one."""
    data = _wav_with(_strc([0, 21000, 30000, 77000]), frames=84000)
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    strc = _chunk_named(chunks, "strc")
    assert "BPM" not in strc["summary"], strc["summary"]
    assert not any(x["name"] == "implied_tempo" for x in strc["fields"])


def test_the_strc_record_size_is_derived_not_assumed(tmp_path):
    """Sixteen of seventeen real files use 32-byte records and one older writer
    does not. A hardcoded stride reports that file's markers as garbage."""
    for stride in (24, 32, 40):
        data = _wav_with(_strc([0, 1000, 2000], stride=stride), frames=8000)
        _l, chunks, _w = _walk_bytes(tmp_path, data, "s%d.wav" % stride)
        strc = _chunk_named(chunks, "strc")
        f = {x["name"]: x["value"] for x in strc["fields"]}
        assert f["record_size"] == stride, (stride, f)
        assert f["slice[2]"] == "2,000"


def test_a_body_that_does_not_divide_says_so_rather_than_guessing(tmp_path):
    """A count that does not divide the body evenly means the layout is not
    what this reader thinks. Reading positions anyway would emit plausible
    numbers from the wrong offsets."""
    data = _wav_with(_strc([0, 1000], stride=32, count=3), frames=8000)
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    strc = _chunk_named(chunks, "strc")
    assert "unreadable" in strc["summary"], strc["summary"]
    assert not any(x["name"].startswith("slice[") for x in strc["fields"])


def test_an_id3_tag_inside_riff_is_read(tmp_path):
    """acidcat has parsed ID3 at the front of an MP3 since before 1.0. The same
    tag inside a RIFF chunk was reported as unparsed bytes: one format, two
    containers, one of them read."""
    data = _wav_with(_id3_chunk())
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    id3 = _chunk_named(chunks, "id3 ")
    f = {x["name"]: x["value"] for x in id3["fields"]}
    assert f["version"] == "2.3.0"
    assert f["TIT2"] == "Test Title"
    assert f["TPE1"] == "Test Artist"


def test_an_id3_chunk_that_is_not_a_tag_says_so(tmp_path):
    data = _wav_with(_chunk(b"id3 ", b"not a tag at all"))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    id3 = _chunk_named(chunks, "id3 ")
    assert "not an ID3v2 tag" in id3["summary"]


def test_a_tag_longer_than_its_chunk_is_flagged(tmp_path):
    """The synchsafe size is inside the tag and the chunk size is outside it.
    Nothing makes them agree."""
    tag = b"ID3" + bytes([3, 0, 0]) + bytes([0, 0, 0x7F, 0x7F]) + b"TIT2"
    data = _wav_with(_chunk(b"id3 ", tag))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    id3 = _chunk_named(chunks, "id3 ")
    assert any("declares" in w for w in id3["warnings"]), id3["warnings"]


# ── ResU, DISP and the padding pair ─────────────────────────────────
#
# The second sweep, over 1,200 files rather than 266: JUNK in 492, LGWV in 147,
# ResU in 107, FLLR in 56, DISP in 4. The first sweep had missed ResU and LGWV
# entirely -- the sample size was the limit, not the method.


def _resu(doc):
    import json
    import zlib
    return _chunk(b"ResU", zlib.compress(json.dumps(doc).encode("utf-8")))


def _logic_doc(tempo=120, signature="4/4", beats=5, duration=2.1666666666667):
    ctx = {
        "version": 2, "offset": 0, "duration": duration,
        "Tempo": [{"t": 0.104, "conf": -1, "tempo": tempo}],
        "time_signatures": [{"t": 0, "conf": -1, "signature": signature}],
        "beats": [{"t": i * 0.5, "conf": -1, "onset": 1} for i in range(beats)],
        "UserEdited": 0,
    }
    return {"version": 2, "rec_ctx": ctx, **ctx}


def test_resu_reads_logics_tempo_and_signature(tmp_path):
    """ResU is zlib-compressed JSON: Logic's own analysis of the file, written
    into it. On a loop library it is the tempo someone actually worked to."""
    data = _wav_with(_resu(_logic_doc(tempo=126, signature="3/4", beats=8)))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    resu = _chunk_named(chunks, "ResU")
    f = {x["name"]: x["value"] for x in resu["fields"]}
    assert f["tempo"] == "126 BPM"
    assert f["time_signature"] == "3/4"
    assert f["beats"] == "8"
    assert "126 BPM" in resu["summary"]


def test_a_resu_that_is_not_zlib_says_so(tmp_path):
    data = _wav_with(_chunk(b"ResU", b"plainly not compressed at all"))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    resu = _chunk_named(chunks, "ResU")
    assert any("zlib" in w for w in resu["warnings"]), resu["warnings"]


def test_a_resu_that_inflates_to_non_json_says_so(tmp_path):
    import zlib
    data = _wav_with(_chunk(b"ResU", zlib.compress(b"\xff\xfe not json")))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    resu = _chunk_named(chunks, "ResU")
    assert resu["summary"] == "undecodable"
    assert any("JSON" in w for w in resu["warnings"]), resu["warnings"]


def test_disp_reads_a_text_clipboard_payload(tmp_path):
    """DISP's first u32 is a Windows clipboard format id and the rest is that
    format's own bytes. Read as text regardless, a bitmap prints as mojibake."""
    data = _wav_with(_chunk(b"DISP", struct.pack("<I", 1) + b"My Title\x00"))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    disp = _chunk_named(chunks, "DISP")
    f = {x["name"]: x["value"] for x in disp["fields"]}
    assert f["clipboard_format"] == 1
    assert f["text"] == "My Title"


def test_disp_reads_a_dib_thumbnail_header(tmp_path):
    """CF_DIB is 8, and what follows is a BITMAPINFOHEADER rather than text.
    Real files carry a small thumbnail here."""
    dib = struct.pack("<Iii", 40, 30, 16) + struct.pack("<HH", 1, 4)
    data = _wav_with(_chunk(b"DISP", struct.pack("<I", 8) + dib + b"\x00" * 64))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    disp = _chunk_named(chunks, "DISP")
    assert "30x16 at 4 bpp" in disp["summary"], disp["summary"]


def test_padding_is_named_rather_than_dumped(tmp_path):
    data = _wav_with(_chunk(b"JUNK", b"\x00" * 512))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    junk = _chunk_named(chunks, "JUNK")
    assert junk["summary"] == "padding, 512 zero bytes"
    assert junk["warnings"] == []


def test_padding_that_is_not_zero_is_a_finding(tmp_path):
    """Padding is written zero. A JUNK chunk that is not is one that was
    overwritten in place with something shorter, and what is left is the tail
    of whatever used to be there."""
    stale = b"\x00" * 32 + b"ISFT" + b"Sound Forge 9.0\x00" + b"\x00" * 32
    data = _wav_with(_chunk(b"JUNK", stale))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    junk = _chunk_named(chunks, "JUNK")
    assert "NOT zero" in junk["summary"], junk["summary"]
    assert any("overwritten in place" in w for w in junk["warnings"])
    f = {x["name"]: x["value"] for x in junk["fields"]}
    assert "Sound Forge" in f["readable"]


def test_fllr_and_pad_are_padding_too(tmp_path):
    for cid in (b"FLLR", b"PAD "):
        data = _wav_with(_chunk(cid, b"\x00" * 128))
        _l, chunks, _w = _walk_bytes(tmp_path, data, cid.strip().decode() + ".wav")
        entry = _chunk_named(chunks, cid.decode())
        assert "padding" in entry["summary"], (cid, entry["summary"])


# ── the third sweep: 2,327 files ────────────────────────────────────


def test_the_id3_parser_answers_to_both_spellings(tmp_path):
    """Chunk ids are matched exactly, and the parser was registered under the
    lowercase spelling only. 25 files in a real library carried an uppercase
    `ID3 ` chunk holding a TBPM frame -- a tempo -- and it was reported as
    unparsed bytes."""
    for cid in (b"id3 ", b"ID3 "):
        data = _wav_with(_chunk(cid, _id3_chunk()[8:]))
        _l, chunks, _w = _walk_bytes(tmp_path, data, cid.strip().decode() + ".wav")
        entry = _chunk_named(chunks, cid.decode())
        assert "ID3v2" in entry["summary"], (cid, entry["summary"])


def test_a_little_endian_tag_size_is_used_and_named(tmp_path):
    """A writer in the wild puts the tag size little-endian, which is not the
    format. 0f 00 00 00 read as the spec requires is 31,457,280; read as that
    writer meant it, 15 -- and 15 is what the chunk actually holds.

    The same writer gets the FRAME size right, so the two halves of one header
    disagree about their own byte order.
    """
    body = b"TBPM" + struct.pack(">I", 5) + b"\x00\x00" + b"\x00" + b"126\x00"
    tag = b"ID3" + bytes([4, 0, 0]) + struct.pack("<I", len(body)) + body
    data = _wav_with(_chunk(b"ID3 ", tag))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    entry = _chunk_named(chunks, "ID3 ")
    f = {x["name"]: x["value"] for x in entry["fields"]}
    assert f["tag_size"] == f"{len(body):,}"
    assert f["TBPM"] == "126"
    assert any("little-endian" in w for w in entry["warnings"]), entry["warnings"]


def test_a_conformant_tag_is_not_reinterpreted(tmp_path):
    """The control, and the reason the fallback is narrow: it fires only when
    the spec reading does not fit AND the little-endian one fits exactly. A
    guess that merely looked plausible would re-read conformant tags."""
    data = _wav_with(_id3_chunk())
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    entry = _chunk_named(chunks, "id3 ")
    f = {x["name"]: x["value"] for x in entry["fields"]}
    assert f["TIT2"] == "Test Title"
    assert not any("little-endian" in w for w in entry["warnings"])


def test_cset_reads_the_code_page(tmp_path):
    """Everything else holding text is bytes until something says how to
    decode them. CSET is that something."""
    data = _wav_with(_chunk(b"CSET", struct.pack("<HHHH", 28591, 0, 9, 1)))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    cset = _chunk_named(chunks, "CSET")
    f = {x["name"]: x["value"] for x in cset["fields"]}
    assert f["code_page"] == 28591
    assert "28591" in cset["summary"]


def test_a_copyright_chunk_is_read_as_text(tmp_path):
    data = _wav_with(_chunk(b"(c) ", b"Example Studios\x00"))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    c = _chunk_named(chunks, "(c) ")
    assert c["summary"] == "Example Studios"


def test_apple_metadata_is_named_not_decoded(tmp_path):
    """AFAn/AFmd hold a NeXTSTEP typedstream -- a serialised object graph.
    Reading it properly means implementing typedstream, which is a format of
    its own. Saying what the bytes ARE and listing the classes the archive
    references is the honest amount."""
    archive = (bytes([0x04, 0x0B]) + b"streamtyped" + b"\x81\xe8\x03\x84\x01@"
               + bytes([19]) + b"NSMutableDictionary"
               + bytes([12]) + b"NSDictionary" + b"\x00" * 16)
    data = _wav_with(_chunk(b"AFAn", archive))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    afan = _chunk_named(chunks, "AFAn")
    assert "Apple typedstream" in afan["summary"]
    classes = [x["value"] for x in afan["fields"] if x["name"] == "class"]
    assert "NSMutableDictionary" in classes and "NSDictionary" in classes


def test_an_afan_that_is_not_a_typedstream_says_so(tmp_path):
    data = _wav_with(_chunk(b"AFmd", b"nothing like an archive here at all"))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    afmd = _chunk_named(chunks, "AFmd")
    assert any("typedstream" in w for w in afmd["warnings"]), afmd["warnings"]


# -- the fourth sweep: what a 6,001-file WAV walk still called bytes ---


def _xmp_packet(props='<xmp:CreatorTool>Soundminer v4</xmp:CreatorTool>'):
    return ('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 5.5.0">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" '
            'xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/">'
            + props +
            '</rdf:Description></rdf:RDF></x:xmpmeta>'
            '<?xpacket end="w"?>').encode("utf-8")


def test_a_pmx_chunk_is_an_xmp_packet(tmp_path):
    """`_PMX` holds Adobe's XMP packet -- RDF/XML, and a sound library writes
    its whole catalogue record into it. The chunk was being reported as
    unparsed bytes with an XML document inside."""
    props = ('<xmp:CreatorTool>Soundminer v4</xmp:CreatorTool>'
             '<dc:publisher><rdf:Bag><rdf:li>www.example.com</rdf:li>'
             '</rdf:Bag></dc:publisher>'
             '<xmpDM:artist>Example Library</xmpDM:artist>')
    data = _wav_with(_chunk(b"_PMX", _xmp_packet(props)))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    pmx = _chunk_named(chunks, "_PMX")
    f = {x["name"]: x["value"] for x in pmx["fields"]}
    assert f["xmp:CreatorTool"] == "Soundminer v4"
    assert f["dc:publisher"] == "www.example.com"
    assert f["xmpDM:artist"] == "Example Library"
    assert "Soundminer v4" in pmx["summary"]


def test_an_alt_keeps_one_language_and_a_bag_keeps_every_entry(tmp_path):
    """RDF has two containers that look alike and do not mean alike. An
    `rdf:Alt` is one value in several languages; joining it prints the same
    sentence twice. An `rdf:Bag` is several values, and dropping all but the
    first loses data."""
    props = ('<dc:title><rdf:Alt>'
             '<rdf:li xml:lang="x-default">Kick</rdf:li>'
             '<rdf:li xml:lang="en-US">Kick</rdf:li>'
             '</rdf:Alt></dc:title>'
             '<dc:subject><rdf:Bag><rdf:li>drum</rdf:li>'
             '<rdf:li>percussion</rdf:li></rdf:Bag></dc:subject>')
    data = _wav_with(_chunk(b"_PMX", _xmp_packet(props)))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    f = {x["name"]: x["value"] for x in _chunk_named(chunks, "_PMX")["fields"]}
    assert f["dc:title"] == "Kick"
    assert f["dc:subject"] == "drum, percussion"


def test_a_property_outside_the_known_namespaces_still_reaches_the_reader(tmp_path):
    """Only the namespaces are named, never the property names. A whitelist of
    properties is how a reader silently drops the one field that mattered."""
    props = ('<vendor:SecretSauce xmlns:vendor="http://example.com/ns/vendor/">'
             '42</vendor:SecretSauce>')
    data = _wav_with(_chunk(b"_PMX", _xmp_packet(props)))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    f = {x["name"]: x["value"] for x in _chunk_named(chunks, "_PMX")["fields"]}
    assert f["vendor:SecretSauce"] == "42"


def test_a_pmx_that_is_not_well_formed_says_so(tmp_path):
    data = _wav_with(_chunk(b"_PMX", b'<?xpacket begin=""?><x:xmpmeta '
                                     b'xmlns:x="adobe:ns:meta/"><oops>'
                                     b"</x:xmpmeta>"))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    pmx = _chunk_named(chunks, "_PMX")
    assert any("well-formed" in w for w in pmx["warnings"]), pmx["warnings"]


def test_minf_reads_its_timestamp_as_a_filetime(tmp_path):
    """Nothing in the chunk says the first eight bytes are a date. The reading
    is offered rather than asserted, and it is offered because it is the one
    that produces sane answers -- as a FILETIME the field lands in the years
    the files were made, and read any other common way it does not."""
    stamp = 0x01CE308F38E4D2DC                   # 2013-04-03
    data = _wav_with(_chunk(b"minf", struct.pack("<QI", stamp, 1) + b"\x00" * 4))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    minf = _chunk_named(chunks, "minf")
    f = {x["name"]: x for x in minf["fields"]}
    assert "2013-04-03" in f["timestamp"]["note"]
    assert "2013-04-03" in minf["summary"]
    assert f["flag"]["value"] == 1


def test_an_implausible_minf_timestamp_is_not_dressed_up_as_a_date(tmp_path):
    """The control, and the reason the reading is bounded: a field that is not
    a date decodes to a year in the far future, and printing that as fact would
    turn noise into provenance."""
    data = _wav_with(_chunk(b"minf", struct.pack("<QI", 1, 1) + b"\x00" * 4))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    minf = _chunk_named(chunks, "minf")
    assert not {x["name"]: x for x in minf["fields"]}["timestamp"]["note"]


def test_an_avid_chunk_is_named_not_decoded(tmp_path):
    """There is no published layout for a Pro Tools region table, and a field
    map guessed from one vendor's files is a guess that reads like a fact. What
    IS certain is which tool wrote it, and what the block calls its own
    records."""
    body = (bytes([0, 1]) + b"AnalysisSetsHdr" + bytes([0, 2])
            + b"PacketStreamData" + bytes([0]) + b"W]0!" + bytes([0]))
    data = _wav_with(_chunk(b"DGDA", body))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    dgda = _chunk_named(chunks, "DGDA")
    assert "Digidesign" in dgda["summary"]
    runs = [x["value"] for x in dgda["fields"] if x["name"] == "text"]
    # the punctuation run is dropped: binary data throws off short printable
    # runs constantly, and listing those dresses noise up as a finding
    assert runs == ["AnalysisSetsHdr", "PacketStreamData"]


def test_logic_chunks_are_named_as_logic(tmp_path):
    """LGWV appears in 790 files across BOTH containers, which is what made it
    a tool's fingerprint rather than a container quirk. Of the LGWV files that
    also carry a bext chunk, 28 of 33 name Logic Pro as the originator, and
    Apple say the same in their own support forum.

    No published layout exists, so it stays named-not-decoded. But a named
    writer is a provenance fact and an unknown chunk is not."""
    from acidcat.core.walk.base import VENDOR_CHUNKS

    assert VENDOR_CHUNKS["LGWV"] == "Logic Pro"
    for cid in ("LGWV", "LGBM"):
        data = _wav_with(_chunk(cid.encode("ascii"), bytes(64)))
        _l, chunks, _w = _walk_bytes(tmp_path, data, cid + ".wav")
        entry = _chunk_named(chunks, cid)
        assert entry["summary"].startswith("Logic Pro"), entry["summary"]


def test_a_vendor_chunk_of_pure_noise_lists_nothing(tmp_path):
    """The control for the label filter. A chunk of arbitrary bytes has
    printable runs in it by chance, and reporting those as text would turn
    every encoded blob into a list of findings."""
    body = bytes((i * 37 + 11) & 0xFF for i in range(2048))
    data = _wav_with(_chunk(b"SMED", body))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    smed = _chunk_named(chunks, "SMED")
    assert smed["summary"].startswith("Soundminer")
    runs = [x["value"] for x in smed["fields"] if x["name"] == "text"]
    assert len(runs) <= 2, runs


def test_every_avid_chunk_id_is_registered(tmp_path):
    """The ID3 lesson: a table is only as good as the ids in it, and a chunk id
    is matched exactly. Registering the family and then walking one of them is
    how a spelling goes missing."""
    from acidcat.core.walk import wav as wwav

    for cid in wwav._AVID_CHUNKS:
        data = _wav_with(_chunk(cid.encode("ascii"), b"\x00" * 32))
        _l, chunks, _w = _walk_bytes(tmp_path, data, cid.strip() + ".wav")
        entry = _chunk_named(chunks, cid)
        assert "unparsed" not in entry["summary"], (cid, entry["summary"])


def test_a_garbage_chunk_id_is_not_rendered_as_a_shorter_one(tmp_path):
    """`decode("ascii", errors="ignore")` does something worse than fail: it
    DROPS the bytes it cannot read and keeps the rest.

    A damaged chunk id of ed f3 34 e5 came back as the string "4", so the
    walker reported a chunk named `4` that does not exist and then repeated
    that invented name inside the warning about it. The census had rendered
    the same bytes as hex all along, which is two answers for one file.
    """
    data = _wav_with(_chunk(bytes([0xED, 0xF3, 0x34, 0xE5]), bytes(16)))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    ids = [c["id"] for c in chunks]
    assert "hex:edf334e5" in ids, ids
    assert "4" not in ids


def test_a_printable_chunk_id_is_untouched(tmp_path):
    """The control. Hex-escaping an ordinary id would be far worse than the
    bug it fixes."""
    from acidcat.core.formats.riff import safe_fourcc

    assert safe_fourcc(b"fmt ") == "fmt "
    assert safe_fourcc(b"(c) ") == "(c) "
    data = _wav_with(_chunk(b"acid", bytes(24)))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    assert "acid" in [c["id"] for c in chunks]


def test_the_census_and_the_walker_render_an_id_the_same_way():
    """One definition. Two renderings of one damaged file is how a reader ends
    up unable to tell whether two tools saw the same thing."""
    from acidcat.core import census
    from acidcat.core.formats.riff import safe_fourcc

    assert census._safe_fourcc is safe_fourcc


# -- the case-variant bug class, pinned ---------------------------------

_PADDING_IDS = ("JUNK", "junk", "FLLR", "filr", "PAD ", "pad ")


def test_every_padding_spelling_gets_the_padding_check(tmp_path):
    """Chunk ids are matched EXACTLY, so every spelling a writer uses has to
    be in the table.

    A census of 867,703 files found two that were not: lowercase `junk` in
    12,881 of them and `filr` in 15,639. Both are all-zero in every specimen
    examined, which is to say both are padding -- so 28,520 files were getting
    no overwritten-data check because of two missing table entries.

    This is the third time this bug has appeared. `id3 ` versus `ID3 ` was the
    first and cost 25 files a tempo; this one cost 28,520 files a forensic
    check. The list is pinned here rather than trusted.
    """
    from acidcat.core.walk.wav import _PARSERS, _parse_padding

    for cid in _PADDING_IDS:
        assert _PARSERS.get(cid) is _parse_padding, f"{cid!r} is not padding"


def test_lowercase_padding_that_is_not_zero_is_still_a_finding(tmp_path):
    """The behaviour the missing entries were costing: a `junk` chunk that is
    not zero is space overwritten in place, and it has to say so in whatever
    case the writer used."""
    payload = bytes(24) + b"ISFT was here" + bytes(11)
    for cid in (b"junk", b"filr"):
        data = _wav_with(_chunk(cid, payload))
        _l, chunks, warns = _walk_bytes(tmp_path, data, cid.decode() + ".wav")
        entry = _chunk_named(chunks, cid.decode())
        assert "NOT zero" in entry["summary"], (cid, entry["summary"])
        readable = next(f for f in entry["fields"] if f["name"] == "readable")
        assert "ISFT was here" in readable["value"]


def test_peak_reads_each_channel(tmp_path):
    """`PEAK` is on the WAV anatomy page -- "records each channel's peak so a
    reader can normalize and draw the waveform without a full scan" -- and the
    walker did not read it. 149 files in an 867,703-file census carry one.

    These are the real bytes from one of them: version 1, a Unix timestamp the
    format defines, and one float-plus-frame record per channel.
    """
    payload = bytes.fromhex("01000000ae42e053e35b783f12010000")
    data = _wav_with(_chunk(b"PEAK", payload))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    peak = _chunk_named(chunks, "PEAK")
    f = {x["name"]: x for x in peak["fields"]}
    assert f["version"]["value"] == 1
    assert f["timestamp"]["note"] == "2014-08-05"
    assert f["peak[0]"]["value"] == "0.970152 at frame 274"
    assert not peak["warnings"]


def test_a_peak_past_unit_scale_is_stated_not_corrected(tmp_path):
    """Float WAV is allowed past 0 dBFS, and a writer that normalises to 2^23
    rather than 1.0 shows up here as a huge number rather than a clipped one.
    Reporting it as damage would be wrong; hiding it would be worse."""
    payload = struct.pack("<II", 1, 0) + struct.pack("<fI", 8388608.0, 100)
    data = _wav_with(_chunk(b"PEAK", payload))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    peak = _chunk_named(chunks, "PEAK")
    fs = next(x for x in peak["fields"] if x["name"] == "full_scale")
    assert "2^23" in fs["note"]
    assert not any("damage" in w or "clip" in w for w in peak["warnings"])


def test_peak_record_count_is_checked_against_the_channels(tmp_path):
    """A peak chunk that disagrees with fmt about how many channels exist is
    one of the two telling a lie."""
    payload = struct.pack("<II", 1, 0) + struct.pack("<fI", 0.5, 10) * 3
    data = _wav_with(_chunk(b"PEAK", payload))      # _wav_with writes 1 channel
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    peak = _chunk_named(chunks, "PEAK")
    assert any("3 peak record(s) for 1 channel" in w
               for w in peak["warnings"]), peak["warnings"]


def _cue(cue_id, sample=0):
    """One cue point: id, play order, chunk, then the sample frame."""
    return struct.pack("<II4sIII", cue_id, 0, b"data", 0, 0, sample)


def test_plst_is_the_playlist_the_spec_defines(tmp_path):
    """The spec's OWN loop machinery -- cue points plus a playlist carrying a
    repeat count -- and it lost. Across one production library `plst` appears
    48 times and `smpl` 154,697. Read anyway: 48 files is 48 files."""
    plst = struct.pack("<I", 1) + struct.pack("<III", 2, 19928, 1)
    data = _wav_with(_chunk(b"plst", plst)
                     + _chunk(b"cue ", struct.pack("<I", 1) + _cue(2)))
    _l, chunks, warns = _walk_bytes(tmp_path, data)
    p = _chunk_named(chunks, "plst")
    seg = next(f for f in p["fields"] if f["name"] == "segment[0]")
    assert seg["value"] == "19,928 frames"
    assert seg["note"].startswith("cue 2")
    assert not warns


def test_a_playlist_naming_a_cue_that_does_not_exist(tmp_path):
    """A segment names a cue point by id and `cue ` is what defines those
    ids. A playlist pointing at a cue nobody declared is broken, and nothing
    else in the file says so."""
    plst = struct.pack("<I", 1) + struct.pack("<III", 99, 1000, 1)
    data = _wav_with(_chunk(b"plst", plst)
                     + _chunk(b"cue ", struct.pack("<I", 1) + _cue(2)))
    _l, _chunks, warns = _walk_bytes(tmp_path, data)
    assert any("cue point(s) [99]" in w and "does not define" in w
               for w in warns), warns


def test_a_playlist_with_no_cue_chunk_at_all(tmp_path):
    """Distinguished from the above, because "the cue chunk is missing" and
    "the cue chunk omits this id" are different repairs."""
    plst = struct.pack("<I", 1) + struct.pack("<III", 4, 1000, 1)
    data = _wav_with(_chunk(b"plst", plst))
    _l, _chunks, warns = _walk_bytes(tmp_path, data)
    assert any("no cue chunk" in w for w in warns), warns


def test_a_segment_that_plays_zero_times(tmp_path):
    """dwLoops is a repeat count, so zero means the segment is in the playlist
    and never plays. Legal, and worth saying out loud."""
    plst = struct.pack("<I", 1) + struct.pack("<III", 2, 1000, 0)
    data = _wav_with(_chunk(b"plst", plst)
                     + _chunk(b"cue ", struct.pack("<I", 1) + _cue(2)))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    assert any("not at all" in w
               for w in _chunk_named(chunks, "plst")["warnings"])


def test_a_plst_that_lies_about_its_segment_count(tmp_path):
    plst = struct.pack("<I", 50) + struct.pack("<III", 2, 1000, 1)
    data = _wav_with(_chunk(b"plst", plst))
    _l, chunks, _w = _walk_bytes(tmp_path, data)
    assert any("declares 50 segments" in w
               for w in _chunk_named(chunks, "plst")["warnings"])


# ── four chunks nobody documents, measured on 30 specimens each ─────
#
# ExifTool, which is the most comprehensive metadata reader there is,
# documents none of them. Three turn out to carry no per-file information at
# all, which is a finding rather than a failure: "68 constant bytes in every
# file measured" is a complete description, and it is what stops the next
# reader spending an evening on it.

def _tlst(count, note=0x3C, target=b"cue ", kind=1, sel=(0, 0)):
    rec = (target + struct.pack("<HHI", sel[0], sel[1], kind)
           + bytes([0xFF, note, 0x90, 0]) + bytes(8))
    return _chunk(b"tlst", struct.pack("<I", count) + rec * count)


def _fields(chunks, cid):
    c = next(x for x in chunks if x["id"].strip() == cid.strip())
    return c, {f["name"]: f["value"] for f in c["fields"]}


def test_tlst_is_a_count_and_fixed_24_byte_records(tmp_path):
    """4 + count * 24 is the whole payload in all 30 specimens measured,
    across four different payload sizes."""
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_tlst(3)))
    c, vals = _fields(chunks, "tlst")
    assert vals["triggers"] == 3
    assert len([f for f in c["fields"] if f["name"].startswith("trigger[")]) == 3
    assert not c["warnings"]


def test_a_trigger_names_the_chunk_it_targets(tmp_path):
    """Every record in every specimen names `cue `, so a trigger targets a
    cue point. A record naming anything else has not been seen and says so."""
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_tlst(1)))
    _c, vals = _fields(chunks, "tlst")
    assert vals["trigger[0]"].startswith("cue ")

    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_tlst(1, target=b"labl")), "b.wav")
    c, _vals = _fields(chunks, "tlst")
    assert any("targets" in w for w in c["warnings"])


def test_the_midi_reading_is_offered_beside_the_raw_bytes(tmp_path):
    """0x90 is MIDI's Note On and the byte beside it took 60 through 63 across
    the specimens. That is a reading, so the raw bytes are shown with it."""
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_tlst(1, note=0x3E)))
    c, _vals = _fields(chunks, "tlst")
    note = next(f["note"] for f in c["fields"] if f["name"] == "trigger[0]")
    assert "MIDI note 62" in note
    assert "ff 3e 90 00" in note


def test_a_trigger_word_that_is_not_note_on_gets_no_reading(tmp_path):
    """The reading is conditional on the byte that carries it. Printing a note
    number for bytes that are not a note-on would be inventing one."""
    body = struct.pack("<I", 1) + b"cue " + struct.pack("<HHI", 0, 0, 1) \
        + bytes([0xFF, 0x3C, 0x00, 0]) + bytes(8)
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_chunk(b"tlst", body)))
    c, _vals = _fields(chunks, "tlst")
    note = next(f["note"] for f in c["fields"] if f["name"] == "trigger[0]")
    assert "MIDI note" not in note
    assert "raw" in note


def test_a_declared_trigger_count_the_payload_cannot_hold_is_reported(tmp_path):
    body = struct.pack("<I", 99) + b"cue " + struct.pack("<HHI", 0, 0, 1) \
        + bytes([0xFF, 0x3C, 0x90, 0]) + bytes(8)
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_chunk(b"tlst", body)))
    c, _vals = _fields(chunks, "tlst")
    assert any("declares 99" in w for w in c["warnings"])


def test_fake_is_reported_as_the_placeholder_it_is(tmp_path):
    """Two bytes in 29 of 30 specimens, four in one, always NULs or spaces."""
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_chunk(b"Fake", b"\x00\x00")))
    c, vals = _fields(chunks, "Fake")
    assert "blank" in c["summary"]
    assert vals["bytes"] == "00 00"


def test_a_Fake_that_is_not_blank_says_so(tmp_path):
    """No specimen measured carries anything. One that did would be new."""
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_chunk(b"Fake", b"\x01\x02")))
    c, _vals = _fields(chunks, "Fake")
    assert "not blank" in c["summary"]


def test_chrp_is_twelve_zero_bytes(tmp_path):
    """All 30 specimens, from four unrelated kits, are zero. It travels with
    `muma`, which does carry values, so it looks like a companion record its
    writer never fills in."""
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_chunk(b"chrp", bytes(12))))
    c, vals = _fields(chunks, "chrp")
    assert "12 zero byte(s)" in c["summary"]
    assert not c["warnings"]


def test_a_chrp_carrying_something_says_so(tmp_path):
    _l, chunks, _w = _walk_bytes(
        tmp_path, _wav_with(_chunk(b"chrp", b"" + bytes(11))))
    c, _vals = _fields(chunks, "chrp")
    assert "not zero" in c["summary"]


def test_saur_is_a_version_stamp(tmp_path):
    """All 30 specimens are byte-identical and read 1.1.0.0, across two
    unrelated libraries, so it stamps the writer and not the file."""
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_chunk(b"SAUR", b"1.1.0.0" + bytes(25))))
    c, vals = _fields(chunks, "SAUR")
    assert vals["version"] == "1.1.0.0"
    assert not c["warnings"]


def test_a_saur_of_an_unmeasured_length_says_so(tmp_path):
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_chunk(b"SAUR", b"9.9" + bytes(5))))
    c, _vals = _fields(chunks, "SAUR")
    assert any("every specimen measured is 32" in w for w in c["warnings"])


def test_cdif_repeats_its_own_size_and_is_otherwise_empty(tmp_path):
    """Thirty specimens from unrelated libraries are byte-identical."""
    body = struct.pack("<II", 68, 1) + bytes(60)
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_chunk(b"CDif", body)))
    c, vals = _fields(chunks, "CDif")
    assert vals["size"] == 68 and vals["value"] == 1
    assert "60 zero bytes" in str(vals["rest"])
    assert not c["warnings"]


def test_a_cdif_carrying_something_is_flagged_as_new(tmp_path):
    body = struct.pack("<II", 68, 1) + b"\x07" + bytes(59)
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_chunk(b"CDif", body)))
    c, _vals = _fields(chunks, "CDif")
    note = next(f["note"] for f in c["fields"] if f["name"] == "rest")
    assert "NOT zero" in note


def test_a_cdif_whose_declared_size_disagrees_is_reported(tmp_path):
    body = struct.pack("<II", 999, 1) + bytes(60)
    _l, chunks, _w = _walk_bytes(tmp_path, _wav_with(_chunk(b"CDif", body)))
    c, _vals = _fields(chunks, "CDif")
    assert any("declares 999" in w for w in c["warnings"])
