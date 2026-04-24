# Onset Detection

Finding where in time "something happens" in an audio signal. The
input to beat tracking, rhythmic analysis, and any slicing or
segmentation task. The `rhythm_and_bpm.md` doc covers how onsets
feed tempo estimation; this one goes deeper on how the onsets
themselves are computed.

Last updated: 2026-04-23

---

## What it is

An **onset** is the perceptual start of a musical event. In most
common sense: the attack of a note, the hit of a drum, the pluck
of a string. More formally, it's the moment at which a new energy
pattern becomes perceptually salient.

Onset detection has two outputs:

- **Onset strength envelope**: a continuous one-dimensional
  function of time whose value is high at candidate onset moments
  and low otherwise. This is what librosa's
  `onset.onset_strength` returns.
- **Onset times**: discrete times picked from peaks in the onset
  strength envelope. This is what `onset.onset_detect` returns.

acidcat uses the envelope directly for BPM estimation (via
`beat.tempo` or `beat.beat_track`), but the discrete times are
useful for slicing, segmentation, and "count the hits" queries.

---

## The general approach

```
    audio waveform
          │
          ▼
    short-time spectral analysis    (STFT or mel-STFT)
          │
          ▼
    detection function              (some form of "change" signal)
          │
          ▼
    optional post-processing        (normalize, smooth)
          │
          ▼
    onset strength envelope         (continuous)
          │
          ▼
    peak picking                    (adaptive threshold + local max)
          │
          ▼
    onset times                     (discrete list)
```

The interesting part is the **detection function**: what measure of
"change in the spectrum" best captures onsets.

---

## Detection function variants

### Simple spectral flux

The baseline. For each time frame `t` and frequency bin `k`,
compute how much louder the spectrum got compared to the previous
frame:

```
    flux_simple[t] = sum over k of max(0, |X[t, k]| - |X[t-1, k]|)
```

Only positive differences contribute. Decreasing energy (release
tails of notes) doesn't add to the onset signal.

Properties:

- Cheap to compute.
- Works well for drum hits and strongly-articulated notes.
- Fails for soft onsets (legato passages, glissando, crossfades).

### Log-amplitude spectral flux

Take log of magnitudes before the difference:

```
    flux_log[t] = sum over k of max(0, log(|X[t, k]| + 1) - log(|X[t-1, k]| + 1))
```

Log compression equalizes the dynamic range. A quiet onset
contributes similarly to a loud onset. Without log, loud onsets
dominate and quiet ones get lost.

This is librosa's default.

### Mel-weighted spectral flux

Instead of linear frequency bins, use mel-scaled filter bank
outputs. The mel scale compresses the frequency axis
perceptually; mid-range frequencies (where most rhythmic content
lives) get emphasis, extreme lows and highs get less.

```
    mel_spec = librosa.feature.melspectrogram(y=y, sr=sr)
    mel_log = librosa.power_to_db(mel_spec)
    flux[t] = sum over m of max(0, mel_log[m, t] - mel_log[m, t-1])
```

More robust than linear-frequency flux for music because it
de-emphasizes low-frequency noise and high-frequency cymbal wash
that can mask onsets.

### Complex spectral difference

Instead of just magnitude difference, include phase information:

```
    D[t, k] = |X[t, k] * exp(-jφ_predicted[t, k]) - X[t, k]|
```

Where `φ_predicted` is the phase expected if the spectrum had
evolved smoothly from the previous frame (based on phase advance
from adjacent frames' frequency).

For a pure sustained tone, phase advances predictably between
frames, so the complex difference is small. For an onset, phase
changes abruptly, so the complex difference is large. This catches
soft onsets that pure magnitude methods miss.

More expensive but more accurate for pitched material with gentle
attacks.

### Superflux

A variant of spectral flux that uses the maximum filter across
frequency bins:

```
    superflux[t, k] = max(0, mag[t, k] - max(mag[t-1, k-1:k+1]))
```

The inner `max` looks across a small frequency window. Effect:
vibrato, pitch-bend, and frequency modulation that would normally
create spurious onsets in pure spectral flux get smoothed out. Real
onsets still produce peaks.

Good for sustained pitched material with ornamentation. Available
in madmom but not in librosa core.

### Phase-based methods

Onset detection using only phase information, no magnitude. Phase
changes abruptly at onsets because the signal transitions from one
predictable phase evolution to another. Works well for quiet
onsets but is noisy and rarely used alone; usually combined with
magnitude methods.

### Energy-based methods

Just look at the RMS envelope and pick its derivatives. Simplest
possible method. Works for percussive material but fails for
everything else because sustained tones change energy only during
attack and release.

---

## librosa's implementation

### `onset_strength`

```python
librosa.onset.onset_strength(y=y, sr=sr,
                              S=None,               # optional pre-computed mel-spectrogram
                              n_fft=2048,
                              hop_length=512,
                              aggregate=np.mean,    # how to aggregate bands
                              feature=librosa.feature.melspectrogram,
                              center=True,
                              detrend=False)
```

Default pipeline (when `S=None`):

1. Compute mel spectrogram (`n_mels=128`).
2. Take log: `librosa.power_to_db`.
3. Compute positive first-order difference along time axis.
4. Aggregate across mel bands via `np.mean`.
5. Return 1D array sampled at frame rate.

Output shape: `(T,)` where T is number of frames.

Frame rate: `sr / hop_length`. At 44.1k/512 = 86.1 Hz, each sample
represents ~11.6ms.

### `onset_detect`

```python
librosa.onset.onset_detect(y=y, sr=sr,
                            onset_envelope=None,
                            units='frames',
                            pre_max=0.03 * sr // hop_length,   # 30ms
                            post_max=0.00 * sr // hop_length,
                            pre_avg=0.10 * sr // hop_length,   # 100ms
                            post_avg=0.10 * sr // hop_length,
                            delta=0.07,
                            wait=0.03 * sr // hop_length)       # 30ms
```

Picks peaks from the onset envelope using adaptive thresholding.
Parameters control the peak-picking behavior (covered below).

Returns array of frame indices (or seconds, or samples, depending
on `units`).

---

## Peak picking

The onset strength envelope has peaks at candidate onsets and dips
between. Not every peak is a real onset: small peaks are noise, and
large peaks within a few ms of a previous onset are echoes or
partial re-articulations.

### Local maximum requirement

A point `t` is a candidate peak iff it's the maximum within a
window `[t - pre_max, t + post_max]`. This handles noise that
creates small fluctuations; a real peak must dominate its
neighborhood.

### Adaptive threshold

A point is only kept as an onset iff it exceeds a running local
mean by some delta:

```
    threshold[t] = mean(envelope[t - pre_avg : t + post_avg]) + delta
    if envelope[t] > threshold[t]: keep as onset
```

`delta` is an absolute offset. Higher delta = fewer, more confident
onsets. Lower delta = more onsets including false positives.

### Refractory period

After detecting an onset at `t0`, no new onset is allowed within
`wait` frames. Prevents double-detection of a single event.

Default `wait = 30ms` prevents a drum hit from being detected
twice due to secondary excitation of the drum's body resonance.

### Tuning for different material

Defaults are general-purpose. For specific needs:

- **Dense polyphony**: reduce `delta` (allow more onsets), increase
  `pre_avg` and `post_avg` (smoother threshold).
- **Sparse percussion**: increase `delta` (require stronger peaks).
- **Fast drums**: decrease `wait` (allow closer-together detections).
- **Legato material**: decrease `delta` aggressively or use the
  complex spectral difference method.

---

## acidcat usage

Currently in two places:

### `core/detect.py` (BPM pipeline)

```python
onset_env = librosa.onset.onset_strength(y=y, sr=sr)
tempos_1 = librosa.beat.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
tempos_2 = librosa.beat.tempo(y=y, sr=sr, aggregate=None)
```

Uses onset envelope as input to tempo estimation. The envelope
drives the autocorrelation that finds periodicity.

### `core/features.py` (feature extraction)

```python
onset_env = librosa.onset.onset_strength(y=y, sr=sr)
tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
features['tempo_librosa'] = float(np.atleast_1d(tempo)[0])
features['beat_count'] = len(beats)
```

The `beats` array is the output of beat tracking, which internally
uses peak picking on the onset envelope.

### What's not exposed

Currently acidcat doesn't expose the raw onset times to the index
or the MCP surface. Two opportunities:

- **Onset density** as an index field: `onsets_per_second`. Useful
  for kind inference (drums have high density, pads low) and for
  search ("find me busy, percussive material").
- **`detect_onsets(path)` MCP tool**: return the list of onset
  times for a file. Useful for slicing workflows, rhythmic analysis,
  and agent-driven "what's happening in this file" questions.

Both would be small additions. Neither changes the architecture.

---

## Interpretation

### The envelope shape

```
    onset strength
     ^
     |   ___                    ___
     |  |   \                  |   \
     |  |    \                 |    \___
     |__|     \________________|         \____
     |                                         
     +─────────────────────────────────────> time
        onset 1                  onset 2
```

Sharp peaks at the hits, near-zero between. Duration and shape of
each peak depend on the attack characteristics and the window
size.

For a clean kick drum: envelope rises from 0 to peak in 1-2 frames
(~12-24ms), falls over 5-10 frames (~60-120ms). Very clean peak.

For a soft pad swell: envelope rises smoothly over 30-50 frames
(~350-600ms), with no clear single peak. Peak picking has trouble
here; the complex spectral difference method works better.

### Counting onsets

Given onset times, the count per unit time tells you:

- **0-1 onsets/second**: drone, sustained note, very slow material.
- **1-3 onsets/second**: ambient, slow melody, soft percussion.
- **3-6 onsets/second**: moderate rhythmic content, mid-tempo drums.
- **6-12 onsets/second**: busy percussion, fast ornamentation, fills.
- **12+ onsets/second**: very dense material, noise textures.

### Onset patterns

Periodic onsets at regular intervals indicate rhythmic content
(loops, drums, ostinato). Irregular onsets indicate melodic or
ornamental content.

Inter-onset interval (IOI) histograms show the rhythmic structure:

```
    IOI count
     ^
     |       |       
     |       |       
     |       |        |
     |__|____|__|_____|____|_____ IOI (ms)
        125  250      500  1000
        
    drum beat       1/2 beat       2 beats
```

Peaks at multiples of a base interval indicate a clear meter. A
flat IOI distribution indicates unmetered content.

---

## Gotchas

### Window length tradeoff

Larger `n_fft` = better frequency resolution but worse time
resolution. For onset detection you want sharp time resolution, so
smaller `n_fft` (512, 1024) is often better than the 2048 default.

librosa's onset_strength default uses 2048. Overriding to 1024
typically produces slightly tighter onset detection on percussive
material at the cost of some noise on sustained material.

### Sensitivity parameter (`delta`)

The single biggest lever for tuning peak picking. Wrong value is
the most common cause of bad onset detection.

Too low: many false positives (detecting noise or slight
modulations as onsets).

Too high: missing real onsets, especially quiet ones.

Default 0.07 works for many cases but not all. For drum-heavy
material, 0.15 is often better. For pad-heavy material with soft
onsets, 0.04 is better. No one value is correct for all material.

### Attack vs note start

The "onset" detected by these algorithms is the audible attack,
which is slightly after the actual note start. The attack is what
the ear responds to, and that's what the envelope picks up.

For some applications (MIDI transcription, score alignment), you
want the note start, which is earlier. Heuristic: subtract 5-10ms
from each detected onset.

For BPM and rhythmic analysis, attack time is the right thing.

### Polyphonic onset smearing

When multiple events happen within a few ms (e.g. a drum fill's
last two hits), they can blur into a single peak. Peak picking
with a low `wait` setting can separate them, but at the cost of
more false positives elsewhere.

Neural onset detectors (madmom's RNN-based detector) handle this
much better. For acidcat's scope, the librosa approach is
"good enough."

### Frequency-specific onsets

A bass-heavy sample with kicks and a treble-heavy sample with
hi-hats produce different onset envelopes when analyzed with a
broadband method. If you care specifically about one frequency
range, run onset detection on the mel-spectrogram restricted to
that range:

```python
S = librosa.feature.melspectrogram(y=y, sr=sr, fmin=50, fmax=200)
onset_env = librosa.onset.onset_strength(S=S, sr=sr)
# bass-only onset envelope
```

Useful for separating kick onsets from snare onsets on full-mix
drum loops.

### Edge effects

The first and last frames of a file produce unreliable onset
values because the difference computation needs a previous frame.
librosa handles this by padding, but the first 2-3 frames'
onset values should be considered suspect.

Not a practical problem for samples longer than ~100ms but worth
knowing for very short clips.

---

## Advanced techniques worth knowing

### Madmom's RNN onset detector

Madmom is a music information retrieval library with a
recurrent-neural-network-based onset detector trained on
hand-labeled data. Much more accurate than librosa's signal
processing methods, especially for:

- Soft onsets
- Dense polyphonic material
- Non-standard instrumentation
- Noisy or low-quality audio

Cost: large model weights (hundreds of MB), slower inference,
additional dependency.

Integration approach if we ever added it: optional `[madmom]`
extra, use it only for `detect_onsets` and hi-quality beat
tracking, default remains librosa for speed.

### Multi-source onset fusion

Run multiple detection functions and combine their outputs:

```python
flux = librosa.onset.onset_strength(y=y, sr=sr,
                                     feature=librosa.feature.melspectrogram)
cplx = complex_onset(y, sr)
phase = phase_onset(y, sr)

fused = 0.5 * flux + 0.3 * cplx + 0.2 * phase
```

Different methods catch different onset types. Fusion improves
recall without sacrificing much precision, at the cost of 3x
compute.

### Rhythm-aware tracking

Some methods use a predictive model: once you've detected several
onsets at a regular interval, bias subsequent detection toward
expected beat positions. librosa's beat tracker does this via
dynamic programming. More sophisticated methods (PLP, particle
filters) can adapt more gracefully to tempo changes.

---

## Summary

Onset detection is a one-line call in acidcat but it's worth
understanding because:

1. The onset envelope is the foundation for all rhythmic analysis.
   Bad envelope = bad BPM, bad beat_count, bad tempogram.
2. The parameters have significant effect on accuracy but currently
   use librosa defaults.
3. Exposing onset density as an indexed feature would enable a
   whole category of queries we don't currently support.
4. A `detect_onsets` MCP tool would enable slicing and
   segmentation workflows.

Priority level: not urgent. Current defaults work well enough for
the typical sample library. But when we want to handle edge cases
(drone libraries, heavy ornamentation, soft-onset material), the
parameter tuning and alternative methods documented here are the
levers to reach for.

---

## References

- Bello, J. P., Daudet, L., Abdallah, S., Duxbury, C., Davies, M., &
  Sandler, M. B. (2005). "A tutorial on onset detection in music
  signals." *IEEE Transactions on Speech and Audio Processing*
  13(5): 1035-1047. Canonical overview of onset detection methods.
- Böck, S., & Widmer, G. (2013). "Maximum filter vibrato suppression
  for onset detection." *DAFx 2013*. Introduces superflux.
- Böck, S., Krebs, F., & Schedl, M. (2012). "Evaluating the online
  capabilities of onset detection methods." *ISMIR 2012*. Benchmark
  comparison of major methods including RNN-based.
- Lerch, A. (2012). *An Introduction to Audio Content Analysis:
  Applications in Signal Processing and Music Informatics*. IEEE
  Press. Chapter on onset detection covers the theoretical
  background.
- librosa onset documentation:
  <https://librosa.org/doc/latest/onset.html>
- madmom onset documentation (if we ever integrate it):
  <https://madmom.readthedocs.io/en/latest/modules/features/onsets.html>
- See `dsp/rhythm_and_bpm.md` for how the onset envelope feeds
  tempo estimation and beat tracking.
- See `dsp/hpr.md` for HPR preprocessing that gives cleaner onset
  envelopes on mixed content.
