# Rhythm and BPM

How acidcat estimates beats-per-minute from an audio waveform: onset
detection, autocorrelation of the onset envelope, beat tracking, and
the validation pipeline that catches librosa's common octave-error
failure mode.

Last updated: 2026-04-23

---

## What it is

**Tempo** is the rate at which musical beats occur, measured in beats
per minute (BPM). For a 4-on-the-floor kick at 128 BPM, there are
128 kick hits per minute and the inter-onset interval is 60000/128 =
469 milliseconds.

**Beat tracking** is the problem of identifying the time position of
each individual beat, not just the rate. For acidcat's indexing
purposes we mostly care about the aggregate tempo, but beat positions
are also returned by `librosa.beat.beat_track` and exposed as
`beat_count` in the feature vector.

---

## The pipeline

Three conceptual steps:

```
    audio waveform
          │
          ▼
    onset strength envelope      (continuous curve peaking at note starts)
          │
          ▼
    autocorrelation / tempogram  (which inter-onset intervals are common)
          │
          ▼
    beat tracking                 (fit a periodic pulse to the onsets)
          │
          ▼
    BPM + beat times
```

Each step has well-understood math and well-understood failure modes.

---

## Step 1: onset strength

An **onset** is a perceptual event in audio, usually corresponding to
the start of a note, a drum hit, or any sudden spectral change. The
onset strength envelope is a one-dimensional function of time whose
value says "how strong is the onset at this time."

### Spectral flux

The core observation: an onset causes a sudden increase in spectral
energy across many frequency bands at once. Spectral flux is the sum
of positive frame-to-frame differences in a spectrogram:

```
    F[t] = sum over k of max(0, |X[t, k]| - |X[t-1, k]|)
```

where `X[t, k]` is the STFT magnitude at time frame `t`, frequency bin
`k`. Only positive differences contribute, so decreasing energy
(decay tails) doesn't add to the onset signal.

### Log-compression

Perceptual loudness is roughly logarithmic, so raw spectral flux
over-weights high-energy passages and under-weights quiet onsets.
librosa's default applies `log(1 + X)` compression before the flux
computation, equalizing the dynamic range.

### Mel weighting

librosa uses a mel-scaled filter bank rather than linear frequency
bins. This concentrates resolution where human hearing is most
sensitive (roughly 500 Hz - 4 kHz) and gives less weight to extreme
lows (where fundamentals live) and extreme highs (where cymbal wash
lives). For beat tracking, this emphasizes the mid-range where most
rhythmic information actually sits.

### The final envelope

After the above, `librosa.onset.onset_strength(y=y, sr=sr)` returns a
one-dimensional float array sampled at the hop rate (default
hop_length=512 at sr=44100 gives 86.13 Hz, so ~11.6ms per sample).

Peaks in this envelope correspond to perceptual onsets. Troughs are
gaps between events.

---

## Step 2: autocorrelation for tempo

Given the onset envelope, how do we extract a BPM?

### The autocorrelation approach

Autocorrelation at lag `τ` measures how similar the envelope is to a
version of itself shifted by `τ`:

```
    R[τ] = sum over t of F[t] * F[t + τ]
```

If the envelope has a periodic component with period `τ_0`, then `R[τ]`
will peak at `τ_0`, `2·τ_0`, `3·τ_0`, etc. The fundamental peak is the
inter-beat interval. Convert to BPM:

```
    BPM = 60 * frame_rate / τ_peak
```

where `frame_rate = sr / hop_length`.

### The tempogram

Rather than a single autocorrelation, librosa computes a **tempogram**:
autocorrelation of the onset envelope within a sliding analysis window,
giving a 2D (tempo, time) matrix. This allows for tempo changes over
time and produces robust estimates even when parts of the signal
violate the beat grid (fills, breaks, silence).

`librosa.beat.tempo` aggregates the tempogram into a single BPM
estimate (or a per-frame array). By default it uses a prior that
favors tempos near 120 BPM to break ties between octave multiples.

### Octave ambiguity

The autocorrelation has peaks at every integer multiple of the true
inter-beat interval. If the true tempo is 128 BPM, there are peaks at:

```
    128 BPM (every beat)
     64 BPM (every 2 beats)
     32 BPM (every 4 beats)
    256 BPM (every half-beat, subdivision)
```

Deciding which peak is the "real" tempo is the hard part. Octave
errors are the #1 failure mode of beat trackers. More on this below.

---

## Step 3: beat tracking

With a tempo estimate in hand, beat tracking places individual beats
at consistent intervals aligned to the onset envelope. This is a
constrained optimization: pick the set of time points that maximize
onset strength at those points while maintaining near-uniform spacing
matching the tempo estimate.

`librosa.beat.beat_track` uses dynamic programming to solve this. The
output is a list of frame indices at which beats fall, convertible to
seconds via `librosa.frames_to_time`.

For acidcat's current use this is mostly interesting as a count
(`beat_count` in the feature vector). Per-beat timing gets important
only for future loop-alignment features.

---

## acidcat's implementation

In `core/detect.py`:

```python
detected_bpm = None
try:
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempos_1 = librosa.beat.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
    tempos_2 = librosa.beat.tempo(y=y, sr=sr, aggregate=None)
    all_tempos = []
    if tempos_1.size > 0:
        all_tempos.extend(tempos_1)
    if tempos_2.size > 0:
        all_tempos.extend(tempos_2)
    if all_tempos:
        detected_bpm = round(float(np.median(all_tempos)), 2)
except Exception:
    pass
```

Two observations worth explaining:

### Why two calls to `librosa.beat.tempo`

`tempos_1` uses the pre-computed onset envelope. `tempos_2` re-runs
the default onset detection internally. These two paths produce
slightly different estimates because librosa's default onset detector
has evolved over versions and the internal path applies additional
smoothing the explicit path doesn't.

Taking the median of both hedges against either one being off. For
clearly-tempoed material they agree closely; for ambiguous material
the median damps out outliers.

`aggregate=None` asks for the full per-frame tempo array rather than
a single summary. We then feed all values into one big median.

### Why median of arrays, not means

Same reasoning as chroma: median is robust to outlier frames where
the tempo estimator briefly goes haywire (breaks, silence, fills).
Mean pulls toward those outliers.

In `core/features.py` the approach is different:

```python
onset_env = librosa.onset.onset_strength(y=y, sr=sr)
tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
features['tempo_librosa'] = float(np.atleast_1d(tempo)[0])
features['beat_count'] = len(beats)
```

Here we use `beat_track` instead of `tempo`, and take the scalar tempo
output. This is used for ML feature vectors where a single number is
needed, not for final BPM estimation.

---

## The octave-error problem in detail

Beat trackers often report half-time or double-time relative to the
perceptual tempo. Why this happens:

### Emphasis of subdivision vs beat

In a 120 BPM hip-hop track, the kick hits on beats 1, 2, 3, 4 but the
hi-hat often hits on every eighth (240 hits per minute). A beat tracker
that weights onset energy across the full frequency range can lock
onto the hi-hat rate, reporting 240 BPM.

### Emphasis of the backbeat

In rock and pop, the snare backbeat on beats 2 and 4 is often the
loudest onset. A tracker can lock onto those two hits as the
fundamental period (every 2 beats), reporting 60 BPM for a 120 BPM
track.

### Triplet feel

In swung or triplet material (jazz, shuffle, dilla beats), the
subdivision is three per beat, not two. A tracker can lock onto the
triplet, reporting 3/2 or 2/3 of the correct tempo.

### Ambient / drone / minimal percussion

With few or no percussive onsets, the tempogram becomes noisy and the
estimate unreliable. Results can be anywhere.

---

## The validation pipeline

Given the known failure modes, acidcat doesn't trust librosa's output
naively. In `core/detect.py`:

```python
def validate_and_improve_bpm(detected_bpm, filename_bpm, confidence_threshold=20):
    if filename_bpm is None:
        return detected_bpm, 'detected'
    if detected_bpm is None:
        return filename_bpm, 'filename'
    if not (60 <= detected_bpm <= 200):
        return filename_bpm, 'filename'

    diff = abs(detected_bpm - filename_bpm)

    if diff <= confidence_threshold:
        return detected_bpm, 'detected'
    if abs(detected_bpm * 2   - filename_bpm) <= confidence_threshold:
        return detected_bpm * 2, 'corrected'
    if abs(detected_bpm / 2   - filename_bpm) <= confidence_threshold:
        return detected_bpm / 2, 'corrected'
    if abs(detected_bpm * 1.5 - filename_bpm) <= confidence_threshold:
        return detected_bpm * 1.5, 'corrected'
    if abs(detected_bpm / 1.5 - filename_bpm) <= confidence_threshold:
        return detected_bpm / 1.5, 'corrected'

    return filename_bpm, 'filename'
```

The logic walks through candidate corrections in order of likelihood.

### Example: 140 BPM track that librosa reports as 70

```
detected_bpm = 70
filename_bpm = 140
confidence_threshold = 20

diff = |70 - 140| = 70           not within 20
|70*2 - 140| = 0                 within 20! use 70*2 = 140
```

Result: `(140, 'corrected')`. The octave error is caught.

### Example: 120 BPM track that librosa reports as 240

```
detected_bpm = 240
filename_bpm = 120

240 is outside [60, 200]  -> reject, use filename
```

Result: `(120, 'filename')`. Range check catches the hi-hat lock.

### Example: 90 BPM track that librosa reports as 60

```
detected_bpm = 60
filename_bpm = 90

diff = 30                          not within 20
|60*2 - 90| = 30                   not within 20
|60/2 - 90| = 60                   not
|60*1.5 - 90| = 0                  within 20! use 60*1.5 = 90
```

Result: `(90, 'corrected')`. Triplet-style error caught.

### Example: librosa reports 124 on a 125 BPM track

```
detected_bpm = 124
filename_bpm = 125

diff = 1                           within 20, accept detected
```

Result: `(124, 'detected')`. Small normal disagreement accepted.

### The thresholds

`confidence_threshold = 20` is a deliberate choice:

- Large enough to accept small tracking jitter (a few BPM either way)
- Small enough that a truly different tempo (say 120 vs 140)
  triggers the octave corrections rather than blindly accepting
- Tight enough that a filename BPM of 125 and detected BPM of 150
  fails to fit any of the ratios (150-125 = 25 > 20, and no ratio
  lands within 20 either) and falls back to filename

The `(60, 200)` range bounds the detected BPM to musically plausible
values. Anything outside is treated as a failure mode.

### Source tag

Every outcome carries a source label: `detected` (librosa agreed or
no filename present), `filename` (filename used), `corrected`
(filename + octave-ratio adjustment), `oneshot` (too short to
analyze). The label gets written to the DB via the future
`bpm_source` column so later queries can filter by confidence.

---

## What the ranges catch and what they don't

### Caught

- **Octave errors**: 2x, 0.5x, 1.5x, 0.67x ratios.
- **Extreme librosa outputs**: anything below 60 or above 200 BPM.
- **Small tracking jitter**: ±20 BPM from filename.

### Not caught (yet)

- **3x errors**: librosa reporting 40 on a 120 track because it locked
  onto quarter-bars. Could add `abs(detected*3 - filename) <= threshold`
  and `abs(detected/3 - filename) <= threshold`.
- **No-filename cases**: if the user renames a file, deleting the BPM
  from the filename, we lose the validation signal and must trust
  librosa blindly.
- **Perceptual disagreements**: sometimes a 170 BPM filename and 85
  detected is the intended tempo (half-time feel). No way to
  disambiguate without musical context.

### Future improvement: chunk-authored BPM

ACID chunks and smpl chunks carry producer-authored BPM in many cases.
When a `bpm` value comes from `core/riff.py:parse_riff` (an ACID chunk),
it should be treated as ground truth and used to validate librosa the
same way filename_bpm currently does. Source label `chunk` beats
`filename` which beats `detected`.

---

## Edge cases and their handling

### One-shots

Samples shorter than 256 samples (about 6ms at 44.1k) get marked as
one-shots with no tempo. In `detect.py`:

```python
if len(y) < 256:
    return {
        "estimated_bpm": "oneshot",
        "estimated_key": None,
        "duration_sec": duration_sec,
        "bpm_source": "oneshot",
        "key_source": None,
    }
```

This is aggressive (some legitimate one-shot hits are longer than
256 samples), but it prevents librosa from running beat tracking on
material where the output is guaranteed meaningless.

A smarter heuristic would use duration AND `acid_beats`: if the file
is under ~1 second and has no ACID beats, it's almost certainly a
one-shot. The current threshold could be raised to ~0.5 seconds
(22050 samples at 44.1k) without introducing false positives.

### Silence or near-silence

librosa's beat tracker on a silent buffer typically returns 0 or
NaN. The outer try/except catches this and falls through to the
filename-only path. Nothing special needed.

### Constant drone

Sustained tonal content with no attack events produces an onset
envelope that's essentially zero. Beat tracker picks arbitrary
intervals. Same handling as silence: range check or validation
catches it.

### Time-varying tempo

Tracks that accelerando or rallentando within a single file confuse
single-number tempo estimates. librosa's `aggregate=None` path
returns a per-frame array; acidcat medians over that, which
produces a reasonable "middle" tempo for mildly-varying material.
Severely-varying material gets a noisy estimate.

### Very fast or very slow tempos

The `[60, 200]` range cuts off legitimate extremes. Drum-and-bass at
170-180 BPM fits; gabber at 200+ BPM doesn't. Ambient or classical
at 40-50 BPM doesn't. Raising the bounds introduces more false
positives from hi-hat lock (on the high end) and long-interval
errors (on the low end), so the tradeoff isn't obvious.

A future refinement: auto-widen the range when the filename BPM
falls outside, since then we have corroborating evidence for the
unusual value.

---

## Feature vector: `tempo_librosa` and `beat_count`

In `core/features.py` these are used for ML similarity, not for the
canonical BPM of the indexed row:

```python
onset_env = librosa.onset.onset_strength(y=y, sr=sr)
tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
features['tempo_librosa'] = float(np.atleast_1d(tempo)[0])
features['beat_count'] = len(beats)
```

The canonical BPM (the `bpm` column in the `samples` table) goes
through the validation pipeline. `tempo_librosa` does not; it's the
raw librosa output. These two values can disagree and that's fine for
their respective purposes:

- `bpm`: what you search by, correctness matters
- `tempo_librosa`: a feature for ML comparison, internal consistency
  matters more than correctness (as long as the same extractor
  produced all values)

`beat_count` is useful as a loop-vs-one-shot signal in combination
with `duration`. A 4-second clip with `beat_count=8` is almost
certainly a 2-bar loop at 120 BPM. A 4-second clip with `beat_count=0`
or `1` is probably a pad, drone, or one-shot.

---

## Gotchas

### Onset detection in distorted / heavy material

Heavy distortion or saturation can blur onset transients. Rock guitar
power-chord strums with long sustain and overdrive often have weak
onset signatures. Beat tracking suffers accordingly.

Fix: use harmonic-percussive separation first. `librosa.effects.hpss`
splits the signal into a harmonic and a percussive component; running
onset detection on just the percussive part improves results for
heavy material.

### Very clean sine-wave content

A continuous sine wave has no onsets. If the material is a single
sustained tone or a slow sine sweep, beat tracking returns nothing
useful. Pipeline falls back to filename.

### Swing and microtiming

Heavy swing (jazz shuffle, late-period Dilla drum programming) pushes
onsets off the grid. Beat tracker still works in the aggregate but
the inter-beat intervals are uneven. BPM estimate is still
meaningful; beat positions are approximate.

### Tempo-multiplexed material

A track that layers patterns at different rates (e.g. a 128 BPM house
beat with a 96 BPM melodic part on top) produces a bimodal
autocorrelation. Result is often a tempo that's a compromise between
the two.

### Librosa version differences

`librosa.beat.tempo` behavior changed between 0.8 and 0.10. The
`aggregate=None` parameter we pass returns a per-frame array in 0.10
but returned a different shape in earlier versions. If upgrading
librosa, re-test on a known-tempo corpus.

---

## Interpretation

A BPM value rounded to 2 decimal places. Common clean values:

```
60.0          very slow (ballad, dub)
80.0 - 100    mid-tempo
120.0 - 128   house, techno
140.0         common dubstep, trap
150.0 - 160   footwork, juke
170.0 - 180   drum and bass
```

Values with non-integer precision often indicate a live-recorded or
time-stretched source where the tempo isn't locked to a grid.
Sample-pack content tends to report round integers because producers
sequence to a fixed grid.

`beat_count` interpretation:

```
0 or 1        no detectable beats -> one-shot or drone
4             one bar of 4/4 or half-bar of 8/4
8             two bars of 4/4 (classic 1-bar loop in a 2-bar sample)
16            four bars of 4/4 (typical loop length in modern production)
32+           long loop, phrase, or full track
```

Combined with `duration` this gives implied tempo via
`beat_count / (duration / 60)`, which should match the canonical
`bpm`. Disagreements indicate the beat tracker lost count.

---

## References

- Bello, J. P., Daudet, L., Abdallah, S., Duxbury, C., Davies, M., &
  Sandler, M. B. (2005). "A tutorial on onset detection in music
  signals." *IEEE Transactions on Speech and Audio Processing*
  13(5): 1035-1047. Canonical survey of onset detection methods.
- Ellis, D. P. W. (2007). "Beat tracking by dynamic programming."
  *Journal of New Music Research* 36(1): 51-60. The algorithm
  librosa's beat tracker is based on.
- Grosche, P., & Müller, M. (2011). "Extracting predominant local
  pulse information from music recordings." *IEEE Transactions on
  Audio, Speech, and Language Processing* 19(6): 1688-1701.
  Introduces the PLP (predominant local pulse) method used in
  newer librosa versions.
- Schreiber, H., & Müller, M. (2018). "A single-step approach to
  musical tempo estimation using a convolutional neural network."
  *ISMIR 2018*. CNN-based tempo estimation, often more accurate
  than autocorrelation-based methods. Not currently used in
  acidcat but a possible future upgrade.
- librosa documentation: <https://librosa.org/doc/latest/>
- See `chroma_and_key.md` for the tonal side of the analysis
  pipeline.
- See `camelot_wheel.md` for how key output feeds harmonic matching.
