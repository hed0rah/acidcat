"""Tests for the IMA/DVI ADPCM decoder (core/adpcm.py)."""

import struct

from acidcat.core import adpcm


def test_ima_step_known_values():
    # nibble 4 at index 0: diff = step(7) -> pred 7, index += 2
    assert adpcm._ima_step(4, 0, 0) == (7, 2)
    # nibble 8 is the sign bit alone: diff = -(step>>3) = 0; index clamps at 0
    assert adpcm._ima_step(8, 0, 0) == (0, 0)


def test_continuous_two_samples_per_byte_in_range():
    out = adpcm.decode_ima_continuous(bytes(range(256)))
    v = struct.unpack(f"<{len(out) // 2}h", out)
    assert len(v) == 256 * 2                              # low + high nibble each byte
    assert all(-32768 <= x <= 32767 for x in v)


def test_block_mono_first_sample_is_the_predictor():
    block = struct.pack("<hBB", 5000, 10, 0) + bytes(60)  # header primes at 5000, idx 10
    out = adpcm.decode_ima(block, 64, 1)
    v = struct.unpack(f"<{len(out) // 2}h", out)
    assert v[0] == 5000                                  # priming sample emitted first


def test_stereo_interleaves_two_channels():
    # two-channel block: 8-byte header (4 per channel) + one interleaved word pair
    hdr = struct.pack("<hBB", 100, 0, 0) + struct.pack("<hBB", -100, 0, 0)
    out = adpcm.decode_ima(hdr + bytes(8), 16, 2)
    v = struct.unpack(f"<{len(out) // 2}h", out)
    assert v[0] == 100 and v[1] == -100                  # L, R priming samples interleaved


def test_convert_to_pcm_forced(tmp_path):
    import types
    import wave
    from acidcat.commands import convert
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 0x0014, 1, 22050, 44100, 2, 4)
    body = b"WAVE" + fmt + b"data" + struct.pack("<I", 20) + bytes(range(20))
    p = tmp_path / "mistagged.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    out = tmp_path / "out.wav"
    args = types.SimpleNamespace(input=str(p), output=str(out), to_pcm=True, codec="ima",
                                 division=480, skip_existing=False, quiet=True)
    assert convert.run(args) == 0
    with wave.open(str(out)) as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1
        assert w.getframerate() == 22050 and w.getnframes() == 40   # 20 bytes x 2 nibbles
