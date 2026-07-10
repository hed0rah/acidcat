"""tests for acidcat.core.sniff, the canonical magic sniffer."""

import struct

from acidcat.core.sniff import sniff, sniff_bytes


def _pad(b, n=16):
    return b + b"\x00" * max(0, n - len(b))


class TestSniffBytes:
    def test_wav(self):
        assert sniff_bytes(_pad(b"RIFF\x24\x00\x00\x00WAVE")) == "wav"

    def test_rf64(self):
        assert sniff_bytes(_pad(b"RF64\xff\xff\xff\xffWAVE")) == "rf64"

    def test_aiff_and_aifc(self):
        assert sniff_bytes(_pad(b"FORM\x00\x00\x00\x20AIFF")) == "aiff"
        assert sniff_bytes(_pad(b"FORM\x00\x00\x00\x20AIFC")) == "aifc"

    def test_midi_needs_14_bytes(self):
        head = b"MThd\x00\x00\x00\x06\x00\x01\x00\x02\x01\xe0"
        assert sniff_bytes(head) == "midi"
        # a 13-byte head is not enough to be dispatched as MIDI
        assert sniff_bytes(head[:13]) is None

    def test_serum(self):
        assert sniff_bytes(_pad(b"XferJson")) == "serum"

    def test_bitwig(self):
        assert sniff_bytes(_pad(b"BtWg0001")) == "bitwig"

    def test_vital(self):
        assert sniff_bytes(_pad(b'{"synth_v')) == "vital"

    def test_mp4(self):
        assert sniff_bytes(_pad(b"\x00\x00\x00\x20ftypM4A ")) == "mp4"

    def test_ni_hsin(self):
        assert sniff_bytes(_pad(b"\x00" * 12 + b"hsin")) == "ni"

    def test_ni_niks_riff(self):
        # RIFF/NIKS is an NI preset, not a WAV
        assert sniff_bytes(_pad(b"RIFF\x10\x00\x00\x00NIKS")) == "ni"

    def test_flac(self):
        assert sniff_bytes(_pad(b"fLaC\x00\x00\x00\x22")) == "flac"

    def test_ogg(self):
        assert sniff_bytes(_pad(b"OggS\x00\x02")) == "ogg"

    def test_id3_is_mp3(self):
        assert sniff_bytes(_pad(b"ID3\x04\x00\x00\x00\x00\x00\x00")) == "mp3"

    def test_bare_mpeg_frame(self):
        # MPEG 1 Layer III, 128 kbps, 44100 Hz
        assert sniff_bytes(_pad(b"\xff\xfb\x90\x00")) == "mp3"

    def test_garbage_sync_rejected(self):
        # sync bits set but reserved layer (layer bits 00): not a frame header
        assert sniff_bytes(_pad(b"\xff\xe1\x90\x00")) is None

    def test_unknown_and_short(self):
        assert sniff_bytes(b"") is None
        assert sniff_bytes(_pad(b"\x00" * 16)) is None
        assert sniff_bytes(_pad(b"NOPE")) is None


class TestSniffFile:
    def test_wav_file(self, minimal_wav):
        assert sniff(minimal_wav) == "wav"

    def test_empty_file(self, empty_file):
        assert sniff(empty_file) is None

    def test_id3_wrapping_wav_is_flagged(self, tmp_path):
        # ID3v2 header, empty 0-byte body, then a RIFF/WAVE container
        tag = b"ID3\x03\x00\x00\x00\x00\x00\x00"
        wav = b"RIFF" + struct.pack("<I", 4) + b"WAVE"
        p = tmp_path / "wrapped.wav"
        p.write_bytes(tag + wav)
        assert sniff(str(p)) == "id3-wrapped"

    def test_id3_over_mpeg_stays_mp3(self, tmp_path):
        tag = b"ID3\x03\x00\x00\x00\x00\x00\x00"
        frame = b"\xff\xfb\x90\x00" + b"\x00" * 100
        p = tmp_path / "tagged.mp3"
        p.write_bytes(tag + frame)
        assert sniff(str(p)) == "mp3"

    def test_free_format_mp3_confirmed_by_twin_sync(self, tmp_path):
        # bitrate index 0 (free format) sniffs as mp3 only when a matching
        # second sync confirms the constant frame length
        frame = bytes([0xFF, 0xFB, 0x00, 0xC0]) + b"\x00" * 296
        p = tmp_path / "free.bin"                  # extensionless on purpose
        p.write_bytes(frame * 3)
        assert sniff(str(p)) == "mp3"

    def test_lone_free_sync_not_sniffed(self, tmp_path):
        p = tmp_path / "junk.bin"
        p.write_bytes(bytes([0xFF, 0xFB, 0x00, 0xC0]) + b"\x11" * 500)
        assert sniff(str(p)) is None
