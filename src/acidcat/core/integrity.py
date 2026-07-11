"""Integrity checks: does what the container claims match what the audio is.

The header declares properties (bit depth, sample format); the samples are the
ground truth. Where the two can be compared cheaply and soundly, a mismatch is a
real forensic signal. The anchor check is **effective bit depth**: a file can
declare 24-bit while every sample's low byte is zero, meaning it was upsampled
from 16-bit ("fake hi-res"). The witness is the PCM itself -- the lowest set bit
across all samples tells how many low bits actually carry data.

Only integer PCM is analyzed (float and compressed codecs have no fixed integer
resolution). Pure silence is skipped (an all-zero region has no bit depth to
read). Reads are capped, so a huge file is sampled, not slurped.
"""

import struct

_SCAN_CAP = 8 * 1024 * 1024        # bytes of PCM to sample for the bit-depth read


def _trailing_zero_bits(n):
    if n == 0:
        return None                # all-silent: undetermined
    bits = 0
    while not (n & 1):
        n >>= 1
        bits += 1
    return bits


def _find(chunks, cid):
    for c in chunks:
        if str(c.get("id", "")).strip() == cid:
            return c
    return None


def _wav_fmt(data, fmt_chunk):
    """(effective_format_tag, channels, bits). For WAVE_FORMAT_EXTENSIBLE
    (0xFFFE) the real tag is the first 2 bytes of the sub-format GUID."""
    off = fmt_chunk["offset"] + 8
    tag, ch, _rate, _avg, _align, bits = struct.unpack_from("<HHIIHH", data, off)
    if tag == 0xFFFE and fmt_chunk["size"] >= 40:
        tag = struct.unpack_from("<H", data, off + 24)[0]   # sub-format tag
    return tag, ch, bits


def _effective_bits(data, start, size, bytes_per_sample):
    """OR every sample in the (capped) PCM span and read the effective bit depth
    from the lowest set bit. Returns (effective_bits, examined) or (None, 0)."""
    end = start + min(size, _SCAN_CAP)
    end -= (end - start) % bytes_per_sample
    acc = 0
    examined = 0
    for p in range(start, end, bytes_per_sample):
        acc |= int.from_bytes(data[p:p + bytes_per_sample], "little", signed=False)
        examined += 1
    tz = _trailing_zero_bits(acc)
    if tz is None:
        return None, examined
    return bytes_per_sample * 8 - tz, examined


def analyze(label, chunks, data):
    """Return a list of ``{check, verdict, detail}`` integrity findings. Read-only.
    Currently WAV/RF64 integer PCM (the bulk of samples)."""
    if label not in ("RIFF/WAVE", "RF64/WAVE"):
        return []
    fmt = _find(chunks, "fmt")
    dat = _find(chunks, "data")
    if not fmt or not dat:
        return []
    try:
        tag, _ch, bits = _wav_fmt(data, fmt)
    except struct.error:
        return []
    # 1 = integer PCM; 0xFFFE (extensible) usually is too but its real tag is in
    # the extension -- keep it simple and only trust plain PCM here.
    if tag != 1 or bits not in (16, 24, 32):
        return []
    bps = bits // 8
    start = dat["offset"] + 8
    eff, examined = _effective_bits(data, start, dat["size"], bps)
    if eff is None or examined < 1024:         # too little / all-silent to judge
        return []
    out = []
    if eff <= bits - 8:
        out.append({
            "check": "bit_depth",
            "verdict": f"declared {bits}-bit, effective {eff}-bit",
            "detail": f"the low {bits - eff} bit(s) are always zero across "
                      f"{examined:,} samples -- likely upsampled from {eff}-bit "
                      f"(padded, not true {bits}-bit)",
        })
    return out
