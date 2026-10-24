"""The pitch a one-shot settles on: an 808, a bass note, a pluck, a found sound.

For a single hit this estimates the fundamental in Hz, the nearest MIDI note,
the offset from it in cents, a 0..1 tunability score (how clearly the sound has
one pitch), and the glide (how far above its settled pitch the sound starts,
which an 808 does a lot). The question it answers is "what do I retune this by
to sit in my key", including for sounds that were never meant to be tuned.

The method is YIN (de Cheveigne and Kawahara, 2002) frame by frame over the
part of the sound after the attack, with a harmonic product spectrum of the
same region as an independent cross-check, a coherence test that tells a tone
from narrow-band noise, and a zero-padded spectral peak to refine the cents.
numpy only; librosa is not needed.

Measured on 1,817 one-shots from a real library whose names gave a note, the
note it reported matched the name on 100% of 28 808s in a tuned 808 pack, and
when it answered on 82% at every length under 4 s: 85% of basses, 93% of
plucks, 90% of toms, 73% of kicks. It declines on hats, claps and most chords,
which is the point of the tunability gate. Loops hold several notes and a
median of them is no pitch, so the library index runs it on files under
MAX_DURATION only.

Sign conventions: `cents` is detected minus the nearest note (positive is
sharp); `glide_cents` is early pitch minus settled pitch (positive is a sound
that starts high and falls).
"""

import math

# bump when the estimator's answers change; `lib index --pitch` re-runs files
# whose stored version differs
PITCH_VERSION = 1

# the longest file the index estimates; every measurement above is under it
MAX_DURATION = 4.0

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

ANALYSIS_SR_MAX = 22050      # decimate by the integer factor that gets under this
FRAME_S = 0.128              # the yin window is half of it (64 ms)
HOP_S = 0.016
COHERENCE_LAG_S = 0.010      # periodicity must survive at least this far
FMIN = 25.0                  # below the lowest 808 anyone tunes
FMAX = 4200.0
YIN_THRESHOLD = 0.15         # absolute-threshold step of the yin paper
VOICED_MAX_APERIODICITY = 0.30
REGION_FLOOR_DB = -40.0      # frames quieter than peak-40 dB are tail/silence
REGION_HEAD_DB = -12.0       # region starts at the first frame within 12 dB of peak
MIN_TUNABLE = 0.35           # below this no note is reported


def _np():
    from acidcat.core.analysis.pcm import _np as np
    return np()


def load_mono(path):
    """(mono float64 array, rate). acidcat's PCM reader first, soundfile for
    anything it does not decode (compressed codecs, odd sample formats)."""
    np = _np()
    from acidcat.core.analysis import pcm
    try:
        planes, rate = pcm.load(path)
        x = np.mean(np.stack(planes), axis=0) if len(planes) > 1 else planes[0]
        return np.asarray(x, dtype=np.float64), int(rate)
    except pcm.Unsupported:
        pass
    import soundfile as sf
    x, rate = sf.read(path, dtype="float64", always_2d=True)
    return x.mean(axis=1), int(rate)


def decimate(x, sr, max_rate=ANALYSIS_SR_MAX):
    """Integer-factor decimation behind a windowed-sinc lowpass; (y, new_rate).

    Truncating the spectrum instead rings a 3 ms click out over the whole
    buffer, which then reads as a pitched tail at the cutoff frequency."""
    np = _np()
    q = int(math.ceil(sr / max_rate))
    if q <= 1 or len(x) == 0:
        return x, sr
    taps = 16 * q + 1
    n = np.arange(taps) - (taps - 1) / 2
    cutoff = 0.45 / q                    # cycles/sample, a little under the new nyquist
    h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
    h /= h.sum()
    y = np.convolve(x, h, mode="same")
    return y[::q], sr // q


def _frames(x, frame, hop):
    np = _np()
    if len(x) < frame:
        x = np.concatenate([x, np.zeros(frame - len(x))])
    n = 1 + (len(x) - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    return x[idx]


def yin_frames(frames, sr, fmin=FMIN, fmax=FMAX, threshold=YIN_THRESHOLD):
    """Per-frame (f0_hz, aperiodicity). Aperiodicity is the cumulative-mean
    normalised difference at the chosen lag: 0 is a perfect period, ~1 noise.

    `frames` is (n, FRAME). The yin window is FRAME // 2 so every lag up to the
    window length has a full comparison window."""
    np = _np()
    n, flen = frames.shape
    w = flen // 2
    tau_max = min(w, int(sr / fmin))
    tau_min = max(2, int(sr / fmax))

    # d(tau) = E[0:w] + E[tau:tau+w] - 2 * sum_j x[j] x[j+tau], the energy
    # terms by cumulative sum and the cross term by fft
    sq = np.cumsum(np.concatenate([np.zeros((n, 1)), frames ** 2], axis=1), axis=1)
    e_tau = sq[:, w:w + tau_max] - sq[:, 0:tau_max]
    e_0 = e_tau[:, :1]
    nfft = 1 << int(math.ceil(math.log2(2 * flen)))
    spec_x = np.fft.rfft(frames, nfft, axis=1)
    spec_w = np.fft.rfft(frames[:, :w], nfft, axis=1)
    xcorr = np.fft.irfft(spec_x * np.conj(spec_w), nfft, axis=1)[:, :tau_max]
    d = e_0 + e_tau - 2.0 * xcorr
    d[:, 0] = 0.0
    d = np.maximum(d, 0.0)

    taus = np.arange(1, tau_max)
    csum = np.cumsum(d[:, 1:], axis=1)
    cmndf = np.ones_like(d)
    with np.errstate(divide="ignore", invalid="ignore"):
        cmndf[:, 1:] = np.where(csum > 0, d[:, 1:] * taus / csum, 1.0)

    f0 = np.zeros(n)
    aper = np.ones(n)
    for i in range(n):
        c = cmndf[i]
        tau = _pick_lag(np, c, tau_min, tau_max, threshold)
        if tau is None:
            continue
        # parabolic refinement of the minimum
        t = tau
        if 1 <= t < tau_max - 1:
            a, b, cc = c[t - 1], c[t], c[t + 1]
            denom = a - 2 * b + cc
            if denom > 0:
                t = t + 0.5 * (a - cc) / denom
        f0[i] = sr / t
        aper[i] = float(min(1.0, max(0.0, c[tau])))
    return f0, aper


def _pick_lag(np, c, tau_min, tau_max, threshold):
    """First lag whose cmndf dips under the threshold, followed to its local
    minimum; the global minimum in range if nothing dips."""
    below = np.flatnonzero(c[tau_min:tau_max] < threshold)
    if len(below):
        t = tau_min + below[0]
        while t + 1 < tau_max and c[t + 1] < c[t]:
            t += 1
        return int(t)
    seg = c[tau_min:tau_max]
    if not len(seg):
        return None
    return int(tau_min + np.argmin(seg))


def hps_f0(x, sr, fmin=FMIN, fmax=FMAX, harmonics=5):
    """Harmonic product spectrum fundamental over a whole segment, or None."""
    np = _np()
    if len(x) < 64:
        return None
    nfft = 1 << int(math.ceil(math.log2(max(len(x), sr // 2) * 4)))
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x)), nfft))
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    logmag = np.log(mag + 1e-12)
    n = len(logmag)
    acc = logmag.copy()
    for h in range(2, harmonics + 1):
        dec = logmag[::h]
        acc[:len(dec)] += dec
        acc[len(dec):] += logmag[-1]
    lo = np.searchsorted(freqs, fmin)
    hi = min(np.searchsorted(freqs, fmax), n // harmonics)
    if hi <= lo:
        return None
    # a candidate must itself carry energy: a pure tone otherwise ties with
    # its own subharmonics (every term but one is the noise floor either way)
    cand = acc[lo:hi].copy()
    cand[mag[lo:hi] < 0.02 * mag.max()] = -np.inf
    if not np.isfinite(cand).any():
        return None
    return float(freqs[lo + int(np.argmax(cand))])


def coherence_at(seg, period, min_lag_s, sr):
    """Normalised autocorrelation of `seg` at a whole number of periods at
    least `min_lag_s` long, or None when the segment is too short. The multiple
    is chosen so the lag lands near an integer sample (a half-sample miss is 45
    degrees of phase at a 4-sample period)."""
    np = _np()
    m0 = max(1, int(math.ceil(min_lag_s * sr / period)))
    best = min(range(m0, m0 + 12), key=lambda m: abs(m * period - round(m * period)))
    lag = int(round(best * period))
    if lag < 1 or lag >= len(seg) // 2:
        return None
    a, b = seg[:-lag], seg[lag:]
    denom = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b)))
    if denom <= 0:
        return None
    return float(np.dot(a, b)) / denom


def spectral_peak_near(seg, sr, f, window_cents=50.0):
    """Parabolic-interpolated magnitude peak within `window_cents` of `f` in a
    zero-padded spectrum of `seg`, or None if the peak bin is not a local
    maximum."""
    np = _np()
    if len(seg) < 32:
        return None
    nfft = 1 << int(math.ceil(math.log2(len(seg) * 8)))
    mag = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), nfft))
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    lo = np.searchsorted(freqs, f * 2 ** (-window_cents / 1200))
    hi = np.searchsorted(freqs, f * 2 ** (window_cents / 1200))
    if hi - lo < 3:
        return None
    k = lo + int(np.argmax(mag[lo:hi]))
    if k <= lo or k >= hi - 1 or k == 0 or k >= len(mag) - 1:
        return None
    a, b, c = (np.log(mag[k - 1] + 1e-12), np.log(mag[k] + 1e-12),
               np.log(mag[k + 1] + 1e-12))
    denom = a - 2 * b + c
    delta = 0.5 * (a - c) / denom if denom < 0 else 0.0
    return float((k + delta) * sr / nfft)


def hz_to_cents(f):
    """Cents above MIDI note 0 (8.1758 Hz)."""
    np = _np()
    return 1200.0 * np.log2(np.asarray(f, dtype=np.float64) / 440.0) + 6900.0


def note_name(midi):
    """MIDI note number to a name with octave, middle C (60) as C4."""
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


_LETTERS = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def parse_note(spec):
    """A note filter: "F#" or "Gb" is (6, None), any octave; "F#1" is
    (6, 30), that note only. None when `spec` is not a note."""
    s = (spec or "").strip()
    if not s or s[0].lower() not in _LETTERS:
        return None
    pc, rest = _LETTERS[s[0].lower()], s[1:]
    if rest[:1] == "#":
        pc, rest = pc + 1, rest[1:]
    elif rest[:1] == "b":
        pc, rest = pc - 1, rest[1:]
    pc %= 12
    if not rest:
        return pc, None
    try:
        octave = int(rest)
    except ValueError:
        return None
    # the octave belongs to the letter: Cb4 is B3, B#3 is C4
    base = _LETTERS[s[0].lower()] + (1 if s[1:2] == "#" else -1 if s[1:2] == "b" else 0)
    midi = (octave + 1) * 12 + base
    return (pc, midi) if 0 <= midi < 128 else None


def cents_to_note(c):
    """(midi_note, cents_offset, note_name) for an absolute cents value."""
    midi = int(round(c / 100.0))
    return midi, c - 100.0 * midi, note_name(midi)


def estimate_pitch(path_or_array, sr=None, min_tunable=MIN_TUNABLE):
    """The pitch of a one-shot, as a dict:

    f0_hz, midi_note, note_name, cents  settled pitch; None when tunability
                                        is under `min_tunable`
    pitch_class                         0..11 (C = 0), None when unpitched
    tunability                          0..1, how clearly the sound has one pitch
    glide_cents                         early pitch minus settled pitch
    method                              the evidence: yin and hps values,
                                        voiced fraction, stability, region
    """
    np = _np()
    if isinstance(path_or_array, (str, bytes)) or hasattr(path_or_array, "__fspath__"):
        x, sr = load_mono(path_or_array)
    else:
        if sr is None:
            raise ValueError("sr is required with an array")
        x = np.asarray(path_or_array, dtype=np.float64)
        if x.ndim == 2:
            x = x.mean(axis=1 if x.shape[1] < x.shape[0] else 0)

    out = {"f0_hz": None, "midi_note": None, "note_name": None, "cents": None,
           "pitch_class": None, "tunability": 0.0, "glide_cents": None,
           "method": {"source_sr": int(sr), "analysis_sr": None}}
    if len(x) == 0 or not np.all(np.isfinite(x)):
        out["method"]["note"] = "empty or non-finite"
        return out
    x = x - np.mean(x)
    peak = float(np.max(np.abs(x)))
    if peak <= 0:
        out["method"]["note"] = "silent"
        return out
    x = x / peak
    x, sr = decimate(x, sr)
    out["method"]["analysis_sr"] = int(sr)
    hop = int(round(HOP_S * sr))
    frame = 8 * hop

    frames = _frames(x, frame, hop)
    rms = np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-12
    db = 20 * np.log10(rms / rms.max())

    # analysis region: from the first frame within 12 dB of the loudest one
    # (the attack has landed) to the last frame above the -40 dB floor
    start = int(np.flatnonzero(db >= REGION_HEAD_DB)[0])
    end = int(np.flatnonzero(db >= REGION_FLOOR_DB)[-1]) + 1
    region = slice(start, max(end, start + 1))
    rf = frames[region]
    rdb = db[region]

    f0, aper = yin_frames(rf, sr)
    voiced = (aper < VOICED_MAX_APERIODICITY) & (f0 > 0)
    n_voiced = int(voiced.sum())
    out["method"].update({"region_frames": len(rf), "voiced_frames": n_voiced,
                          "region_ms": [round(start * hop / sr * 1000),
                                        round(end * hop / sr * 1000)]})

    # clarity: loudness-weighted periodicity over the whole region, so noisy
    # frames pull it down and silence does not count
    wts = 10 ** (rdb / 20)
    clarity = float(np.sum((1 - aper) * wts) / np.sum(wts))
    if n_voiced == 0:
        out["tunability"] = round(max(0.0, (clarity - 0.5) * 2) * 0.3, 3)
        out["method"]["note"] = "no periodic frames"
        return out

    cents = np.where(voiced, hz_to_cents(np.where(f0 > 0, f0, 1.0)), np.nan)
    vidx = np.flatnonzero(voiced)
    # settled pitch: median of the later half of the voiced frames
    tail = vidx[len(vidx) // 2:] if len(vidx) >= 4 else vidx
    settled = float(np.median(cents[tail]))
    head = vidx[:max(1, min(3, len(vidx) // 4))]
    glide = float(np.median(cents[head])) - settled

    # stability of the settled part: median absolute deviation in cents
    mad = float(np.median(np.abs(cents[tail] - settled)))
    stability = math.exp(-((mad / 35.0) ** 2))
    voiced_frac = float(np.sum(wts[voiced]) / np.sum(wts))

    # hps on the settled part of the signal as an independent opinion
    t0 = start + int(tail[0])
    t1 = start + int(tail[-1])
    seg = x[t0 * hop: t1 * hop + frame]
    hps = hps_f0(seg, sr)
    f_settled = 440.0 * 2 ** ((settled - 6900.0) / 1200.0)
    agree = octave = None
    if hps:
        diff = 1200.0 * math.log2(hps / f_settled)
        agree = abs(diff) < 60.0
        octave = abs(abs(diff) - 1200.0) < 60.0 or abs(abs(diff) - 1902.0) < 60.0

    # does the period survive a longer lag? narrow-band noise correlates with
    # itself one period on (its coherence time is 1/bandwidth) but not ten
    # milliseconds on; a tone does, inharmonic partials only partly
    coh = coherence_at(seg, sr / f_settled, COHERENCE_LAG_S, sr)
    coh_factor = 1.0 if coh is None else min(1.0, max(0.0, coh)) ** 0.5

    # cents refinement: the exact spectral peak of the settled segment, when
    # one sits within half a semitone of the yin answer and the pitch held
    # still. the lag grid is coarse for short periods (one sample is 190
    # cents at 1.5 kHz); a zero-padded spectral peak is not
    refined = spectral_peak_near(seg, sr, f_settled) if mad < 20 else None
    if refined:
        f_settled = refined
        settled = float(hz_to_cents(refined))

    tun = clarity * (0.5 + 0.5 * voiced_frac) * (0.4 + 0.6 * stability) * coh_factor
    if agree is False and not octave:
        tun *= 0.7
    tun = float(min(1.0, max(0.0, tun)))

    out["method"].update({"yin_f0": round(f_settled, 2),
                          "hps_f0": round(hps, 2) if hps else None,
                          "hps_agrees": agree, "hps_octave_off": octave,
                          "clarity": round(clarity, 3),
                          "voiced_frac": round(voiced_frac, 3),
                          "settled_mad_cents": round(mad, 1),
                          "stability": round(stability, 3),
                          "coherence": None if coh is None else round(coh, 3),
                          "spectral_refined": bool(refined)})
    out["tunability"] = round(tun, 3)
    out["glide_cents"] = round(glide, 1)
    if tun >= min_tunable:
        midi, off, name = cents_to_note(settled)
        out.update({"f0_hz": round(f_settled, 2), "midi_note": midi,
                    "note_name": name, "cents": round(off, 1),
                    "pitch_class": midi % 12})
    return out
