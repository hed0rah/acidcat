"""Tests for unified sample extraction (core/samples.py)."""

import io
import struct
import wave

import pytest

from acidcat.core import samples as smod


def _make_mod(pcm_bytes=20):
    title = b"TEST".ljust(20, b"\x00")
    hdrs = []
    for i in range(31):
        name = b"snare".ljust(22, b"\x00") if i == 0 else b"\x00" * 22
        words = (pcm_bytes // 2) if i == 0 else 0
        hdrs.append(name + struct.pack(">H", words) + bytes([0, 64]) + struct.pack(">HH", 0, 1))
    order = bytes([0]) + b"\x00" * 127
    body = title + b"".join(hdrs) + bytes([1, 127]) + order + b"M.K."
    body += b"\x00" * (64 * 4 * 4)                       # one 4-channel pattern
    body += bytes(range(pcm_bytes))                      # sample 0 PCM
    return body


def _svx(rate=8000, body=b"\x01\x02\x03\x04"):
    def chunk(cid, p):
        return cid + struct.pack(">I", len(p)) + p + (b"\x00" if len(p) & 1 else b"")
    vhdr = chunk(b"VHDR", struct.pack(">IIIHBBI", len(body), 0, 0, rate, 1, 0, 0x10000))
    inner = b"8SVX" + vhdr + chunk(b"BODY", body)
    return b"FORM" + struct.pack(">I", len(inner)) + inner


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_undelta8_accumulates():
    # deltas 1,1,1 -> running values 1,2,3
    assert smod._undelta8(bytes([1, 1, 1])) == bytes([1, 2, 3])


def test_undelta16_accumulates():
    raw = struct.pack("<HHH", 1, 1, 1)
    assert smod._undelta16(raw) == struct.pack("<HHH", 1, 2, 3)


def test_mod_extraction(tmp_path):
    p = _write(tmp_path, "k.mod", _make_mod(pcm_bytes=20))
    recs = [r for r in smod.iter_samples(p) if r.get("wav")]
    assert len(recs) == 1
    assert recs[0]["name"] == "snare"
    w = wave.open(io.BytesIO(recs[0]["wav"]), "rb")
    assert w.getsampwidth() == 2 and w.getnframes() == 20    # 20 x 8-bit -> 20 frames 16-bit


def test_svx_extraction(tmp_path):
    p = _write(tmp_path, "v.8svx", _svx(body=bytes(range(10))))
    recs = list(smod.iter_samples(p))
    assert len(recs) == 1 and recs[0]["name"] == "voice"
    assert recs[0]["wav"][:4] == b"RIFF"


def test_unsupported_format_raises(tmp_path):
    # a plain WAV is not a sample-bearing bank
    p = _write(tmp_path, "x.wav", b"RIFF" + struct.pack("<I", 4) + b"WAVE")
    with pytest.raises(smod.SampleError, match="no sample extractor"):
        list(smod.iter_samples(p))


def test_extractable_set():
    assert {"mod", "xm", "it", "8svx", "ncw", "sf2"} <= smod.EXTRACTABLE
