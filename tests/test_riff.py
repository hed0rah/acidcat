"""tests for acidcat.core.riff."""

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

    def test_drum_loop_if_present(self):
        import os
        from conftest import SAMPLE_WAV
        if not os.path.isfile(SAMPLE_WAV):
            pytest.skip("Drum_Loop.wav not present")
        results, meta, seen = parse_riff(SAMPLE_WAV)
        assert "fmt " in seen
        assert "data" in seen
