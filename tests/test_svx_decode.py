"""Tests for the IFF 8SVX -> WAV decode/convert path (core/svx.py)."""

import io
import struct
import wave

import pytest

from acidcat.core import svx


def _chunk(cid, payload):
    return cid + struct.pack(">I", len(payload)) + payload + (
        b"\x00" if len(payload) & 1 else b"")


def _vhdr(one=0, rep=0, cyc=0, rate=8000, octs=1, comp=0, vol=0x10000):
    return _chunk(b"VHDR", struct.pack(">IIIHBBI", one, rep, cyc, rate, octs, comp, vol))


def _form(chunks, ftype=b"8SVX"):
    body = ftype + b"".join(chunks)
    return b"FORM" + struct.pack(">I", len(body)) + body


def test_is_8svx():
    assert svx.is_8svx(_form([_vhdr(), _chunk(b"BODY", b"\x00" * 4)]))
    assert not svx.is_8svx(b"RIFF\x00\x00\x00\x00WAVE")
    assert not svx.is_8svx(b"FORM\x00\x00\x00\x00AIFF")
    assert not svx.is_8svx(b"short")


def test_raw_decode_signed_wrap():
    # bytes decode as SIGNED 8-bit: 0,127,128,255 -> 0,127,-128,-1
    body = bytes([0, 127, 128, 255])
    data = _form([_vhdr(one=4, comp=0), _chunk(b"BODY", body)])
    info, samples = svx.decode(data)
    assert info["compression_name"] == "raw 8-bit PCM"
    assert samples == [0, 127, -128, -1]
    assert info["num_samples"] == 4


def test_fibonacci_decode_known_value():
    # BODY = [pad, seed=0, 0x8F]; nibble 8 -> delta 0, nibble 15 -> delta 21
    body = bytes([0x00, 0x00, 0x8F])
    data = _form([_vhdr(one=2, comp=1), _chunk(b"BODY", body)])
    info, samples = svx.decode(data)
    assert info["compression_name"] == "Fibonacci-delta"
    assert samples == [0, 21]


def test_fibonacci_seed_is_signed():
    # seed byte 0xF0 = -16 signed; the seed is not emitted, the first sample is
    # seed + first delta. byte 0x88 -> nibbles 8,8 -> DELTA[8]=0 twice, so the
    # signed seed shows straight through.
    body = bytes([0x00, 0xF0, 0x88])
    _, samples = svx.decode(data=_form([_vhdr(one=2, comp=1),
                                        _chunk(b"BODY", body)]))
    assert samples == [-16, -16]                        # -16 + 0, then + 0


def test_high_octave_trim():
    # 3 octaves, high octave = one+rep = 4 samples; body has 12 -> keep first 4
    body = bytes(range(12))
    data = _form([_vhdr(one=4, rep=0, octs=3, comp=0), _chunk(b"BODY", body)])
    info, samples = svx.decode(data)
    assert info["num_samples"] == 4
    assert samples == [0, 1, 2, 3]


def test_no_length_keeps_full_body():
    # oneShot=0 and repeat=0 -> no trim, render the whole body
    body = bytes([1, 2, 3, 4, 5])
    _, samples = svx.decode(_form([_vhdr(one=0, rep=0), _chunk(b"BODY", body)]))
    assert samples == [1, 2, 3, 4, 5]


def test_to_wav_is_valid_16bit_mono():
    data = _form([_vhdr(one=4, rate=11025, comp=0),
                  _chunk(b"BODY", bytes([0, 64, 127, 128]))])
    info, samples = svx.decode(data)
    wav = svx.to_wav(info, samples)
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 11025
        assert w.getnframes() == 4
        frames = w.readframes(4)
    # 8-bit samples are scaled up by 256
    assert struct.unpack("<4h", frames) == (0, 64 * 256, 127 * 256, -128 * 256)


def test_zero_rate_defaults_and_flags():
    data = _form([_vhdr(one=2, rate=0, comp=0), _chunk(b"BODY", b"\x01\x02")])
    info, samples = svx.decode(data)
    wav = svx.to_wav(info, samples)
    assert info.get("rate_defaulted") is True
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert w.getframerate() == 8000


def test_missing_vhdr_raises():
    data = _form([_chunk(b"BODY", b"\x00" * 4)])
    with pytest.raises(svx.SvxError, match="VHDR"):
        svx.decode(data)


def test_missing_body_raises():
    data = _form([_vhdr()])
    with pytest.raises(svx.SvxError, match="BODY"):
        svx.decode(data)


def test_unsupported_compression_raises():
    data = _form([_vhdr(comp=2), _chunk(b"BODY", b"\x00" * 4)])
    with pytest.raises(svx.SvxError, match="sCompression"):
        svx.decode(data)


def test_not_8svx_raises():
    with pytest.raises(svx.SvxError, match="8SVX"):
        svx.decode(b"RIFF\x00\x00\x00\x00WAVEfmt ")
