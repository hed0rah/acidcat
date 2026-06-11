"""tests for acidcat.core.riff."""

import struct
import time

import pytest
from acidcat.core.riff import iter_chunks, get_riff_info, get_duration, parse_riff


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


class TestGetDuration:
    def test_silent_wav(self, silent_wav):
        dur = get_duration(silent_wav)
        assert dur is not None
        assert abs(dur - 0.1) < 0.01  # 4410 samples at 44100 Hz = 0.1s

    def test_minimal_wav(self, minimal_wav):
        # 4 samples -- very short but valid
        dur = get_duration(minimal_wav)
        assert dur is not None
        assert dur >= 0

    def test_not_riff(self, not_riff):
        # should return None or 0, not raise
        result = get_duration(not_riff)
        assert result is None or result == 0

    def test_empty_file(self, empty_file):
        result = get_duration(empty_file)
        assert result is None or result == 0


class TestParseRiff:
    def test_returns_three_tuple(self, minimal_wav):
        results, meta, seen = parse_riff(minimal_wav)
        assert isinstance(results, list)
        assert isinstance(meta, dict)
        assert isinstance(seen, list)

    def test_fmt_fields_present(self, minimal_wav):
        _, meta, seen = parse_riff(minimal_wav)
        assert "fmt " in seen

    def test_sample_rate_correct(self, minimal_wav):
        results, _, _ = parse_riff(minimal_wav, enumerate_all=True)
        sr_entry = next((v for c, k, v in results if k == "sample_rate"), None)
        assert sr_entry == 44100

    def test_channels_correct(self, minimal_wav):
        results, _, _ = parse_riff(minimal_wav, enumerate_all=True)
        ch_entry = next((v for c, k, v in results if k == "channels"), None)
        assert ch_entry == 1

    def test_huge_num_cues_does_not_hang(self, tmp_path):
        """B-7: the CUE chunk parser reads `num_cues` as a raw uint32
        with no validation against the chunk's actual payload size, then
        iterates `range(num_cues)`. A corrupt or malicious WAV with
        `num_cues = 0xFFFFFFFF` would spin ~4 billion iterations before
        the inner length check rejects each empty slice. This isn't
        a crash, just 40-80s of wasted CPU per bad file.

        Build a WAV whose `cue ` chunk advertises 0xFFFFFFFF cues but
        carries zero real cue records. parse_riff must return promptly
        (well under one second) and emit at most `payload_size // 24`
        cue marker rows.
        """
        # fmt chunk
        fmt = struct.pack(
            "<HHIIHH", 1, 1, 44100, 44100 * 2, 2, 16,
        )
        fmt_chunk = b"fmt " + struct.pack("<I", 16) + fmt
        # data chunk (tiny)
        data_chunk = b"data" + struct.pack("<I", 4) + b"\x00" * 4
        # cue chunk: 4-byte num_cues header claiming 0xFFFFFFFF, then no
        # actual cue records (payload smaller than what num_cues * 24
        # would require). Total cue payload size = 4 bytes.
        cue_payload = struct.pack("<I", 0xFFFFFFFF)
        cue_chunk = b"cue " + struct.pack("<I", len(cue_payload)) + cue_payload
        riff_body = b"WAVE" + fmt_chunk + data_chunk + cue_chunk
        wav = b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body

        wav_path = tmp_path / "huge_cue.wav"
        wav_path.write_bytes(wav)

        t0 = time.perf_counter()
        results, _, _ = parse_riff(str(wav_path), enumerate_all=True)
        elapsed = time.perf_counter() - t0

        # tight ceiling -- the fix should make this a no-op
        assert elapsed < 0.5, (
            f"parse_riff took {elapsed:.2f}s on a 4-billion-cue claim"
        )
        # bounded marker count: 4-byte payload after the 4-byte
        # num_cues header leaves zero room for cue records, so the
        # parser must not synthesize any.
        cue_markers = [
            (c, k, v) for c, k, v in results
            if c == "cue " and isinstance(k, str) and k.startswith("marker_")
        ]
        assert len(cue_markers) == 0


    def test_acid_chunk_spec_layout(self, tmp_path):
        """the acid chunk layout is, per libsndfile and field-verified
        against real ACIDized packs:

            offset 0   uint32  type flags
            offset 4   uint16  root note
            offset 6   uint16  q1 (unknown, often 0x8000)
            offset 8   float32 q2 (unknown, observed 0.0)
            offset 12  uint32  num_beats
            offset 16  uint16  meter denominator
            offset 18  uint16  meter numerator
            offset 20  float32 tempo

        the old parser unpacked '<IHHIII f', which read num_beats from
        the q2 float (always 0 in the wild) and the meter as two
        uint32s spanning the real num_beats and the packed meter words.
        every spec-conformant file reported acid_beats=0 and garbage
        meter. real loops must surface their actual beat count.
        """
        fmt = struct.pack("<HHIIHH", 1, 1, 44100, 44100 * 2, 2, 16)
        fmt_chunk = b"fmt " + struct.pack("<I", 16) + fmt
        data_chunk = b"data" + struct.pack("<I", 4) + b"\x00" * 4
        # 15-beat 4/4 loop at 122 bpm, root C4: mirrors a verified
        # real-world acid payload byte for byte
        acid_payload = struct.pack(
            "<IHHfIHHf", 0x05, 60, 0x8000, 0.0, 15, 4, 4, 122.0,
        )
        acid_chunk = b"acid" + struct.pack("<I", 24) + acid_payload
        body = b"WAVE" + fmt_chunk + data_chunk + acid_chunk
        wav = tmp_path / "acid_loop.wav"
        wav.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

        results, meta, _ = parse_riff(str(wav), enumerate_all=True)
        assert meta["acid_beats"] == 15
        assert meta["acid_root_note"] == 60
        assert meta["bpm"] == 122.0
        meter = next((v for c, k, v in results if k == "meter"), None)
        assert meter == "4/4"

    def test_drum_loop_if_present(self):
        import os
        from conftest import SAMPLE_WAV
        if not os.path.isfile(SAMPLE_WAV):
            pytest.skip("Drum_Loop.wav not present")
        results, meta, seen = parse_riff(SAMPLE_WAV)
        assert "fmt " in seen
        assert "data" in seen
