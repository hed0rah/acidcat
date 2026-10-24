"""The profile of a sound: how it starts, how long it lasts, whether it repeats,
and where its energy sits in frequency.

For any file this measures the envelope (attack time, how long until it has
fallen 20 dB from its peak, how much of it stays within 12 dB of the peak,
how many times it attacks again) and the share of its energy in five bands,
and names the envelope one of four:

    hit      over within half a second: a drum, a click, a short pluck
    decay    one attack and a long tail: a crash, a piano note, a long 808
    sustain  holds its level: a pad, a drone, a held note, a texture bed
    pulse    attacks again and again: a loop, a roll, a gated pad

These are physical descriptions, not genres or instruments, so the questions
they answer are a producer's ones when fitting found sounds together: what is
short enough to sit on a grid, what can be a bed under something, what already
has rhythm, and what fills the band that is empty.

Measured against 2,074 files whose names or folders said what they were (one
pack at a time, so no pack decides its own answer), drum one-shots came out
as hits on 95%, pads as sustained on 87%, and loops as pulses on 81%. Most of
the misses were the names being wrong (stems of a construction kit called
"Kick.wav"), or a crash, which really does decay. On the bands, 808s put 89%
of their energy under 60 Hz, kicks and basses sit in 60-250 Hz, pads and keys
in 250 Hz-1 kHz, claps in 1-4 kHz and hats above 4 kHz.

numpy only, about 50 ms a file. The first MAX_SECONDS of a file is enough to
tell.
"""

# bump when the answers change; `lib index --profile` re-runs files whose
# stored version differs
PROFILE_VERSION = 1

MAX_SECONDS = 30.0
RATE_MAX = 24000             # decimate under this: nyquist stays above 11 kHz
FRAME = 1024
HOP = 256
BANDS = (("sub", 20, 60), ("low", 60, 250), ("lowmid", 250, 1000),
         ("mid", 1000, 4000), ("high", 4000, 11000))
BAND_NAMES = tuple(b[0] for b in BANDS)
ENVELOPES = ("hit", "decay", "sustain", "pulse")

ACTIVE_DB = 40.0             # frames quieter than peak-40 dB are silence
REATTACK_DB = 9.0            # a re-attack climbs this far in REATTACK_S
REATTACK_S = 0.05
REATTACK_GAP_S = 0.08        # two attacks closer than this are one
REATTACK_FLOOR_DB = 30.0     # and it lands within 30 dB of the peak
PULSE_REATTACKS = 3
HIT_DECAY_MS = 500.0
HIT_ACTIVE_S = 1.5
SUSTAIN_ACTIVE_S = 2.0          # sounding for longer than this
SUSTAIN_FRACTION = 0.5          # and within 12 dB of its peak for more than half of it


def _np():
    from acidcat.core.analysis.pcm import _np as np
    return np()


def curves(x, sr):
    """Per-frame curves for a mono signal: level in dB, per-band power, and
    the power-weighted spectral centroid. None for a signal too short to
    frame, or silent."""
    np = _np()
    from acidcat.core.analysis.pitch import decimate
    x = np.asarray(x, dtype=np.float64)[: int(MAX_SECONDS * sr)]
    x, sr = decimate(x, sr, RATE_MAX)
    if len(x) < FRAME or not np.any(x):
        return None
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    power = np.abs(np.fft.rfft(x[idx] * np.hanning(FRAME)[None, :], axis=1)) ** 2
    total = power.sum(axis=1)
    freqs = np.fft.rfftfreq(FRAME, 1.0 / sr)
    return {
        "fps": sr / HOP,
        "level_db": 10 * np.log10(np.maximum(total, 1e-20)),
        "bands": np.stack([power[:, (freqs >= lo) & (freqs < hi)].sum(axis=1)
                           for _, lo, hi in BANDS], axis=1),
        "centroid": (power * freqs[None, :]).sum(axis=1) / np.maximum(total, 1e-20),
    }


def reattacks(level_db, fps):
    """Frames where the level climbs REATTACK_DB within REATTACK_S, after the
    first attack. A sound that starts from silence does not count its start."""
    look = max(1, int(REATTACK_S * fps))
    gap = max(1, int(REATTACK_GAP_S * fps))
    floor = level_db.max() - REATTACK_FLOOR_DB
    out, last = [], -gap - 1
    for i in range(look, len(level_db)):
        if (level_db[i] > floor and i - last > gap
                and level_db[i] - level_db[i - look:i].min() >= REATTACK_DB):
            out.append(i)
            last = i
    return out


def describe(c, duration):
    """The profile dict from `curves` output and the file's full duration."""
    np = _np()
    fps = c["fps"]
    lv = c["level_db"]
    peak_i = int(np.argmax(lv))
    peak = float(lv[peak_i])
    active = lv > peak - ACTIVE_DB
    amp = 10 ** (lv / 20)

    # attack: the 10% to 90% rise of the first hit -- from the first frame at
    # 10% of the peak amplitude to the first that reaches 90% of the loudest
    # in the half second after it. a loop's attack is its first hit's
    start = int(np.argmax(amp >= 0.1 * amp[peak_i]))
    window = amp[start:start + max(2, int(0.5 * fps))]
    first = start + int(np.argmax(window >= 0.9 * window.max()))
    # decay: from that first peak until the level is 20 dB under it, or the
    # file ends. measured from the first peak, not the loudest frame, which
    # in a held sound can land anywhere
    after = lv[first:]
    below = np.flatnonzero(after < lv[first] - 20)
    decay_ms = (below[0] if len(below) else len(after)) / fps * 1000
    re_ = reattacks(lv, fps)

    bands = c["bands"][active]
    total = float(bands.sum())
    share = {name: (float(bands[:, i].sum()) / total if total else 0.0)
             for i, name in enumerate(BAND_NAMES)}
    w = 10 ** (lv[active] / 10)
    d = {
        "attack_ms": round((first - start) / fps * 1000, 1),
        "decay_ms": round(decay_ms, 1),
        "sustain": round(float(np.mean(lv[active] > peak - 12)), 3),
        "active_s": round(float(active.sum()) / fps, 3),
        "reattacks": len(re_),
        "centroid_hz": round(float((c["centroid"][active] * w).sum() / w.sum()), 1),
        "bands": {k: round(v, 4) for k, v in share.items()},
        "band": max(share, key=share.get) if total else None,
        "duration": duration,
    }
    d["envelope"] = envelope(d)
    return d


def envelope(d):
    """hit / decay / sustain / pulse from a profile dict; see the module doc."""
    if d["reattacks"] >= PULSE_REATTACKS:
        return "pulse"
    if d["decay_ms"] < HIT_DECAY_MS and d["active_s"] < HIT_ACTIVE_S:
        return "hit"
    if d["active_s"] > SUSTAIN_ACTIVE_S and d["sustain"] > SUSTAIN_FRACTION:
        return "sustain"
    return "decay"


def estimate_profile(path_or_array, sr=None):
    """The profile of a file or a mono array, or None when there is nothing to
    measure (empty, silent, non-finite, shorter than one frame)."""
    np = _np()
    if isinstance(path_or_array, (str, bytes)) or hasattr(path_or_array, "__fspath__"):
        from acidcat.core.analysis.pitch import load_mono
        x, sr = load_mono(path_or_array)
    else:
        if sr is None:
            raise ValueError("sr is required with an array")
        x = np.asarray(path_or_array, dtype=np.float64)
        if x.ndim == 2:
            x = x.mean(axis=1 if x.shape[1] < x.shape[0] else 0)
    if len(x) == 0 or not np.all(np.isfinite(x)):
        return None
    c = curves(x, sr)
    if c is None:
        return None
    return describe(c, round(len(x) / sr, 4))


def band_label(name):
    """Hz range of a band name, for display: 'low' -> '60-250 Hz'."""
    def hz(f):
        return f"{f // 1000} kHz" if f >= 1000 else f"{f} Hz"
    for n, lo, hi in BANDS:
        if n == name:
            return f"{hz(lo)}-{hz(hi)}"
    return None
