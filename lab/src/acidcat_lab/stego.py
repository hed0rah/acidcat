"""Hide a payload in the low bits of PCM WAV samples.

Two independent choices. WHITENING (the `raw` flag) decides what the bits say;
METHOD (`replace`/`match`/`adaptive`) decides which bits carry them and how.

Whitening, and which setting is detectable is the opposite of what you would
expect. `embed` whitens the payload against a keystream first, so the low bit
plane comes out uniform; `raw=True` writes the plaintext bits straight in.

MEASURED, because the intuition is backwards. A real 16-bit recording already
has near-random LSBs -- that is what the bottom bit of a microphone signal is.
So a whitened payload lands in noise and looks like noise, while a raw one is
LESS random than the carrier and is the one that shifts the statistic:

    carrier            LSB entropy mean 1.000
    whitened embed     1.000   -- indistinguishable
    raw embed          0.996   -- measurably lower

Method decides placement, and only one of the three buys real concealment here:

    replace   overwrite the low bit of each sample. Any depth. The baseline.
    match     move the whole sample +/-1 to set the low bit (16-bit). The
              textbook counter to the value-histogram attacks (chi-square,
              sample-pair) -- which are already degenerate on real 16-bit audio,
              so against the entropy detector this repo actually uses it is no
              better than replace. Documented at its definition below.
    adaptive  write only where the carrier's low bits are already noisy
              (16-bit). This one genuinely evades the windowed-entropy detector:
              the profile does not change, so no new window lights up. It needs
              a carrier with noisy regions and carries less. See below.

acidcat's `lsb_entropy` notice reports "uniformly high LSB entropy", which
fires on six of six clean real WAVs before anything is hidden in them. It is
honestly worded and it is not evidence. See tests/test_lab_loop.py and
tests/test_lab_stego_methods.py, which pin what is and is not detectable here
rather than implying more.

Not a secure tool: the key seeds Python's PRNG.
"""


import array
import os
import struct
import random

_MAGIC = b"ACST"  # 4-byte marker so extract can tell "no payload" from garbage
_BLOCK = 256      # adaptive: samples per block whose noisiness is judged together


def _keystream(key, n):
    r = random.Random(key)
    return bytes(r.getrandbits(8) for _ in range(n))


def _data_region(wav):
    """(offset, length, sample_width) of the PCM data chunk (any linear-PCM depth)."""
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    bits = None
    pos = 12
    n = len(wav)
    while pos + 8 <= n:
        cid = wav[pos:pos + 4]
        size = struct.unpack_from("<I", wav, pos + 4)[0]
        body = pos + 8
        if cid == b"fmt " and size >= 16:
            bits = struct.unpack_from("<H", wav, body + 14)[0]
        elif cid == b"data":
            # the LSB lives in the low byte of each little-endian sample at
            # offset i*width, so the embed/extract are width-agnostic for any
            # linear-PCM depth. only the sample width has to be known.
            if bits not in (8, 16, 24, 32):
                raise ValueError(f"unsupported bits_per_sample {bits}")
            return body, min(size, n - body), bits // 8
        pos = body + size + (size & 1)
    raise ValueError("no data chunk")


def capacity(wav):
    """How many payload bytes fit (one bit per sample, minus the header)."""
    _, length, width = _data_region(wav)
    samples = length // width
    header_bits = (len(_MAGIC) + 4) * 8
    return max(0, (samples - header_bits) // 8)


def _bits(data):
    for byte in data:
        for i in range(7, -1, -1):
            yield (byte >> i) & 1


def embed(wav, payload, key=1337, raw=False, method="replace"):
    """Hide `payload` in the sample LSBs. method: "replace" (overwrite the low
    bit, any depth), "match" (+/-1 to set it, 16-bit, defeats value-histogram
    attacks), or "adaptive" (only in already-noisy blocks, 16-bit, keeps the LSB
    entropy profile unchanged)."""
    if method not in ("replace", "match", "adaptive"):
        raise ValueError(f"unknown method {method!r}")
    off, length, width = _data_region(wav)
    blob = _MAGIC + struct.pack("<I", len(payload)) + payload
    if not raw:
        ks = _keystream(key, len(blob))
        blob = bytes(b ^ k for b, k in zip(blob, ks))
    if method == "adaptive":
        return _embed_adaptive(wav, blob, off, length)
    need = len(blob) * 8
    samples = length // width
    if need > samples:
        raise ValueError(f"payload needs {need} samples, carrier holds {samples}")
    if method == "match":
        if width != 2:
            raise ValueError("match stego is 16-bit only")
        return _embed_match(wav, blob, off, length)
    out = bytearray(wav)
    for i, bit in enumerate(_bits(blob)):
        p = off + i * width           # low byte of the i-th LE sample
        out[p] = (out[p] & 0xFE) | bit
    return bytes(out)


def _read_bits(wav, off, width, nbits):
    val = bytearray((nbits + 7) // 8)
    for i in range(nbits):
        bit = wav[off + i * width] & 1
        val[i // 8] |= bit << (7 - (i % 8))
    return bytes(val)


def extract(wav, key=1337, raw=False, method="replace"):
    """Recover a payload. "replace" and "match" read the sequential LSBs
    identically; "adaptive" recomputes the noisy-block selection first."""
    if method not in ("replace", "match", "adaptive"):
        raise ValueError(f"unknown method {method!r}")
    if method == "adaptive":
        return _extract_adaptive(wav, key, raw)
    off, length, width = _data_region(wav)
    head_len = len(_MAGIC) + 4
    head = _read_bits(wav, off, width, head_len * 8)
    if not raw:
        head = bytes(b ^ k for b, k in zip(head, _keystream(key, head_len)))
    if head[:4] != _MAGIC:
        raise ValueError("no acidcat-stego payload found (wrong key or none embedded)")
    plen = struct.unpack_from("<I", head, 4)[0]
    total = head_len + plen
    blob = _read_bits(wav, off, width, total * 8)
    if not raw:
        blob = bytes(b ^ k for b, k in zip(blob, _keystream(key, total)))
    return blob[head_len:head_len + plen]


# ── method: match (LSB matching, +/-1) ──────────────────────────────
#
# Replacement OVERWRITES the low bit, which drives the value pairs (2k, 2k+1)
# toward equal counts -- the signature the chi-square and sample-pair attacks
# read. Matching instead ADDS OR SUBTRACTS 1 to make the low bit land where it
# should, so the value keeps moving the way the signal does and the pair
# histogram is not flattened. It is the standard counter to those attacks.
#
# A caveat this tool states rather than hides: those attacks are a property of a
# dense, smooth value histogram, which 8-bit images have and real 16-bit audio
# does not. Measured on this repo's corpus, sample-pair analysis is degenerate
# on real audio whether the embed is replacement or matching. So against the
# value-histogram attacks matching is the textbook defence, and against the only
# LSB test that works on audio here (windowed LSB entropy) it is no better and
# no worse than replacement: the low bit comes out just as random either way.
# Matching is 16-bit only; the +/-1 is on the whole sample, not a byte.


def _samples16(wav, off, length):
    a = array.array("h")
    a.frombytes(bytes(wav[off:off + (length // 2) * 2]))
    import sys as _sys
    if _sys.byteorder == "big":
        a.byteswap()
    return a


def _embed_match(wav, blob, off, length):
    a = _samples16(wav, off, length)
    rng = random.Random(0xACE)
    for i, bit in enumerate(_bits(blob)):
        v = a[i]
        if (v & 1) != bit:
            if v <= -32768:
                v += 1
            elif v >= 32767:
                v -= 1
            else:
                v += 1 if rng.getrandbits(1) else -1
            a[i] = v
    import sys as _sys
    if _sys.byteorder == "big":
        a.byteswap()
    out = bytearray(wav)
    out[off:off + len(a) * 2] = a.tobytes()
    return bytes(out)


# ── method: adaptive (hide only where the LSBs are already noisy) ────
#
# The one attack that works on audio here is windowed LSB entropy: a payload
# written into a quiet stretch turns that stretch's low bits random and the
# window lights up. Adaptive embedding answers it directly -- it only writes
# where the carrier's low bits are ALREADY noisy, so the entropy profile does
# not change and there is no new window to see. Verified on this repo: a naive
# fill lit 15 extra windows; the adaptive fill left the profile identical to the
# clean carrier.
#
# The noisy blocks are chosen from bits the embed does not touch (each sample
# shifted right by one, which drops the low bit), so the receiver recomputes the
# exact same selection. 16-bit only.


def _block_variance(a):
    """Population variance of (sample >> 1) per _BLOCK-sample block. Uses the
    high bits only, so the choice of blocks is identical before and after an LSB
    write."""
    out = []
    for start in range(0, len(a) - _BLOCK + 1, _BLOCK):
        hi = [a[j] >> 1 for j in range(start, start + _BLOCK)]
        m = sum(hi) / _BLOCK
        out.append((start, sum((x - m) ** 2 for x in hi) / _BLOCK))
    return out


def _adaptive_indices(a):
    """Sample indices to carry the payload: every sample of the blocks whose
    LSB-independent variance is at or above the median. Deterministic, so embed
    and extract agree."""
    bv = _block_variance(a)
    if not bv:
        return []
    med = sorted(v for _s, v in bv)[len(bv) // 2]
    idx = []
    for start, v in bv:
        if v >= med and med > 0:
            idx.extend(range(start, start + _BLOCK))
    return idx


def adaptive_capacity(wav):
    """Payload bytes an adaptive embed can carry in this carrier (the noisy
    blocks only, minus the header)."""
    off, length, width = _data_region(wav)
    if width != 2:
        raise ValueError("adaptive stego is 16-bit only")
    idx = _adaptive_indices(_samples16(wav, off, length))
    header_bits = (len(_MAGIC) + 4) * 8
    return max(0, (len(idx) - header_bits) // 8)


def _embed_adaptive(wav, blob, off, length):
    a = _samples16(wav, off, length)
    idx = _adaptive_indices(a)
    need = len(blob) * 8
    if need > len(idx):
        raise ValueError(f"payload needs {need} noisy samples, carrier offers {len(idx)}")
    for bit, i in zip(_bits(blob), idx):
        a[i] = (a[i] & ~1) | bit
    import sys as _sys
    if _sys.byteorder == "big":
        a.byteswap()
    out = bytearray(wav)
    out[off:off + len(a) * 2] = a.tobytes()
    return bytes(out)


def _extract_adaptive(wav, key, raw):
    off, length, width = _data_region(wav)
    if width != 2:
        raise ValueError("adaptive stego is 16-bit only")
    a = _samples16(wav, off, length)
    idx = _adaptive_indices(a)
    head_len = len(_MAGIC) + 4

    def read(n):
        val = bytearray((n + 7) // 8)
        for i in range(n):
            val[i // 8] |= (a[idx[i]] & 1) << (7 - (i % 8))
        return bytes(val)

    head = read(head_len * 8)
    if not raw:
        head = bytes(b ^ k for b, k in zip(head, _keystream(key, head_len)))
    if head[:4] != _MAGIC:
        raise ValueError("no acidcat-stego payload found (wrong key or none embedded)")
    plen = struct.unpack_from("<I", head, 4)[0]
    total = head_len + plen
    blob = read(total * 8)
    if not raw:
        blob = bytes(b ^ k for b, k in zip(blob, _keystream(key, total)))
    return blob[head_len:head_len + plen]
