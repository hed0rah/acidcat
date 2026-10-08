"""CRI ADX -- the cross-platform middleware ADPCM.

CRI Middleware's ADX is everywhere: Dreamcast, PS2, GameCube, Wii, arcade, PC --
Sega and countless others streamed music and voice through it for two decades.
The format: a header opening with 0x8000 and closing with a "(c)CRI" copyright
marker just before the audio, then interleaved per-channel frames. Each frame is
a 2-byte scale followed by 16 bytes of 4-bit nibbles (32 samples), and the
predictor's two coefficients are derived from the header's highpass cutoff and
the sample rate (not stored per frame like DSP-ADPCM).

Handles the standard linear-scale types (2 and 3). AHX (type 4, MPEG) and
encrypted ADX are out of scope and raise.

    from acidcat.core.codecs import adx
    pcm, info = adx.decode(open("bgm.adx","rb").read())
"""

import math
import struct

from acidcat.core.infra.source import open_input
from acidcat.core.primitives.pcm import clip16, interleave_stereo

_MAGIC = 0x8000


class AdxError(Exception):
    pass


def parse_header(data):
    """Parse an ADX header. Returns dict(channels, block_size, rate, samples,
    highpass, enc_type, version, data_offset). Raises AdxError if not ADX."""
    if len(data) < 0x14 or struct.unpack_from(">H", data, 0)[0] != _MAGIC:
        raise AdxError("not an ADX (missing 0x8000 magic)")
    copyright_off = struct.unpack_from(">H", data, 2)[0]
    enc, blk, bits, ch = data[4], data[5], data[6], data[7]
    rate, samples = struct.unpack_from(">II", data, 8)
    highpass = struct.unpack_from(">H", data, 0x10)[0]
    version = data[0x12]
    return {"channels": ch, "block_size": blk, "bits": bits, "rate": rate,
            "samples": samples, "highpass": highpass, "enc_type": enc,
            "version": version, "data_offset": copyright_off + 4,
            "copyright_off": copyright_off}


def is_adx(path):
    """True if `path` is a (non-encrypted) ADX, confirmed by the (c)CRI marker."""
    try:
        with open_input(path) as f:
            head = f.read(4)
            if head[:2] != b"\x80\x00":
                return False
            co = struct.unpack(">H", head[2:4])[0]
            f.seek(co - 2)
            return f.read(6) == b"(c)CRI"
    except (OSError, struct.error):
        return False


def _coefs(highpass, rate):
    """The two predictor coefficients, derived from the highpass cutoff."""
    sqrt2 = math.sqrt(2.0)
    a = sqrt2 - math.cos(2.0 * math.pi * highpass / rate)
    b = sqrt2 - 1.0
    c = (a - math.sqrt((a + b) * (a - b))) / b
    return int(c * 8192.0), int(-(c * c) * 4096.0)




def decode(data):
    """Decode an ADX to interleaved 16-bit PCM. Returns (pcm_bytes, info) with
    channels, rate, frames. Raises AdxError for AHX / encrypted / unsupported."""
    import array
    h = parse_header(data)
    if h["enc_type"] not in (2, 3):
        raise AdxError(f"unsupported ADX encoding type {h['enc_type']} "
                       "(AHX/encrypted not handled)")
    ch, blk, rate = h["channels"], h["block_size"], h["rate"]
    # header bytes nothing has validated yet. blk == 0 never advanced the loop
    # (an infinite hang at the CLI), ch == 0 indexed outs[0] out of nothing,
    # and rate == 0 divided by itself in _coefs.
    if ch not in (1, 2):
        raise AdxError(f"ADX channel count {ch} (1 or 2 supported)")
    if blk < 3:
        raise AdxError(f"ADX block size {blk} cannot hold a scale and nibbles")
    if rate == 0:
        raise AdxError("ADX sample rate is 0")
    coef1, coef2 = _coefs(h["highpass"], rate)
    spf = (blk - 2) * 2                               # samples per frame (usually 32)
    outs = [array.array("h") for _ in range(ch)]
    hist = [[0, 0] for _ in range(ch)]
    pos = h["data_offset"]
    total = h["samples"]
    end = len(data)
    while pos + blk * ch <= end and len(outs[0]) < total:
        for c in range(ch):
            scale = struct.unpack_from(">H", data, pos)[0]
            p1, p2 = hist[c]
            base = pos + 2
            oc = outs[c]
            for i in range(spf):
                byte = data[base + (i >> 1)]
                nib = (byte >> 4) if (i & 1) == 0 else (byte & 0x0F)
                s = nib - 16 if nib >= 8 else nib
                samp = clip16(s * scale + ((coef1 * p1 + coef2 * p2) >> 12))
                p2, p1 = p1, samp
                oc.append(samp)
            hist[c] = [p1, p2]
            pos += blk
    n = min((len(o) for o in outs), default=0)
    n = min(n, total) if total else n
    if ch == 1:
        pcm = outs[0][:n].tobytes()
    else:
        pcm = interleave_stereo(outs[0][:n], outs[1][:n])
    return pcm, {"channels": ch, "rate": rate, "frames": n}
