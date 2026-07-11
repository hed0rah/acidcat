"""NI Compressed Wave (.ncw) reader and lossless decoder.

Kontakt stores samples as NCW, Native Instruments' lossless codec: DPCM (delta)
plus bit-truncation, optional mid/side, over fixed 512-sample blocks. The header
carries the audio parameters (like a WAV fmt / FLAC STREAMINFO); ``decode``
reconstructs the PCM.

NCW is compression, not access control -- there is no key and nothing to bypass;
this is codec work, the same class as decoding FLAC. The block algorithm here
follows the public reverse-engineering (git-moss/ConvertWithMoss NcwFile.java,
monomadic/ncw) and is byte-verified against real Kontakt library samples.
"""

import struct

MAGIC = b"\x01\xa8\x9e\xd6"          # 0xD69EA801 LE
_BLOCK_MAGIC = 0x3E9A0C16
_SAMPLES_PER_BLOCK = 512
_FLAG_MID_SIDE = 0x01
_FLAG_IEEE_FLOAT = 0x02


def parse_header(data):
    """Return {channels, bits, sample_rate, num_samples} or None if the bytes
    are not a plausible NCW header (validated so a coincidental magic is not
    trusted)."""
    if len(data) < 0x14 or data[:4] != MAGIC:
        return None
    channels = struct.unpack_from("<H", data, 0x08)[0]
    bits = struct.unpack_from("<H", data, 0x0A)[0]
    rate = struct.unpack_from("<I", data, 0x0C)[0]
    num_samples = struct.unpack_from("<I", data, 0x10)[0]
    if not (1 <= channels <= 32) or bits not in (8, 16, 24, 32) \
            or not (8000 <= rate <= 384000) \
            or not (1 <= num_samples <= 2_000_000_000):  # ~11 h at 48 kHz; rejects the 0xffffffff sentinel
        return None
    return {"channels": channels, "bits": bits, "sample_rate": rate,
            "num_samples": num_samples}


class NcwError(ValueError):
    """The bytes are not a decodable NCW stream."""


def _unpack_signed(data, count, precision):
    """Unpack `count` signed `precision`-bit values from `data`, LSB-first
    (the bit order both NCW modes use). Returns a list of Python ints."""
    out = []
    acc = 0
    nbits = 0
    pos = 0
    mask = (1 << precision) - 1
    half = 1 << (precision - 1)
    for _ in range(count):
        while nbits < precision:
            acc |= data[pos] << nbits
            pos += 1
            nbits += 8
        v = acc & mask
        acc >>= precision
        nbits -= precision
        out.append(v - (1 << precision) if v & half else v)   # sign-extend
    return out


def decode(data):
    """Decode an NCW stream to (header, channel_samples) where channel_samples
    is a list of `channels` lists, each `num_samples` signed ints (or, for an
    IEEE-float stream, the raw 32-bit int bit patterns). Raises NcwError on
    malformed input -- a caller should degrade to a warning, never crash."""
    hdr = parse_header(data)
    if hdr is None:
        raise NcwError("not a valid NCW header")
    channels = hdr["channels"]
    bps = hdr["bits"]
    total = hdr["num_samples"]
    blk_addr = struct.unpack_from("<I", data, 0x14)[0]
    blk_data = struct.unpack_from("<I", data, 0x18)[0]
    if not (0x14 <= blk_addr <= blk_data <= len(data)):
        raise NcwError("block table / data offsets outside the file")
    n_offsets = (blk_data - blk_addr) // 4
    offsets = [struct.unpack_from("<I", data, blk_addr + i * 4)[0]
               for i in range(n_offsets)]
    n_blocks = -(-total // _SAMPLES_PER_BLOCK)          # ceil
    if n_blocks > len(offsets):
        raise NcwError(f"header declares {total} samples ({n_blocks} blocks) but "
                       f"the block table holds {len(offsets)}")

    chans = [[] for _ in range(channels)]
    for b in range(n_blocks):
        pos = blk_data + offsets[b]
        mid_side = False
        is_float = False
        block_chans = []
        for ch in range(channels):
            if pos + 16 > len(data):
                raise NcwError(f"block {b} channel {ch} header runs past EOF")
            magic, base, bits, flags = struct.unpack_from("<iihH", data, pos)
            if magic != _BLOCK_MAGIC:
                raise NcwError(f"block {b} channel {ch}: bad block magic "
                               f"0x{magic & 0xffffffff:08x}")
            pos += 16
            if ch == 0:
                mid_side = bool(flags & _FLAG_MID_SIDE)
                is_float = bool(flags & _FLAG_IEEE_FLOAT)
            precision = abs(bits)
            if bits == 0:                               # raw, uncompressed
                bpsamp = bps // 8
                nbytes = _SAMPLES_PER_BLOCK * bpsamp
                if pos + nbytes > len(data):
                    raise NcwError(f"block {b} raw data runs past EOF")
                samples = []
                for i in range(_SAMPLES_PER_BLOCK):
                    off = pos + i * bpsamp
                    v = int.from_bytes(data[off:off + bpsamp], "little", signed=True)
                    samples.append(v)
                pos += nbytes
            else:
                nbytes = _SAMPLES_PER_BLOCK * precision // 8
                if pos + nbytes > len(data):
                    raise NcwError(f"block {b} packed data runs past EOF")
                vals = _unpack_signed(data[pos:pos + nbytes],
                                      _SAMPLES_PER_BLOCK, precision)
                pos += nbytes
                if bits > 0:                            # delta / DPCM
                    samples = []
                    acc = base
                    for d in vals:
                        samples.append(acc)
                        acc += d
                else:                                   # truncation
                    samples = vals
            block_chans.append(samples)

        if mid_side and channels == 2:                  # L=mid+side, R=mid-side
            mid, side = block_chans[0], block_chans[1]
            block_chans = [[m + s for m, s in zip(mid, side)],
                           [m - s for m, s in zip(mid, side)]]
        for ci in range(channels):
            chans[ci].extend(block_chans[ci])

    for ci in range(channels):                          # trim block padding
        del chans[ci][total:]
    hdr = dict(hdr, is_float=is_float if channels else False)
    return hdr, chans


def to_wav(hdr, chans):
    """Pack decoded channel samples into a canonical PCM (or IEEE-float) WAV."""
    channels = hdr["channels"]
    bps = hdr["bits"]
    rate = hdr["sample_rate"]
    is_float = hdr.get("is_float")
    n = min(len(c) for c in chans) if chans else 0
    bpsamp = bps // 8
    fmt_tag = 3 if is_float else 1                      # 3 = IEEE float

    body = bytearray()
    if is_float:                                        # 32-bit float
        for i in range(n):
            for ci in range(channels):
                body += struct.pack("<f", struct.unpack("<f",
                                    struct.pack("<i", chans[ci][i] & 0xFFFFFFFF))[0])
    else:
        mask = (1 << bps) - 1
        half = 1 << bps
        for i in range(n):
            for ci in range(channels):
                v = chans[ci][i] & mask                 # two's complement in-range
                body += v.to_bytes(bpsamp, "little")

    byte_rate = rate * channels * bpsamp
    block_align = channels * bpsamp
    fmt = struct.pack("<HHIIHH", fmt_tag, channels, rate, byte_rate,
                      block_align, bps)
    data_chunk = b"data" + struct.pack("<I", len(body)) + bytes(body)
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    riff_body = b"WAVE" + fmt_chunk + data_chunk
    return b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body
