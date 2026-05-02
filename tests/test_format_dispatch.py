"""F-21: format dispatch should sniff magic bytes, not extensions only.

Double-suffixed files (e.g. foo.aiff.wav from a bad batch convert)
must route by actual content, not by the trailing suffix.
"""

import struct

import pytest

from acidcat.commands.index import _sniff_format


def _wav_bytes():
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 44100 * 2, 2, 16)
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    data_chunk = b"data" + struct.pack("<I", 0)
    body = b"WAVE" + fmt_chunk + data_chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _aiff_bytes():
    body = b"AIFF" + b"COMM" + struct.pack(">I", 18) + b"\x00" * 18
    return b"FORM" + struct.pack(">I", len(body)) + body


def _midi_bytes():
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", 0)


class TestSniffFormat:
    def test_wav(self, tmp_path):
        p = tmp_path / "x.wav"
        p.write_bytes(_wav_bytes())
        assert _sniff_format(str(p)) == "wav"

    def test_aiff(self, tmp_path):
        p = tmp_path / "x.aiff"
        p.write_bytes(_aiff_bytes())
        assert _sniff_format(str(p)) == "aiff"

    def test_midi(self, tmp_path):
        p = tmp_path / "x.mid"
        p.write_bytes(_midi_bytes())
        assert _sniff_format(str(p)) == "midi"

    def test_serum(self, tmp_path):
        p = tmp_path / "x.fxp"
        p.write_bytes(b"XferJson" + b"{\"presetName\":\"x\"}")
        assert _sniff_format(str(p)) == "serum"

    def test_flac_magic(self, tmp_path):
        p = tmp_path / "x.flac"
        p.write_bytes(b"fLaC" + b"\x00" * 8)
        assert _sniff_format(str(p)) == "flac"

    def test_double_suffix_aiff_dot_wav(self, tmp_path):
        # F-21 regression: a file written as AIFF but renamed to .wav
        # must still be recognized as AIFF, not WAV. Extension fallback
        # only kicks in when the magic bytes are unknown.
        p = tmp_path / "broken.aiff.wav"
        p.write_bytes(_aiff_bytes())
        assert _sniff_format(str(p)) == "aiff"

    def test_unknown_magic_returns_none(self, tmp_path):
        p = tmp_path / "x.bin"
        p.write_bytes(b"\x00" * 64)
        assert _sniff_format(str(p)) is None

    def test_short_file_returns_none(self, tmp_path):
        p = tmp_path / "tiny"
        p.write_bytes(b"AB")
        assert _sniff_format(str(p)) is None

    def test_unreadable_returns_none(self, tmp_path):
        # nonexistent file
        assert _sniff_format(str(tmp_path / "no.wav")) is None
