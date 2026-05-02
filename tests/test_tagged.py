"""tests for acidcat.core.tagged -- mutagen-based tag parsing."""

import os
import pytest

# skip entire module if mutagen is not installed
mutagen = pytest.importorskip("mutagen", reason="mutagen not installed; skip tagged tests")

from acidcat.core.tagged import parse_tagged, is_tagged_format, TAGGED_EXTENSIONS


FIXTURES = os.path.join(os.path.dirname(__file__), "..", "data", "test_formats")


def test_strip_bom_removes_leading_feff():
    # F-26: a leading UTF-8 BOM in a tag value would otherwise leak
    # into the FTS index and break matching on the affected rows.
    from acidcat.core.tagged import _strip_bom
    assert _strip_bom("﻿hello") == "hello"
    assert _strip_bom("﻿﻿trim both") == "trim both"
    assert _strip_bom("clean") == "clean"
    assert _strip_bom(None) is None
    assert _strip_bom(42) == 42


def fixture_path(name):
    return os.path.join(FIXTURES, name)


def has_fixture(name):
    return os.path.isfile(fixture_path(name))


class TestIsTaggedFormat:
    @pytest.mark.parametrize("ext", TAGGED_EXTENSIONS)
    def test_known_extensions(self, tmp_path, ext):
        p = tmp_path / f"test{ext}"
        p.write_bytes(b"")
        assert is_tagged_format(str(p)) is True

    def test_wav_is_not_tagged(self, tmp_path):
        p = tmp_path / "test.wav"
        p.write_bytes(b"")
        assert is_tagged_format(str(p)) is False

    def test_case_insensitive(self, tmp_path):
        p = tmp_path / "test.MP3"
        p.write_bytes(b"")
        assert is_tagged_format(str(p)) is True


class TestParseTaggedEdgeCases:
    def test_garbage_mp3_returns_none(self, bad_mp3):
        """mutagen raises HeaderNotFoundError on a garbage MP3 -- should return None."""
        result = parse_tagged(bad_mp3)
        assert result is None

    def test_nonexistent_file(self, tmp_path):
        """missing file should return None, not raise."""
        result = parse_tagged(str(tmp_path / "nonexistent.mp3"))
        assert result is None

    def test_zero_byte_mp3(self, tmp_path):
        p = tmp_path / "zero.mp3"
        p.write_bytes(b"")
        result = parse_tagged(str(p))
        assert result is None


class TestParseTaggedMp3:
    @pytest.fixture(autouse=True)
    def skip_without_fixture(self):
        if not has_fixture("gs-16b-2c-44100hz.mp3"):
            pytest.skip("MP3 fixture not present")

    def test_returns_dict(self):
        result = parse_tagged(fixture_path("gs-16b-2c-44100hz.mp3"))
        assert isinstance(result, dict)

    def test_format_type_is_mp3(self):
        result = parse_tagged(fixture_path("gs-16b-2c-44100hz.mp3"))
        assert result["format_type"] == "mp3"

    def test_has_duration(self):
        result = parse_tagged(fixture_path("gs-16b-2c-44100hz.mp3"))
        assert result.get("duration") is not None
        assert result["duration"] > 0

    def test_sample_rate_44100(self):
        result = parse_tagged(fixture_path("gs-16b-2c-44100hz.mp3"))
        assert result.get("sample_rate") == 44100


class TestParseTaggedFlac:
    @pytest.fixture(autouse=True)
    def skip_without_fixture(self):
        if not has_fixture("gs-16b-2c-44100hz.flac"):
            pytest.skip("FLAC fixture not present")

    def test_returns_dict(self):
        result = parse_tagged(fixture_path("gs-16b-2c-44100hz.flac"))
        assert isinstance(result, dict)

    def test_format_type_is_flac(self):
        result = parse_tagged(fixture_path("gs-16b-2c-44100hz.flac"))
        assert result["format_type"] == "flac"

    def test_bits_per_sample(self):
        result = parse_tagged(fixture_path("gs-16b-2c-44100hz.flac"))
        assert result.get("bits_per_sample") == 16


class TestParseTaggedOgg:
    @pytest.fixture(autouse=True)
    def skip_without_fixture(self):
        if not has_fixture("gs-16b-2c-44100hz.ogg"):
            pytest.skip("OGG fixture not present")

    def test_format_type_is_ogg(self):
        result = parse_tagged(fixture_path("gs-16b-2c-44100hz.ogg"))
        assert result is not None
        assert result["format_type"] == "ogg"


class TestParseTaggedOpus:
    @pytest.fixture(autouse=True)
    def skip_without_fixture(self):
        if not has_fixture("gs-16b-2c-44100hz.opus"):
            pytest.skip("Opus fixture not present")

    def test_format_type_is_opus(self):
        result = parse_tagged(fixture_path("gs-16b-2c-44100hz.opus"))
        assert result is not None
        assert result["format_type"] == "opus"


class TestParseTaggedM4a:
    @pytest.fixture(autouse=True)
    def skip_without_fixture(self):
        if not has_fixture("gs-16b-2c-44100hz.m4a"):
            pytest.skip("M4A fixture not present")

    def test_format_type_is_m4a(self):
        result = parse_tagged(fixture_path("gs-16b-2c-44100hz.m4a"))
        assert result is not None
        assert result["format_type"] == "m4a"

    def test_has_duration(self):
        result = parse_tagged(fixture_path("gs-16b-2c-44100hz.m4a"))
        assert result.get("duration") is not None
        assert result["duration"] > 0
