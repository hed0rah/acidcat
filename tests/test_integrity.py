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


from acidcat.core.walk.aiff import inspect_aiff


def _aiff(tmp_path, bits, samples, name="t.aiff"):
    bps = bits // 8
    pcm = b"".join(int(s & ((1 << bits) - 1)).to_bytes(bps, "big") for s in samples)
    # COMM: channels, frames, sampleSize, 80-bit sample rate (44100 Hz)
    comm = struct.pack(">HIH", 1, len(samples), bits) + b"\x40\x0e\xacD\x00\x00\x00\x00\x00\x00"
    ssnd = struct.pack(">II", 0, 0) + pcm            # offset, blockSize, then PCM
    body = (b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm
            + b"SSND" + struct.pack(">I", len(ssnd)) + ssnd)
    p = tmp_path / name
    p.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    return str(p)


def _run_aiff(path):
    chunks, _warns = inspect_aiff(path, "AIFF")
    with open(path, "rb") as f:
        data = f.read()
    return integrity.analyze("IFF/AIFF", chunks, data)


def test_aiff_fake_24bit_flagged(tmp_path):
    vals = [(i * 517 & 0xFFFF) << 8 for i in range(4000)]   # 16-bit left-shifted
    findings = _run_aiff(_aiff(tmp_path, 24, vals))
    assert findings and "effective 16-bit" in findings[0]["verdict"]


def test_aiff_genuine_24bit_not_flagged(tmp_path):
    vals = [(i * 2654435761) & 0xFFFFFF for i in range(4000)]
    assert _run_aiff(_aiff(tmp_path, 24, vals)) == []


def test_aiff_genuine_16bit_not_flagged(tmp_path):
    vals = [(i * 40503) & 0xFFFF for i in range(4000)]
    assert _run_aiff(_aiff(tmp_path, 16, vals)) == []


def _mp4_dur(mdhd_dur, stts_pairs):
    def box(t, p): return struct.pack(">I", 8 + len(p)) + t + p
    # mdhd v0: ver/flags, creation, mod, timescale(1000), duration, lang, pre
    mdhd = box(b"mdhd", struct.pack(">I", 0) + b"\x00" * 8
               + struct.pack(">II", 1000, mdhd_dur) + b"\x00" * 4)
    stts_body = struct.pack(">I", 0) + struct.pack(">I", len(stts_pairs))
    for cnt, delta in stts_pairs:
        stts_body += struct.pack(">II", cnt, delta)
    stts = box(b"stts", stts_body)
    stbl = box(b"stbl", stts)
    minf = box(b"minf", stbl)
    mdia = box(b"mdia", mdhd + minf)
    tree = box(b"moov", box(b"trak", mdia))
    ftyp = box(b"ftyp", b"M4A \x00\x00\x00\x00")
    return ftyp + tree + box(b"mdat", b"\x00" * 16)


def test_mp4_duration_consistent_not_flagged():
    # mdhd duration 8000 == stts sum (1000 * 8)
    data = _mp4_dur(8000, [(1000, 8)])
    assert integrity.analyze("MP4/M4A", [], data) == []


def test_mp4_duration_mismatch_flagged():
    # mdhd says 4000 but the sample table sums to 8000
    data = _mp4_dur(4000, [(1000, 8)])
    findings = integrity.analyze("MP4/M4A", [], data)
    assert findings and findings[0]["check"] == "duration"
    assert "disagree" in findings[0]["detail"]
