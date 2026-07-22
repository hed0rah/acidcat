"""IFF 8SVX decode -> 16-bit PCM WAV.

An 8SVX BODY is 8-bit *signed* PCM, optionally Fibonacci-delta compressed
(VHDR.sCompression == 1): two samples per byte, each a 4-bit code that indexes
a fixed delta table added to a running accumulator. This module reconstructs
the samples and renders a standard 16-bit mono WAV so a 1988 Amiga voice plays
in any modern tool.

Decode, not access control -- the same class of work as decoding FLAC or the
Kontakt NCW path. The walker (core/walk/svx.py) *describes* the bytes; this
turns them into audio. Multi-octave voices store the high octave first followed
by successively-halved copies; we render the high octave (oneShot + repeat),
which is the sample the file is actually about.
"""

import io
import struct
import wave

MAGIC = b"FORM"

# the Fibonacci delta table from the IFF 8SVX spec (D1Unpack): a 4-bit code
# 0..15 picks a delta -- small steps in the middle, big jumps at the ends.
DELTA = (-34, -21, -13, -8, -5, -3, -2, -1, 0, 1, 2, 3, 5, 8, 13, 21)

_COMPRESSION = {0: "raw 8-bit PCM", 1: "Fibonacci-delta"}


class SvxError(Exception):
    """Raised when the bytes are not a decodable 8SVX voice."""


def is_8svx(data):
    return len(data) >= 12 and data[:4] == b"FORM" and data[8:12] == b"8SVX"


def _chunks(b):
    """Yield (chunk_id, payload) for each IFF chunk after the FORM header.
    Chunks are word-aligned: a pad byte follows any odd-sized payload."""
    pos = 12                                          # skip FORM + size + 8SVX
    n = len(b)
    while pos + 8 <= n:
        cid = b[pos:pos + 4]
        size = struct.unpack_from(">I", b, pos + 4)[0]
        payload = b[pos + 8:pos + 8 + size]
        yield cid, payload
        step = 8 + size + (size & 1)
        if step <= 8:                                 # zero-size guard: stop cleanly
            break
        pos += step


def _fib_decode(body):
    """Fibonacci-delta unpack. byte 0 is padding, byte 1 the signed seed, then
    each byte is two 4-bit codes (high nibble first). Returns signed samples,
    2 * (len(body) - 2) of them."""
    if len(body) < 2:
        return []
    x = body[1] - 256 if body[1] > 127 else body[1]   # seed as a signed byte
    out = []
    ap = out.append
    for byte in body[2:]:
        for nib in (byte >> 4, byte & 0x0F):
            x = ((x + DELTA[nib] + 128) & 0xFF) - 128  # accumulate, wrap 8-bit
            ap(x)
    return out


def _raw_decode(body):
    """An uncompressed BODY is already 8-bit signed PCM."""
    return [s - 256 if s > 127 else s for s in body]


def decode(data):
    """Parse an 8SVX and reconstruct its high-octave PCM.

    Returns (info, samples) where samples is a list of signed 8-bit ints and
    info carries rate / compression / octaves / duration. Raises SvxError on
    anything that is not a usable 8SVX voice."""
    if not is_8svx(data):
        raise SvxError("not an IFF FORM 8SVX file")

    vhdr = body = None
    for cid, payload in _chunks(data):
        if cid == b"VHDR" and vhdr is None:
            vhdr = payload
        elif cid == b"BODY" and body is None:
            body = payload
    if vhdr is None:
        raise SvxError("missing VHDR voice header")
    if body is None:
        raise SvxError("missing BODY sample data")
    if len(vhdr) < 16:
        raise SvxError(f"VHDR is {len(vhdr)} bytes, spec is 20")

    one = struct.unpack_from(">I", vhdr, 0)[0]        # oneShotHiSamples
    rep = struct.unpack_from(">I", vhdr, 4)[0]        # repeatHiSamples
    rate = struct.unpack_from(">H", vhdr, 12)[0]      # samplesPerSec
    octs = vhdr[14] or 1                              # ctOctave
    comp = vhdr[15]                                   # sCompression

    if comp not in _COMPRESSION:
        raise SvxError(f"unsupported sCompression {comp} "
                       f"(only raw and Fibonacci-delta are decodable)")

    samples = _fib_decode(body) if comp == 1 else _raw_decode(body)

    # the high octave is stored first; keep just oneShot+repeat when the header
    # gives a length (correct for both raw and fib, since the high octave is the
    # leading run of the decoded stream). fall back to the whole body otherwise.
    high = one + rep
    if 0 < high <= len(samples):
        samples = samples[:high]

    info = {
        "rate": rate or 0,
        "compression": comp,
        "compression_name": _COMPRESSION[comp],
        "octaves": octs,
        "one_shot": one,
        "repeat": rep,
        "num_samples": len(samples),
    }
    return info, samples


def to_wav(info, samples):
    """Render decoded 8-bit signed samples to a 16-bit mono WAV (scaled up by
    256 so it plays anywhere) and return the file bytes."""
    rate = info.get("rate") or 0
    if rate <= 0:
        rate = 8000                                   # unusable header rate; pick a sane default
        info["rate_defaulted"] = True
    frames = b"".join(struct.pack("<h", s * 256) for s in samples)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()
