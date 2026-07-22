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


def test_be16_to_wav_byteswaps():
    raw = struct.pack(">hhh", 1, 2, -3)                  # big-endian 16-bit PCM
    w = wave.open(io.BytesIO(smod._be16_to_wav(raw, 44100)), "rb")
    assert struct.unpack("<3h", w.readframes(3)) == (1, 2, -3)


def _zip_multisample(tmp_path):
    import zipfile
    def _wavbytes(n=100):
        b = io.BytesIO()
        with wave.open(b, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(struct.pack(f"<{n}h", *([1000] * n)))
        return b.getvalue()
    p = tmp_path / "pack.multisample"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("multisample.xml", b"<multisample name='t'/>")
        z.writestr("a - mix.wav", _wavbytes())
        z.writestr("b - mix.wav", _wavbytes(50))
    return str(p)


def test_multisample_extraction(tmp_path):
    p = _zip_multisample(tmp_path)
    recs = [r for r in smod.iter_samples(p) if r.get("wav")]
    assert len(recs) == 2
    assert {r["name"] for r in recs} == {"a - mix", "b - mix"}
    assert recs[0]["wav"][:4] == b"RIFF"


# KRZ builders (mirrors tests/test_krz.py) --------------------------------------
def _krz_object(type_code, oid, name, body):
    n = len(name)
    pad = b"\x00" if n % 2 else b"\x00\x00"
    ofs = n + (3 if n % 2 else 4)
    inner = struct.pack(">HHH", (type_code << 10) | oid, 0, ofs) + name.encode() + pad + body
    total = 4 + len(inner)
    total += (-total) % 4
    inner += b"\x00" * (total - 4 - len(inner))
    return struct.pack(">i", -total) + inner


def _krz_sample_body(rate=44100):
    period = round(1e9 / rate)
    ksample = struct.pack(">hhhBBhh", 1, 0, 8, 0, 0, 0, 0)
    sfh = (struct.pack(">BBBB", 60, 0x70, 0, 0) + struct.pack(">HH", 0, 0)
           + struct.pack(">iiii", 0, 0, 100, 200)        # start=0, ..., end=200 words
           + struct.pack(">HH", 8, 6) + struct.pack(">I", period))
    envs = struct.pack(">hhhhhh", -1, 1, 0, 0, -1600, 0) * 2
    return ksample + sfh + envs


def test_krz_extraction(tmp_path):
    obj = _krz_object(38, 200, "Snare", _krz_sample_body())
    body = obj + struct.pack(">i", 0)
    osize = 32 + len(body)
    header = b"PRAM" + struct.pack(">i", osize) + struct.pack(">iii", 0, 0, 207) + b"\x00" * 12
    pcm = struct.pack(">200h", *range(200))              # 200 words, big-endian
    p = tmp_path / "bank.krz"
    p.write_bytes(header + body + pcm)
    recs = [r for r in smod.iter_samples(str(p)) if r.get("wav")]
    assert len(recs) == 1 and recs[0]["name"] == "Snare"
    w = wave.open(io.BytesIO(recs[0]["wav"]), "rb")
    assert w.getnframes() == 200                          # end(200) - start(0) words
    assert struct.unpack("<2h", w.readframes(2)) == (0, 1)   # byteswapped BE range


def test_s3m_frames_unsigned_8bit():
    # S3M 8-bit is unsigned: 0x80 (128) is the zero-crossing -> 0
    raw = bytes([128, 128 + 64, 128 - 64])
    frames = smod._s3m_frames(raw, bits16=False, stereo=False)
    assert struct.unpack("<3h", frames) == (0, 64 * 256, -64 * 256)


def test_s3m_frames_stereo_interleave():
    # stereo stored as L-block then R-block -> interleaved LR in the WAV
    raw = bytes([128 + 10, 128 + 20, 128 - 10, 128 - 20])   # L=[+10,+20], R=[-10,-20]
    frames = smod._s3m_frames(raw, bits16=False, stereo=True)
    assert struct.unpack("<4h", frames) == (10 * 256, -10 * 256, 20 * 256, -20 * 256)


def _gf1_patch(pcm, rate=44100, name=b"snare"):
    hdr = bytearray(129); hdr[0:12] = b"GF1PATCH110\x00"; hdr[82] = 1
    inst = bytearray(63); inst[22] = 1
    layer = bytearray(47); layer[6] = 1
    sh = bytearray(96); sh[0:len(name)] = name
    struct.pack_into("<I", sh, 8, len(pcm)); struct.pack_into("<H", sh, 20, rate)
    sh[55] = 0x02                                        # 8-bit unsigned
    return bytes(hdr + inst + layer + sh) + pcm


def test_gf1_extraction(tmp_path):
    p = tmp_path / "k.pat"
    p.write_bytes(_gf1_patch(bytes([128 + i for i in range(30)]), rate=44100))
    recs = [r for r in smod.iter_samples(str(p)) if r.get("wav")]
    assert len(recs) == 1 and recs[0]["name"] == "snare"
    w = wave.open(io.BytesIO(recs[0]["wav"]), "rb")
    assert w.getframerate() == 44100 and w.getnframes() == 30
    assert struct.unpack("<1h", w.readframes(1))[0] == 0     # 128 unsigned -> 0


def test_extractable_set():
    assert {"mod", "xm", "it", "s3m", "gf1pat", "8svx", "ncw", "sf2",
            "multisample", "krz"} <= smod.EXTRACTABLE
