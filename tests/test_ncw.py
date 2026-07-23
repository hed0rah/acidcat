"""NCW lossless decode: ground-truth vectors (build a known NCW, decode, assert
bit-exact) plus error handling. The synthetic encoder here is the inverse of the
decoder, so a round-trip proves the bit-packing and accumulation exactly."""
import struct

import pytest

from acidcat.core import ncw

_BLK = 512


def _pack_signed(vals, precision):
    """LSB-first bit-pack signed values into `precision` bits each (inverse of
    ncw._unpack_signed)."""
    out = bytearray()
    acc = nbits = 0
    mask = (1 << precision) - 1
    for v in vals:
        acc |= (v & mask) << nbits
        nbits += precision
        while nbits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nbits -= 8
    if nbits:
        out.append(acc & 0xFF)
    return bytes(out)


def _block_channel(samples512, bits, base, flags, bps):
    """One channel sub-block: 16-byte header + packed data."""
    hdr = struct.pack("<iihH", 0x3E9A0C16, base, bits, flags) + b"\x00\x00\x00\x00"
    if bits == 0:                                    # raw
        body = b"".join(int(s).to_bytes(bps // 8, "little", signed=True)
                        for s in samples512)
    elif bits > 0:                                   # delta
        deltas = [samples512[i + 1] - samples512[i] for i in range(_BLK - 1)] + [0]
        body = _pack_signed(deltas, bits)
    else:                                            # truncation
        body = _pack_signed(samples512, -bits)
    return hdr, body


def make_ncw(channels, bps, rate, chan_samples, bits, flags=0):
    """Build a one-block NCW (<=512 samples/channel) from known samples. `bits`
    picks the mode: 0 raw, >0 delta, <0 truncation. flags -> the block header."""
    n = len(chan_samples[0])
    padded = [list(cs) + [cs[-1]] * (_BLK - len(cs)) for cs in chan_samples]
    block = bytearray()
    for ci in range(channels):
        base = padded[ci][0] if bits > 0 else 0
        h, b = _block_channel(padded[ci], bits, base, flags, bps)
        block += h + b
    blk_addr = 0x78
    n_offsets = 2                                    # one block + end marker
    blk_data = blk_addr + n_offsets * 4
    header = bytearray(0x78)
    header[0:4] = ncw.MAGIC
    struct.pack_into("<I", header, 4, 0x131)
    struct.pack_into("<H", header, 8, channels)
    struct.pack_into("<H", header, 0xA, bps)
    struct.pack_into("<I", header, 0xC, rate)
    struct.pack_into("<I", header, 0x10, n)
    struct.pack_into("<I", header, 0x14, blk_addr)
    struct.pack_into("<I", header, 0x18, blk_data)
    struct.pack_into("<I", header, 0x1C, len(block))
    table = struct.pack("<II", 0, len(block))
    return bytes(header) + table + bytes(block)


def test_raw_mode_roundtrip():
    samples = [[100, -200, 32767, -32768, 0, 55, -55, 12345]]
    data = make_ncw(1, 16, 44100, samples, bits=0)
    hdr, chans = ncw.decode(data)
    assert hdr["channels"] == 1 and hdr["num_samples"] == 8
    assert chans[0] == samples[0]


def test_delta_mode_roundtrip():
    # a ramp: delta encoding shines here
    samples = [[i * 7 - 1000 for i in range(300)]]
    data = make_ncw(1, 16, 48000, samples, bits=14)
    hdr, chans = ncw.decode(data)
    assert chans[0] == samples[0]


def test_truncation_mode_roundtrip():
    # small-amplitude samples fit in few bits; truncation stores them directly
    samples = [[(-1) ** i * (i % 60) for i in range(200)]]
    data = make_ncw(1, 16, 44100, samples, bits=-8)
    hdr, chans = ncw.decode(data)
    assert chans[0] == samples[0]


def test_stereo_and_sample_count():
    left = [i - 50 for i in range(120)]
    right = [100 - i for i in range(120)]
    data = make_ncw(2, 16, 44100, [left, right], bits=12)
    hdr, chans = ncw.decode(data)
    assert hdr["channels"] == 2
    assert chans[0] == left and chans[1] == right


def test_mid_side_reconstruction():
    # decoder does L = mid + side, R = mid - side; encode mid/side of known L,R
    left = [10, 20, 30, 40, 50, 60]
    right = [10, 18, 34, 40, 46, 60]                 # same parity as left
    mid = [(l + r) // 2 for l, r in zip(left, right)]
    side = [(l - r) // 2 for l, r in zip(left, right)]
    data = make_ncw(2, 16, 44100, [mid, side], bits=8, flags=ncw._FLAG_MID_SIDE)
    hdr, chans = ncw.decode(data)
    assert chans[0] == left and chans[1] == right


def test_to_wav_shape():
    samples = [[1000, -1000, 500, -500, 0, 32000]]
    data = make_ncw(1, 16, 44100, samples, bits=0)
    hdr, chans = ncw.decode(data)
    wav = ncw.to_wav(hdr, chans)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    tag, ch, rate, _br, _ba, bits = struct.unpack_from("<HHIIHH", wav, 20)
    assert tag == 1 and ch == 1 and rate == 44100 and bits == 16
    # the data chunk holds exactly num_samples * channels * 2 bytes
    di = wav.find(b"data")
    dsize = struct.unpack_from("<I", wav, di + 4)[0]
    assert dsize == 6 * 1 * 2


def test_bad_magic_raises():
    with pytest.raises(ncw.NcwError):
        ncw.decode(b"NOTNCW" + b"\x00" * 200)


def test_truncated_block_raises():
    samples = [[1, 2, 3, 4]]
    data = make_ncw(1, 16, 44100, samples, bits=0)
    with pytest.raises(ncw.NcwError):
        ncw.decode(data[:0x80])                      # header ok, blocks cut off


def test_unpack_signed_is_invertible():
    vals = [(-1) ** i * (i % 31) for i in range(512)]
    packed = _pack_signed(vals, 6)
    assert ncw._unpack_signed(packed, 512, 6) == vals


# ── batch / directory conversion (commands.convert) ────────────────

class _CArgs:
    def __init__(self, **kw):
        d = {"input": None, "output": None, "division": 480,
             "skip_existing": False, "quiet": True, "to_pcm": False, "codec": None}
        d.update(kw)
        for k, v in d.items():
            setattr(self, k, v)


def test_convert_single_ncw(tmp_path):
    from acidcat.commands import convert
    data = make_ncw(1, 16, 44100, [[100, -100, 200, -200, 0]], bits=0)
    p = tmp_path / "s.ncw"
    p.write_bytes(data)
    out = str(tmp_path / "s.wav")
    assert convert.run(_CArgs(input=str(p), output=out)) == 0
    assert open(out, "rb").read()[:4] == b"RIFF"


def test_convert_batch_directory_recursive(tmp_path):
    from acidcat.commands import convert
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "deep").mkdir()
    good = make_ncw(2, 16, 48000, [[i for i in range(20)], [-i for i in range(20)]], bits=8)
    (tmp_path / "a" / "one.ncw").write_bytes(good)
    (tmp_path / "a" / "deep" / "two.ncw").write_bytes(good)
    (tmp_path / "a" / "not_ncw.wav").write_bytes(b"RIFF....WAVE")  # ignored
    rc = convert.run(_CArgs(input=str(tmp_path)))
    assert rc == 0
    assert (tmp_path / "a" / "one.wav").exists()
    assert (tmp_path / "a" / "deep" / "two.wav").exists()


def test_convert_batch_skip_existing(tmp_path):
    from acidcat.commands import convert
    data = make_ncw(1, 16, 44100, [[1, 2, 3]], bits=0)
    (tmp_path / "x.ncw").write_bytes(data)
    convert.run(_CArgs(input=str(tmp_path)))            # first pass writes x.wav
    mtime = (tmp_path / "x.wav").stat().st_mtime_ns
    convert.run(_CArgs(input=str(tmp_path), skip_existing=True))
    assert (tmp_path / "x.wav").stat().st_mtime_ns == mtime   # not rewritten


def test_convert_batch_bad_ncw_counted_not_fatal(tmp_path):
    from acidcat.commands import convert
    (tmp_path / "ok.ncw").write_bytes(make_ncw(1, 16, 44100, [[5, 6, 7]], bits=0))
    (tmp_path / "bad.ncw").write_bytes(b"NOTNCW" + b"\x00" * 200)   # unparseable
    rc = convert.run(_CArgs(input=str(tmp_path)))
    assert rc == 0                                       # one good file -> success
    assert (tmp_path / "ok.wav").exists()
    assert not (tmp_path / "bad.wav").exists()
