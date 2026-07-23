"""Statistical audio-blob detection -- the signatureless engine behind `scan`.

`scan` is "PhotoRec for audio." PhotoRec carves files by *signature* (magic
headers/footers); raw PCM has no signature, so a signature carver walks straight
past it. This module fills that gap: it finds raw audio in an unknown blob by its
*statistical structure* -- the same smoothness (sample[n] ~= sample[n-1]) that
lets DPCM/Fibonacci/BRR compress audio is what makes raw audio detectable.
Compressibility and detectability are the same coin.

Phase 1 (this module) is the LOCATOR: a windowed pass that flags candidate audio
regions. It is tuned for *recall* -- catch the audio, tolerate some false hits --
because the precision comes downstream (back up to a container header, hand the
range to the real walker). Its output regions are exactly what `carve` consumes.

The discriminator is entropy + the *shape* of autocorrelation across lags, drawn
from measured class profiles:

    class          entropy   r1     r2     r4     r8
    random noise   ~8.0      ~0     ~0     ~0     ~0
    program code   ~4.8      +0.42  +0.29  +0.15  +0.05   (monotone decay, low H)
    8-bit voice    ~7.5      +0.53  +0.56  +0.17  -0.25   (bump at r2, oscillates)
    clean tone     ~6.8      +0.99  +0.98  +0.91  +0.67   (sustained)

Noise is flat at every lag; code decays monotonically from a modest peak; audio
either *sustains* correlation (tonal) or *oscillates* into negative autocorr
(voiced waveform). A lone lag-1 threshold can't separate low-fi 8-bit audio
(r1~0.5) from code (r1~0.4) -- the decay/oscillation shape is what does.

v1 reads the blob as 8-bit signed PCM. 16-bit / endianness / sign is a small
search (try the strides, keep the best autocorr) and is a later increment.
Compressed audio blobs (BRR, ADPCM, MP3) are high-entropy and not smooth, so
this engine does not find them; they need structural signatures -- also later.

Pure-Python by design: `scan` is a base-install capability, not gated behind the
numpy `analysis` extra.
"""

import math
import struct

# ---- tunables (first-cut, derived from the class profiles above; a labeled
# corpus pass is expected to refine these) -------------------------------------

LAGS = (1, 2, 4, 8)               # autocorrelation lags that expose the decay shape

_PEAK_FLOOR = 0.25                # low-lag autocorr below this reads as noise
_PEAK_SPAN = 0.50                 # ... and saturates confidence PEAK_SPAN above the floor
_STRUCT_SPAN = 0.30               # structure needed to clear code's monotone decay
_ENTROPY_FLOOR = 2.0              # below this the window is ~constant, not a live blob

# distribution gate (calibrated on a labeled corpus: real 8SVX audio vs code/
# text/random/binary). autocorrelation already rejects random/compressed/binary
# cold (~0 correlation); the residual false positives are structured CODE and
# TEXT, which are ~99% printable bytes while real audio is ~22% (p90 0.37) --
# a clean, no-overlap separation. So a printable-fraction factor zeroes code/
# text without touching audio recall.
_PRINTABLE_LO = 0.35              # audio sits at/below this; factor is 1.0 here
_PRINTABLE_HI = 0.70             # code/text is ~1.0; factor reaches 0.0 by here
_ENTROPY_CEIL = 7.7              # above this is random/compressed -- skip the
                                 # expensive autocorrelation (it would score 0)

DEFAULT_WINDOW = 1024
DEFAULT_STEP = 512
DEFAULT_MIN_SCORE = 0.25          # recall-oriented: phases 2/3 supply precision
DEFAULT_MERGE_GAP = 4             # bridge up to this many below-gate windows (~2 KiB)
DEFAULT_READ_CAP = 256 * 1024 * 1024

# signed-8-bit lookup: byte 0..255 -> -128..127
_SIGNED = tuple(b - 256 if b > 127 else b for b in range(256))


def _clamp01(x):
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _entropy(win):
    """Shannon entropy (bits/byte, 0..8) of a window's byte values."""
    n = len(win)
    if n == 0:
        return 0.0
    counts = [0] * 256
    for b in win:
        counts[b] += 1
    return _entropy_from_counts(counts, n)


def _entropy_from_counts(counts, n):
    h = 0.0
    for c in counts:
        if c:
            p = c / n
            h -= p * math.log2(p)
    return h


# text/code tell: bytes in the printable ASCII band (+ tab/newline/return)
_PRINTABLE = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def _distribution(counts, n):
    """Value-distribution shape from a byte histogram.

    printable_frac -- share of bytes in the text range; a code/text signature.
    hist_tv -- total variation of the normalized histogram: audio's value
    histogram is smooth (a sampled continuous signal), code's is spiky (isolated
    peaks at common byte values), random's is flat. So high TV reads as code."""
    printable = sum(counts[b] for b in _PRINTABLE) / n
    tv = 0.0
    prev = counts[0] / n
    for c in counts[1:]:
        cur = c / n
        tv += cur - prev if cur > prev else prev - cur
        prev = cur
    return printable, tv


def _autocorr(samples, mean, den, lag):
    """Pearson autocorrelation at `lag` for a signed-sample sequence, given its
    precomputed mean and denominator (sum of squared deviations)."""
    n = len(samples)
    if den <= 0.0 or lag >= n:
        return 0.0
    num = 0.0
    for i in range(n - lag):
        num += (samples[i] - mean) * (samples[i + lag] - mean)
    return num / den


def window_features(win):
    """Feature vector for one window of bytes, read as 8-bit signed PCM.

    Returns a dict: entropy (bits), autocorr {lag: r}, and the derived
    peak/structure terms so callers can show the evidence behind a hit."""
    n = len(win)
    counts = [0] * 256
    for b in win:
        counts[b] += 1
    entropy = _entropy_from_counts(counts, n)
    printable, hist_tv = _distribution(counts, n)
    # cheap pre-filter: a window at near-maximal entropy is random / compressed /
    # encrypted and cannot be raw audio, so skip the O(n * lags) autocorrelation
    # AND the sample decode below (it would score 0 anyway). This is the bulk of
    # a real disk image, so the early-out is most of the speed.
    if n < LAGS[-1] + 1 or entropy > _ENTROPY_CEIL:
        return {"entropy": entropy, "autocorr": {L: 0.0 for L in LAGS},
                "peak": 0.0, "structure": 0.0, "printable": printable,
                "hist_tv": hist_tv, "n": n}

    samples = [_SIGNED[b] for b in win]
    mean = sum(samples) / n
    den = 0.0
    for s in samples:
        d = s - mean
        den += d * d
    ac = {L: _autocorr(samples, mean, den, L) for L in LAGS}

    r1, r2, r4, r8 = ac[1], ac[2], ac[4], ac[8]
    peak = max(r1, r2)                                  # low-lag correlation strength
    # structure separates a waveform from code's monotone decay:
    oscillate = max(-r4, -r8, 0.0)                      # dips negative at higher lag (voiced)
    sustain = max(r4, r8, 0.0)                          # stays correlated (tonal)
    periodic = max(r2 - r1, 0.0)                        # bump at lag 2 (pitched)
    structure = max(oscillate, sustain, periodic)
    return {"entropy": entropy, "autocorr": ac, "peak": peak,
            "structure": structure, "printable": printable,
            "hist_tv": hist_tv, "n": n}


def audio_score(feat):
    """Audio-likeness in [0, 1] from a feature vector. Recall-oriented: a window
    scores only when it is *correlated* (beats noise), *shaped* like a waveform
    (beats code's monotone decay), and not text/code by value distribution."""
    if feat["entropy"] < _ENTROPY_FLOOR:
        return 0.0
    strength = _clamp01((feat["peak"] - _PEAK_FLOOR) / _PEAK_SPAN)
    shape = _clamp01(feat["structure"] / _STRUCT_SPAN)
    dist = _clamp01((_PRINTABLE_HI - feat.get("printable", 0.0))
                    / (_PRINTABLE_HI - _PRINTABLE_LO))
    return strength * shape * dist


def scan(data, *, window=DEFAULT_WINDOW, step=DEFAULT_STEP,
         min_score=DEFAULT_MIN_SCORE, merge_gap=DEFAULT_MERGE_GAP,
         read_cap=DEFAULT_READ_CAP):
    """Locate candidate raw-audio regions in `data` (bytes).

    Slides a window, scores each, and merges runs of audio-like windows into
    regions. Real audio is dynamic -- quiet passages and transients dip below the
    gate -- so a region is held open across up to `merge_gap` consecutive
    below-gate windows (hysteresis), keeping one file as one region instead of
    shattering it into fragments. Returns dicts with offset/end, a confidence
    (mean of the audio windows), and averaged evidence. Never raises."""
    if read_cap and len(data) > read_cap:
        data = data[:read_cap]
    n = len(data)
    if n < window:
        return []

    marks = []                                          # (offset, score, feat)
    off = 0
    last = n - window
    while off <= last:
        feat = window_features(data[off:off + window])
        marks.append((off, audio_score(feat), feat))
        off += step

    regions = []
    run = None                                          # accumulating region
    gap = 0                                             # consecutive below-gate windows
    for off, score, feat in marks:
        if score >= min_score:
            if run is None:
                run = {"start": off, "end": off + window, "_scores": [score],
                       "_feats": [feat]}
            else:
                run["end"] = off + window               # end tracks the last HIT only
                run["_scores"].append(score)
                run["_feats"].append(feat)
            gap = 0
        elif run is not None:
            gap += 1
            if gap > merge_gap:                         # sustained non-audio: close
                regions.append(_finalize(run))
                run = None
                gap = 0
            # else bridge the short dip, keeping the region open
    if run is not None:
        regions.append(_finalize(run))
    return regions


def analyze_geometry(data, cap=16384):
    """Infer the PCM geometry of a raw region -- bit width (8/16), channels
    (mono/stereo), and endianness -- by which interpretation is smoothest
    (highest lag-1 autocorrelation). Sample RATE is playback metadata that does
    not live in the bytes, so it is reported as None with common candidates.
    Returns a dict; never raises."""
    b = data[:cap]

    def _ac(seq):
        n = len(seq)
        if n < 8:
            return -1.0
        m = sum(seq) / n
        den = 0.0
        for s in seq:
            d = s - m
            den += d * d
        if den <= 0:
            return -1.0
        num = 0.0
        for i in range(n - 1):
            num += (seq[i] - m) * (seq[i + 1] - m)
        return num / den

    s8 = [x - 256 if x > 127 else x for x in b]
    n16 = len(b) // 2
    le = [struct.unpack_from("<h", b, i * 2)[0] for i in range(n16)]
    be = [struct.unpack_from(">h", b, i * 2)[0] for i in range(n16)]
    cands = [(8, 1, None, _ac(s8)),
             (16, 1, "le", _ac(le)),
             (16, 1, "be", _ac(be))]
    if n16 >= 16:
        cands.append((16, 2, "le", (_ac(le[0::2]) + _ac(le[1::2])) / 2))
        cands.append((16, 2, "be", (_ac(be[0::2]) + _ac(be[1::2])) / 2))
    if len(s8) >= 16:
        cands.append((8, 2, None, (_ac(s8[0::2]) + _ac(s8[1::2])) / 2))
    width, channels, endian, score = max(cands, key=lambda c: c[3])
    return {"width": width, "channels": channels, "endian": endian,
            "confidence": round(max(score, 0.0), 3),
            "rate": None, "rate_candidates": [8000, 11025, 22050, 44100, 48000]}


def _finalize(run):
    """Collapse an accumulated run into a reported region with mean evidence."""
    scores = run["_scores"]
    feats = run["_feats"]
    k = len(feats)
    mean_ac = {L: sum(f["autocorr"][L] for f in feats) / k for L in LAGS}
    return {
        "start": run["start"],
        "end": run["end"],
        "length": run["end"] - run["start"],
        "confidence": sum(scores) / k,
        "windows": k,
        "evidence": {
            "entropy": sum(f["entropy"] for f in feats) / k,
            "autocorr": mean_ac,
            "width": 1,                                 # v1: 8-bit signed PCM
        },
    }
