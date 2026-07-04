"""Low-bit-plane entropy analysis for PCM audio (LSB-steganography detection).

Natural audio's least-significant bits are correlated with the signal: in quiet
passages they are patterned or near-constant, so per-window LSB entropy varies
and dips low. An encrypted/compressed hidden payload (DeepSound, OpenPuff,
Steghide, ...) writes ~uniform bits, so LSB entropy sits near 1.0 across EVERY
window, including silence. That uniform-high floor is the detectable tell.

This only sees payloads written into the sample LSBs; echo/phase/spread-spectrum
stego have no byte-level signature and are out of scope (say so, do not fake it).
"""

import math
import struct

_MAX_PCM = 16 * 1024 * 1024  # cap the bytes we scan, so a huge file stays snappy


def _bit_entropy(ones, total):
    if total <= 0:
        return 0.0
    p = ones / total
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -p * math.log2(p) - (1 - p) * math.log2(1 - p)


def entropy_windows(pcm, sample_width, windows=64):
    """Per-window Shannon entropy (0..1) of the LSB of each PCM sample."""
    width = max(1, sample_width)
    lows = pcm[::width]                 # bit 0 lives in the low byte (LE) of each sample
    n = len(lows)
    if n < windows * 4:
        windows = max(1, n // 4)
    if windows == 0 or n == 0:
        return []
    per = n // windows
    if per == 0:
        return []
    out = []
    for w in range(windows):
        seg = lows[w * per:(w + 1) * per]
        ones = sum(b & 1 for b in seg)
        out.append(round(_bit_entropy(ones, len(seg)), 3))
    return out


_DE_CAP = 200_000  # bytes of PCM to score for the dual-endian check (plenty)


def _lag1_autocorr(b, swap):
    """Normalized lag-1 autocorrelation of 16-bit samples in b. With swap, read
    each sample's bytes in the opposite endianness first (the cross-endian view).
    Near 1.0 = structured audio, near 0 = noise."""
    if swap:
        b = bytes(b[i ^ 1] for i in range(len(b)))
    n = len(b) // 2
    if n < 2:
        return 0.0
    s = struct.unpack_from("<%dh" % n, b, 0)
    den = sum(v * v for v in s) or 1
    return sum(s[i] * s[i + 1] for i in range(n - 1)) / den


def _pcm16_region(fmt_label, chunks):
    """(offset, length) of a 16-bit linear-PCM region for WAV (data) or AIFF
    (SSND, past its 8-byte preamble), or None if not 16-bit PCM."""
    bits = None
    region = hdr = None
    for c in chunks:
        cid = str(c.get("id", "")).strip().upper()
        if cid in ("FMT", "COMM"):
            for f in c.get("fields", []):
                if f.get("name") in ("bits_per_sample", "sample_size"):
                    try:
                        bits = int(str(f.get("value")).split()[0])
                    except (ValueError, IndexError):
                        pass
        elif cid == "DATA" and c.get("offset") is not None:
            region, hdr = (c["offset"], c.get("size") or 0), 8
        elif cid == "SSND" and c.get("offset") is not None:
            region, hdr = (c["offset"], c.get("size") or 0), 16
    if bits != 16 or region is None:
        return None
    return region[0] + hdr, max(0, region[1] - (hdr - 8))


def dual_endian(filepath, fmt_label, chunks):
    """Score whether BOTH endian readings of a 16-bit PCM block are structured
    audio (a cross-endian WAV/AIFF artifact), not just one. Returns
    {le, be, flagged} or None when not applicable."""
    reg = _pcm16_region(fmt_label, chunks)
    if reg is None:
        return None
    off, length = reg
    with open(filepath, "rb") as f:
        f.seek(off)
        pcm = f.read(min(length, _DE_CAP))
    b = pcm[:len(pcm) - len(pcm) % 2]
    if len(b) < 512:
        return None
    le = _lag1_autocorr(b, False)
    be = _lag1_autocorr(b, True)
    # calibrated on 2328 real WAVs: bass-heavy audio can leave the byte-swapped
    # view moderately structured (up to ~0.86), so require BOTH views strongly
    # structured. 0.9 flags the crafted artifact (~0.97) with ~0.3% false rate.
    return {"le": round(le, 3), "be": round(be, 3), "flagged": le > 0.9 and be > 0.9}


def _wav_pcm_region(filepath, chunks):
    """(data_offset, data_len, sample_width_bytes) for a PCM WAV, or None."""
    bits = fmt_tag = None
    data = None
    for c in chunks:
        cid = str(c.get("id", "")).strip()
        if cid == "fmt":
            for f in c.get("fields", []):
                if f.get("name") == "bits_per_sample":
                    try:
                        bits = int(str(f.get("value")).split()[0])
                    except (ValueError, IndexError):
                        pass
                if f.get("name") == "format_tag":
                    fmt_tag = str(f.get("value"))
        elif cid == "data":
            data = (c.get("offset"), c.get("size"))
    if not bits or not data or data[0] is None or data[1] is None:
        return None
    if fmt_tag and "0x0001" not in fmt_tag and "PCM" not in fmt_tag:
        return None  # only linear PCM has meaningful sample LSBs
    return data[0] + 8, data[1], max(1, bits // 8)


def analyze(filepath, fmt_label, chunks):
    """Analyze a PCM WAV's sample LSBs. Returns a dict with the per-window
    entropy map, summary stats, and a suspicion flag, or None if not applicable."""
    if not fmt_label or not fmt_label.startswith("RIFF/WAVE"):
        return None
    region = _wav_pcm_region(filepath, chunks)
    if region is None:
        return None
    off, length, width = region
    with open(filepath, "rb") as f:
        f.seek(off)
        pcm = f.read(min(length, _MAX_PCM))
    if len(pcm) < width * 256:
        return None
    win = entropy_windows(pcm, width)
    if not win:
        return None
    lo, hi = min(win), max(win)
    mean = sum(win) / len(win)
    # A uniformly high LSB entropy floor is CONSISTENT with an encrypted embedded
    # payload, but entropy alone cannot separate that from a legitimate noise
    # floor: real field recordings, dithered masters, and high-bit-depth captures
    # also fill their low bits with near-random noise. So this is a descriptive
    # flag, not a verdict; callers report it as a heuristic, not an alert.
    # (A discriminating test, e.g. StegExpose-style sample-pair/chi-square, would
    # be the next step to cut the false positives on noisy audio.)
    uniform_high = len(win) >= 16 and lo >= 0.92 and mean >= 0.97
    return {
        "region": [off, len(pcm)],
        "sample_width": width,
        "windows": win,
        "min": round(lo, 3),
        "max": round(hi, 3),
        "mean": round(mean, 3),
        "uniform_high": uniform_high,
        "capped": length > len(pcm),
    }
