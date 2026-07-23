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


# ── Microsoft ADPCM (0x0002) ──────────────────────────────────────────────

def test_ms_nibble_predictor_and_delta():
    # coef (256, 0): pred = sample1 * 256 / 256 = sample1; nibble 0 adds nothing
    pred, delta = adpcm._ms_nibble(0, 100, 50, 256, 0, 16)
    assert pred == 100
    assert delta == 16                                   # (230*16)>>8 = 14, clamped up to 16


def test_ms_mono_emits_older_then_recent_sample():
    # header: predictor idx 0, delta 16, sample1 100, sample2 50, one data byte 0x00
    block = struct.pack("<BhhhB", 0, 16, 100, 50, 0x00)
    out = adpcm.decode_ms_adpcm(block, 8, 1)
    v = struct.unpack(f"<{len(out) // 2}h", out)
    assert v[0] == 50 and v[1] == 100                    # sample2 (older) first, then sample1
    assert v[2] == 100 and v[3] == 100                   # both nibbles predict 100 under coef (256,0)


def test_ms_stereo_interleaves_channels():
    # 14-byte stereo header (pred/delta/s1/s2 per channel) + one data byte
    block = struct.pack("<BBhhhhhhB", 0, 0, 16, 16, 100, 200, 50, 150, 0x00)
    out = adpcm.decode_ms_adpcm(block, 15, 2)
    v = struct.unpack(f"<{len(out) // 2}h", out)
    assert v[0] == 50 and v[1] == 150                    # sample2 L, R
    assert v[2] == 100 and v[3] == 200                   # sample1 L, R
    assert v[4] == 100 and v[5] == 200                   # high nibble -> L, low nibble -> R


def test_convert_to_pcm_ms_by_tag(tmp_path):
    import types
    import wave
    from acidcat.commands import convert
    std = [(256, 0), (512, -256), (0, 0), (192, 64), (240, 0), (460, -208), (392, -232)]
    block = struct.pack("<BhhhB", 0, 16, 100, 50, 0x00)  # -> 4 samples
    ext = struct.pack("<HH", 4, 7) + b"".join(struct.pack("<hh", a, b) for a, b in std)
    fmtbody = struct.pack("<HHIIHH", 0x0002, 1, 22050, 22050, 8, 4) + struct.pack("<H", len(ext)) + ext
    fmt = b"fmt " + struct.pack("<I", len(fmtbody)) + fmtbody
    body = b"WAVE" + fmt + b"data" + struct.pack("<I", len(block)) + block
    p = tmp_path / "ms.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    out = tmp_path / "ms_out.wav"
    args = types.SimpleNamespace(input=str(p), output=str(out), to_pcm=True, codec=None,
                                 division=480, skip_existing=False, quiet=True)
    assert convert.run(args) == 0
    with wave.open(str(out)) as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1 and w.getframerate() == 22050
        assert w.getnframes() == 4                       # sample2, sample1, + 2 nibbles
        assert struct.unpack("<4h", w.readframes(4)) == (50, 100, 100, 100)
