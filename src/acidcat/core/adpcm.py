"""ADPCM -> 16-bit PCM decoding for the common "won't play" WAV codecs.

Games and old software ship WAVs in ADPCM (4-bit, ~4x smaller than PCM) that many
players won't decode -- and sometimes mistag them (the Doom 64 DC port tags IMA
ADPCM as G.726). This gives acidcat a native IMA/DVI ADPCM decoder so
`convert --to-pcm` can render those to plain, universally playable 16-bit PCM.

    IMA / DVI ADPCM   (WAVE format 0x0011)   block-structured
    + a `continuous` variant for the mistagged / block-less streams
    Microsoft ADPCM  (WAVE format 0x0002)   block-structured, coefficient-predicted

Output is signed 16-bit little-endian, interleaved for stereo.
"""

import struct

_IMA_STEP = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767]
_IMA_INDEX = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]


def _clip16(v):
    return -32768 if v < -32768 else 32767 if v > 32767 else v


def _ima_step(nib, pred, idx):
    step = _IMA_STEP[idx]
    diff = step >> 3
    if nib & 1:
        diff += step >> 2
    if nib & 2:
        diff += step >> 1
    if nib & 4:
        diff += step
    if nib & 8:
        diff = -diff
    pred = _clip16(pred + diff)
    idx += _IMA_INDEX[nib]
    return pred, (0 if idx < 0 else 88 if idx > 88 else idx)


def decode_ima_continuous(data):
    """IMA nibbles with no block structure (predictor starts at 0) -- the
    mistagged / raw-stream case (mono, low nibble first). -> 16-bit LE bytes."""
    out = bytearray()
    pred = idx = 0
    for byte in data:
        for nib in (byte & 0x0F, byte >> 4):
            pred, idx = _ima_step(nib, pred, idx)
            out += struct.pack("<h", pred)
    return bytes(out)


def _ima_mono(data, block_align):
    out = bytearray()
    step = block_align if block_align and block_align >= 5 else len(data)
    for b0 in range(0, len(data), step):
        block = data[b0:b0 + step]
        if len(block) < 4:
            break
        pred = struct.unpack_from("<h", block, 0)[0]
        idx = min(88, block[2])
        out += struct.pack("<h", pred)                    # the priming sample
        for byte in block[4:]:
            for nib in (byte & 0x0F, byte >> 4):
                pred, idx = _ima_step(nib, pred, idx)
                out += struct.pack("<h", pred)
    return bytes(out)


def _ima_stereo(data, block_align):
    out = bytearray()
    for b0 in range(0, len(data), block_align):
        block = data[b0:b0 + block_align]
        if len(block) < 8:
            break
        pred = [struct.unpack_from("<h", block, 0)[0],
                struct.unpack_from("<h", block, 4)[0]]
        idx = [min(88, block[2]), min(88, block[6])]
        samples = [[pred[0]], [pred[1]]]
        pos = 8
        while pos + 8 <= len(block):
            for c in (0, 1):                              # interleaved 4-byte words
                for byte in block[pos + c * 4:pos + c * 4 + 4]:
                    for nib in (byte & 0x0F, byte >> 4):
                        pred[c], idx[c] = _ima_step(nib, pred[c], idx[c])
                        samples[c].append(pred[c])
            pos += 8
        for i in range(min(len(samples[0]), len(samples[1]))):
            out += struct.pack("<hh", samples[0][i], samples[1][i])
    return bytes(out)


def decode_ima(data, block_align, channels):
    """Block-structured IMA/DVI ADPCM (WAVE 0x0011) -> 16-bit LE PCM bytes."""
    return _ima_stereo(data, block_align) if channels == 2 else _ima_mono(data, block_align)


# ── Microsoft ADPCM (WAVE 0x0002) ──────────────────────────────────────────
# A linear predictor over the two previous samples (coefficient pair chosen per
# block) plus a per-nibble adaptive delta. The seven standard coefficient pairs
# are the default; a file may carry its own set in the fmt chunk.
_MS_ADAPT = [230, 230, 230, 230, 307, 409, 512, 614,
             768, 614, 512, 409, 307, 230, 230, 230]
_MS_COEF = [(256, 0), (512, -256), (0, 0), (192, 64),
            (240, 0), (460, -208), (392, -232)]


def _t256(x):
    """Divide by 256 truncating toward zero (matches the reference C decoders,
    where integer division truncates rather than floors)."""
    return x // 256 if x >= 0 else -(-x // 256)


def _ms_nibble(nib, s1, s2, c1, c2, delta):
    pred = _t256(s1 * c1 + s2 * c2)
    pred += (nib - 16 if nib & 8 else nib) * delta         # sign-extend the nibble
    pred = _clip16(pred)
    delta = (_MS_ADAPT[nib] * delta) >> 8
    return pred, (16 if delta < 16 else delta)


def _ms_mono(data, block_align, coefs):
    out = bytearray()
    step = block_align if block_align and block_align >= 7 else len(data)
    for b0 in range(0, len(data), step):
        blk = data[b0:b0 + step]
        if len(blk) < 7:
            break
        p = blk[0] if blk[0] < len(coefs) else len(coefs) - 1
        c1, c2 = coefs[p]
        delta = struct.unpack_from("<h", blk, 1)[0]
        s1 = struct.unpack_from("<h", blk, 3)[0]           # more recent
        s2 = struct.unpack_from("<h", blk, 5)[0]           # older
        out += struct.pack("<hh", s2, s1)                  # emit older, then recent
        for byte in blk[7:]:
            for nib in (byte >> 4, byte & 0x0F):           # high nibble first
                pred, delta = _ms_nibble(nib, s1, s2, c1, c2, delta)
                s2, s1 = s1, pred
                out += struct.pack("<h", pred)
    return bytes(out)


def _ms_stereo(data, block_align, coefs):
    out = bytearray()
    for b0 in range(0, len(data), block_align):
        blk = data[b0:b0 + block_align]
        if len(blk) < 14:
            break
        p = [blk[0] if blk[0] < len(coefs) else len(coefs) - 1,
             blk[1] if blk[1] < len(coefs) else len(coefs) - 1]
        c = [coefs[p[0]], coefs[p[1]]]
        delta = [struct.unpack_from("<h", blk, 2)[0], struct.unpack_from("<h", blk, 4)[0]]
        s1 = [struct.unpack_from("<h", blk, 6)[0], struct.unpack_from("<h", blk, 8)[0]]
        s2 = [struct.unpack_from("<h", blk, 10)[0], struct.unpack_from("<h", blk, 12)[0]]
        out += struct.pack("<hh", s2[0], s2[1])            # older frame first
        out += struct.pack("<hh", s1[0], s1[1])            # then the recent frame
        for byte in blk[14:]:                              # one L nibble + one R nibble
            pl, delta[0] = _ms_nibble(byte >> 4, s1[0], s2[0], c[0][0], c[0][1], delta[0])
            s2[0], s1[0] = s1[0], pl
            pr, delta[1] = _ms_nibble(byte & 0x0F, s1[1], s2[1], c[1][0], c[1][1], delta[1])
            s2[1], s1[1] = s1[1], pr
            out += struct.pack("<hh", pl, pr)
    return bytes(out)


def decode_ms_adpcm(data, block_align, channels, coefs=None):
    """Block-structured Microsoft ADPCM (WAVE 0x0002) -> 16-bit LE PCM bytes.
    `coefs` is the fmt chunk's coefficient table (list of (c1, c2)); the seven
    standard pairs are used if omitted."""
    coefs = coefs or _MS_COEF
    return (_ms_stereo(data, block_align, coefs) if channels == 2
            else _ms_mono(data, block_align, coefs))
