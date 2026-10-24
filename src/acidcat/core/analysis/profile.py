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

Wetness, 0 to 1, is how much of a sound is its room: reverb or ambience
baked into the file. A room cannot let a sound fall faster than the room
itself decays, so the first measure is the steepest fall the sound ever
makes: the most it drops within 200 ms, starting within 20 dB of its peak.
A dry note's release falls 50 dB in that time. A wet one falls only as far
as its direct sound stands above the room, then at the room's own rate,
8 dB in 200 ms for a 1.5 s hall. The end of a file counts as silence (a
one-shot stops there) unless the envelope is a pulse, a loop, whose end
runs back into its start. The second measure is stereo: a reverb tail is
decorrelated between the ears, so the quieter frames (12 dB and more under
the peak) of a wet file carry side energy a dry centred source does not.
Mono files, and stereo ones whose channels are identical, are judged on the
fall alone.

On 930 pairs of the same sound shipped dry and wet (18 packs, mostly
vocals), the wet twin read wetter on 82%. Against names alone (1,494 files
from 81 packs: names or folders saying wet, reverb, verb, tail, hall or
room, against dry-named files and drum one-shots) the AUC is 0.73, and 0.78
for the reverb, verb, tail and hall names alone; 0.5 splits them with 69%
of the wet over it and 77% of the dry under. Most of the misses are the
names: "wet" in a pack often means "with effects" (a sidechained bass, a
distorted kick), and many dry one-shots carry a room. The rest are real
limits: a slow natural decay (a piano note, a long 808, a crash) falls no
faster than a room would, so in mono it reads wet; and a reversed sound,
which swells and stops dead, reads dry.

numpy only, about 60 ms a file. The first MAX_SECONDS of a file is enough to
tell.
"""

# bump when the answers change; `lib index --profile` re-runs files whose
# stored version differs. 2: wetness
PROFILE_VERSION = 2

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

WET_WINDOW_S = 0.2           # the steepest fall within this long
WET_FROM_DB = 20.0           # that starts within this far of the peak
WET_FLOOR_DB = 50.0          # and lands no lower than this under it
WET_DRY_DB = 50.0            # a fall this deep is dry (0)
WET_WET_DB = 10.0            # and one this shallow is wet (1)
WET_QUIET_DB = 12.0          # the stereo evidence: frames this far under the peak
WET_SIDE_FULL = 0.4          # whose median side share this high is wet (0.5 = no correlation)
WET_FALL_WEIGHT = 0.6        # the fall's share of the answer when there is stereo


def _np():
    from acidcat.core.analysis.pcm import _np as np
    return np()


def _frame_energy(np, x, n, q):
    """sum of squares over the n analysis frames, on a signal q times the
    analysis rate. No filter: the energy of a frame does not need one"""
    c = np.concatenate([[0.0], np.cumsum(x * x)])
    starts = HOP * q * np.arange(n)
    ends = np.minimum(starts + FRAME * q, len(x))
    return c[ends] - c[starts]


def curves(x, sr, side=None):
    """Per-frame curves for a mono signal: level in dB, per-band power, and
    the power-weighted spectral centroid; with `side` ((left - right) / 2 at
    the same rate) also each frame's share of side energy. None for a signal
    too short to frame, or silent."""
    np = _np()
    from acidcat.core.analysis.pitch import decimate
    x0 = np.asarray(x, dtype=np.float64)[: int(MAX_SECONDS * sr)]
    rate = sr
    x, sr = decimate(x0, sr, RATE_MAX)
    if len(x) < FRAME or not np.any(x):
        return None
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    power = np.abs(np.fft.rfft(x[idx] * np.hanning(FRAME)[None, :], axis=1)) ** 2
    total = power.sum(axis=1)
    freqs = np.fft.rfftfreq(FRAME, 1.0 / sr)
    out = {
        "fps": sr / HOP,
        "level_db": 10 * np.log10(np.maximum(total, 1e-20)),
        "bands": np.stack([power[:, (freqs >= lo) & (freqs < hi)].sum(axis=1)
                           for _, lo, hi in BANDS], axis=1),
        "centroid": (power * freqs[None, :]).sum(axis=1) / np.maximum(total, 1e-20),
    }
    if side is not None:
        q = max(1, rate // sr)
        es = _frame_energy(np, np.asarray(side, dtype=np.float64)[:len(x0)], n, q)
        em = _frame_energy(np, x0, n, q)
        # identical channels are mono, not a dry stereo image
        if es.max() > 1e-12 * em.max():
            out["side_share"] = es / np.maximum(em + es, 1e-30)
    return out


def wetness(c, ends_silent=True):
    """0 (dry) to 1 (wet) from `curves` output; see the module doc.
    `ends_silent` says the file's end is silence (a one-shot stops there);
    False holds the last level past it (a loop goes round again)."""
    np = _np()
    lv = c["level_db"]
    peak = float(lv.max())
    k = max(2, int(round(WET_WINDOW_S * c["fps"])))
    sm = np.convolve(np.pad(lv, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    sm = np.maximum(sm, peak - WET_FLOOR_DB)
    after = peak - WET_FLOOR_DB if ends_silent else sm[-1]
    ext = np.concatenate([sm, np.full(k, after)])
    n = len(sm)
    lowest = np.min(np.stack([ext[j:j + n] for j in range(1, k + 1)]), axis=0)
    start = sm > peak - WET_FROM_DB
    fall = float((sm - lowest)[start].max()) if start.any() else 0.0
    w = min(1.0, max(0.0, (WET_DRY_DB - fall) / (WET_DRY_DB - WET_WET_DB)))
    share = c.get("side_share")
    if share is not None:
        quiet = (lv > peak - ACTIVE_DB) & (lv <= peak - WET_QUIET_DB)
        if quiet.any():
            st = min(1.0, float(np.median(share[quiet])) / WET_SIDE_FULL)
            w = WET_FALL_WEIGHT * w + (1 - WET_FALL_WEIGHT) * st
    return round(w, 3)


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
    # a pulse is a loop, and a loop's end runs back into its start
    d["wetness"] = wetness(c, ends_silent=d["envelope"] != "pulse")
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
    """The profile of a file or an array (mono, or channels on the shorter
    axis), or None when there is nothing to measure (empty, silent,
    non-finite, shorter than one frame)."""
    np = _np()
    if isinstance(path_or_array, (str, bytes)) or hasattr(path_or_array, "__fspath__"):
        from acidcat.core.analysis.pitch import load_planes
        planes, sr = load_planes(path_or_array)
    else:
        if sr is None:
            raise ValueError("sr is required with an array")
        x = np.asarray(path_or_array, dtype=np.float64)
        if x.ndim == 2 and x.shape[1] < x.shape[0]:
            x = x.T
        planes = list(x) if x.ndim == 2 else [x]
    x = np.mean(np.stack(planes), axis=0) if len(planes) > 1 else planes[0]
    if len(x) == 0 or not np.all(np.isfinite(x)):
        return None
    # side is (left - right) / 2 so that it and the mono mix share a scale
    side = (planes[0] - planes[1]) / 2 if len(planes) == 2 else None
    c = curves(x, sr, side)
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
