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
    # encrypted payload: even the least-random window is near-uniform. A natural
    # recording almost always has at least one low-entropy (quiet/patterned)
    # window, so a high FLOOR across many windows is the signal.
    suspicious = len(win) >= 16 and lo >= 0.92 and mean >= 0.97
    return {
        "region": [off, len(pcm)],
        "sample_width": width,
        "windows": win,
        "min": round(lo, 3),
        "max": round(hi, 3),
        "mean": round(mean, 3),
        "suspicious": suspicious,
        "capped": length > len(pcm),
    }
