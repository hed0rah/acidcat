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
