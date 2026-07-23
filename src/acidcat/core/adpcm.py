"""ADPCM -> 16-bit PCM decoding for the common "won't play" WAV codecs.

Games and old software ship WAVs in ADPCM (4-bit, ~4x smaller than PCM) that many
players won't decode -- and sometimes mistag them (the Doom 64 DC port tags IMA
ADPCM as G.726). This gives acidcat a native IMA/DVI ADPCM decoder so
`convert --to-pcm` can render those to plain, universally playable 16-bit PCM.

    IMA / DVI ADPCM   (WAVE format 0x0011)   block-structured
    + a `continuous` variant for the mistagged / block-less streams

Output is signed 16-bit little-endian, interleaved for stereo. Microsoft ADPCM
(0x0002) is a planned follow-up (its coefficient/priming decode wants its own
verification pass).
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
