"""shared fixtures for acidcat tests."""

import struct
import tempfile
import os
import pytest


@pytest.fixture(autouse=True)
def _isolate_acidcat_env(monkeypatch):
    """Strip acidcat env vars so a dev shell with ACIDCAT_REGISTRY/ACIDCAT_DB
    set cannot leak into the test process and corrupt the user's real
    registry or single-DB index. Applied to every test.
    """
    monkeypatch.delenv("ACIDCAT_DB", raising=False)
    monkeypatch.delenv("ACIDCAT_REGISTRY", raising=False)


def _make_riff_wav(sample_rate=44100, channels=1, bits=16, num_samples=4):
    """Build a minimal valid PCM WAV in memory."""
    block_align = channels * bits // 8
    byte_rate = sample_rate * block_align
    audio_data = b"\x00" * (num_samples * block_align)

    fmt = struct.pack(
        "<HHIIHH",
        1,           # PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
    )
    # fmt chunk: id + size + data (16 bytes)
    fmt_chunk = b"fmt " + struct.pack("<I", 16) + fmt
    # data chunk
    data_chunk = b"data" + struct.pack("<I", len(audio_data)) + audio_data
    # RIFF header: size = 4 (WAVE) + len(fmt_chunk) + len(data_chunk)
    riff_body = b"WAVE" + fmt_chunk + data_chunk
    return b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body


@pytest.fixture
def minimal_wav(tmp_path):
    """Write a minimal valid WAV to a temp file."""
    p = tmp_path / "minimal.wav"
    p.write_bytes(_make_riff_wav())
    return str(p)


@pytest.fixture
def silent_wav(tmp_path):
    """Slightly longer WAV: 4410 samples (0.1 s at 44100 Hz)."""
    p = tmp_path / "silent.wav"
    p.write_bytes(_make_riff_wav(num_samples=4410))
    return str(p)


@pytest.fixture
def not_riff(tmp_path):
    """A file with no RIFF magic bytes."""
    p = tmp_path / "not_riff.wav"
    p.write_bytes(b"\x00" * 64)
    return str(p)


@pytest.fixture
def empty_file(tmp_path):
    """A zero-byte file with a .wav extension."""
    p = tmp_path / "empty.wav"
    p.write_bytes(b"")
    return str(p)


@pytest.fixture
def truncated_riff(tmp_path):
    """A file that starts with RIFF but is truncated."""
    p = tmp_path / "truncated.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", 1000) + b"WAVE" + b"fmt ")  # cuts off
    return str(p)


@pytest.fixture
def bad_mp3(tmp_path):
    """An MP3-extension file that contains garbage (triggers mutagen error)."""
    p = tmp_path / "bad.mp3"
    p.write_bytes(b"\x00" * 72)
    return str(p)


# real test files (skip if absent)
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "test_formats")


def real_file(name):
    path = os.path.join(FIXTURES_DIR, name)
    return pytest.mark.skipif(
        not os.path.isfile(path),
        reason=f"test fixture {name} not present",
    )(path)


SAMPLE_WAV = os.path.join(
    os.path.dirname(__file__), "..", "data", "samples", "Drum_Loop.wav"
)
