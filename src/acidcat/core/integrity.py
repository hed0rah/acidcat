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

from acidcat.core import mp4 as mp4mod

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


def _effective_bits(data, start, size, bytes_per_sample, byteorder):
    """OR every sample in the (capped) PCM span and read the effective bit depth
    from the lowest data-carrying bit. ``byteorder`` is "little" (WAV) or "big"
    (AIFF), so the low byte is masked wherever it actually sits. Returns
    (effective_bits, examined) or (None, examined)."""
    end = start + min(size, _SCAN_CAP)
    end -= (end - start) % bytes_per_sample
    acc = 0
    examined = 0
    for p in range(start, end, bytes_per_sample):
        acc |= int.from_bytes(data[p:p + bytes_per_sample], byteorder, signed=False)
        examined += 1
    tz = _trailing_zero_bits(acc)
    if tz is None:
        return None, examined
    return bytes_per_sample * 8 - tz, examined


def _bit_depth_finding(bits, eff, examined):
    if eff is None or examined < 1024 or eff > bits - 8:
        return None
    return {
        "check": "bit_depth",
        "verdict": f"declared {bits}-bit, effective {eff}-bit",
        "detail": f"the low {bits - eff} bit(s) are always zero across "
                  f"{examined:,} samples -- likely upsampled from {eff}-bit "
                  f"(padded, not true {bits}-bit)",
    }


def _wav_pcm(data, chunks):
    """(bits, byteorder, pcm_start, pcm_size) for a WAV/RF64 integer-PCM file, or
    None if not applicable."""
    fmt = _find(chunks, "fmt")
    dat = _find(chunks, "data")
    if not fmt or not dat:
        return None
    try:
        tag, _ch, bits = _wav_fmt(data, fmt)
    except struct.error:
        return None
    if tag != 1 or bits not in (16, 24, 32):
        return None
    return bits, "little", dat["offset"] + 8, dat["size"]


def _aiff_pcm(data, chunks):
    """(bits, byteorder, pcm_start, pcm_size) for an AIFF integer-PCM file, or
    None. AIFF is big-endian signed PCM; SSND payload leads with offset(4) +
    blockSize(4) before the samples."""
    comm = _find(chunks, "COMM")
    ssnd = _find(chunks, "SSND")
    if not comm or not ssnd:
        return None
    try:
        bits = struct.unpack_from(">H", data, comm["offset"] + 8 + 6)[0]
        soff = struct.unpack_from(">I", data, ssnd["offset"] + 8)[0]  # data offset
    except struct.error:
        return None
    if bits not in (16, 24, 32):
        return None
    start = ssnd["offset"] + 8 + 8 + soff
    size = max(0, ssnd["size"] - 8 - soff)
    return bits, "big", start, size


def _mp4_duration(data):
    """The MP4 duration-consistency check: the sample table (stts) sums to a media
    duration that must match the media header (mdhd). A mismatch means the header
    and the samples disagree -- a truncation or a mux/edit that updated one and not
    the other. Pure timescale math, no codec knowledge. Single-track only (multi-
    track is ambiguous here). Returns a finding dict or None."""
    mdhd = stts = None
    for b in mp4mod.iter_boxes(data):
        if b["truncated"]:
            continue
        if b["type"] == b"mdhd":
            mdhd = b if mdhd is None else "multi"
        elif b["type"] == b"stts":
            stts = b if stts is None else "multi"
    if mdhd in (None, "multi") or stts in (None, "multi"):
        return None
    try:
        p = mdhd["offset"] + mdhd["hdr"]
        version = data[p]
        if version == 1:
            timescale = struct.unpack_from(">I", data, p + 20)[0]
            declared = struct.unpack_from(">Q", data, p + 24)[0]
        else:
            timescale = struct.unpack_from(">I", data, p + 12)[0]
            declared = struct.unpack_from(">I", data, p + 16)[0]
        q = stts["offset"] + stts["hdr"]
        n = struct.unpack_from(">I", data, q + 4)[0]
        summed = 0
        for i in range(n):
            cnt, delta = struct.unpack_from(">II", data, q + 8 + i * 8)
            summed += cnt * delta
    except struct.error:
        return None
    if not timescale or not declared:
        return None
    # allow one sample-delta of slack; a real mismatch is far larger
    if abs(summed - declared) <= max(2, declared // 1000):
        return None
    return {
        "check": "duration",
        "verdict": f"declared {declared / timescale:.3f} s, sample table sums to "
                   f"{summed / timescale:.3f} s",
        "detail": "the media header (mdhd) duration and the sample table (stts) "
                  "disagree -- the file was truncated or re-muxed without both "
                  "being updated",
    }


def analyze(label, chunks, data):
    """Return a list of ``{check, verdict, detail}`` integrity findings. Read-only.
    Covers WAV/RF64 (little-endian) and AIFF (big-endian) integer-PCM bit depth --
    the bulk of sample files -- and MP4/M4A duration consistency."""
    if label in ("RIFF/WAVE", "RF64/WAVE"):
        spec = _wav_pcm(data, chunks)
    elif label == "IFF/AIFF":
        spec = _aiff_pcm(data, chunks)
    elif label == "MP4/M4A":
        d = _mp4_duration(data)
        return [d] if d else []
    else:
        return []
    if spec is None:
        return []
    bits, order, start, size = spec
    eff, examined = _effective_bits(data, start, size, bits // 8, order)
    finding = _bit_depth_finding(bits, eff, examined)
    return [finding] if finding else []
