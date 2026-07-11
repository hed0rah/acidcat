"""Integrity checks: effective-bit-depth (fake hi-res) detection on WAV PCM."""
import struct

from acidcat.core import integrity
from acidcat.core.walk.wav import inspect_wav


def _wav(tmp_path, bits, samples, tag=1, name="t.wav"):
    """Build a PCM WAV. `samples` is a list of ints stored little-endian at the
    given bit depth."""
    bps = bits // 8
    fmt = struct.pack("<HHIIHH", tag, 1, 44100, 44100 * bps, bps, bits)
    pcm = b"".join(int(s & ((1 << bits) - 1)).to_bytes(bps, "little") for s in samples)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    p = tmp_path / name
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p)


def _run(path):
    chunks, _warns = inspect_wav(path)
    with open(path, "rb") as f:
        data = f.read()
    return integrity.analyze("RIFF/WAVE", chunks, data)


def test_fake_24bit_flagged(tmp_path):
    # 16-bit values left-shifted into 24-bit: low byte always zero
    vals = [(i * 517 & 0xFFFF) << 8 for i in range(4000)]
    path = _wav(tmp_path, 24, vals)
    findings = _run(path)
    assert findings and findings[0]["check"] == "bit_depth"
    assert "effective 16-bit" in findings[0]["verdict"]


def test_genuine_24bit_not_flagged(tmp_path):
    # values that use the low bits too
    vals = [(i * 2654435761) & 0xFFFFFF for i in range(4000)]
    path = _wav(tmp_path, 24, vals)
    assert _run(path) == []


def test_genuine_16bit_not_flagged(tmp_path):
    vals = [(i * 40503) & 0xFFFF for i in range(4000)]
    path = _wav(tmp_path, 16, vals)
    assert _run(path) == []


def test_silence_not_judged(tmp_path):
    path = _wav(tmp_path, 24, [0] * 4000)
    assert _run(path) == []                     # all-zero: no bit depth to read


def test_float_pcm_skipped(tmp_path):
    # tag 3 (IEEE float): not integer PCM, must be ignored
    path = _wav(tmp_path, 32, [1] * 4000, tag=3)
    assert _run(path) == []
