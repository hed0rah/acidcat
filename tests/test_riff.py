"""tests for acidcat.core.riff and the WAV walker behaviors that
replaced the legacy parse_riff/get_duration parsers."""

import struct
import time

import pytest
from acidcat.core.riff import iter_chunks, get_riff_info, effective_acid_beats
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
