# Chroma and Key Detection

How acidcat goes from a raw audio waveform to a single letter like "Am"
that can be fed into the Camelot wheel. Covers chroma vector theory, the
STFT vs CQT variants librosa offers, the current argmax-based key
estimator, and a proposed upgrade to Krumhansl-Schmuckler correlation.

Last updated: 2026-04-23

---

## What it is

A **chroma vector** is a 12-dimensional representation of the pitch
content in a short audio frame, one element per pitch class (C, C#, D,
... B). Each element says roughly "how much energy is in this pitch
class, regardless of octave."

A 10-second guitar riff in C major played across three octaves collapses
into a chroma matrix of shape `(12, T)` where `T` is the number of
analysis frames. Summing or taking the median across the time axis
gives a single 12-vector representing the pitch-class distribution of
the whole clip.

**Key detection** is the problem of taking that 12-vector and picking
the major or minor key whose characteristic pitch-class distribution it
most closely resembles. In principle this is a classification problem
over 24 classes (12 major + 12 minor keys).

---

## The math

### Pitch class as octave equivalence

The perceptual-musical fact chroma exploits: the pitches C3 (~130 Hz),
C4 (~261 Hz), and C5 (~523 Hz) all feel "the same note" to a listener.
Mathematically, two frequencies are the same pitch class iff their
ratio is a power of 2. Pitch class is frequency modulo octave.

Formally, given frequency `f` in Hz, the continuous pitch class is

```
    pc(f) = (12 * log2(f / 440) + 69) mod 12
```

which maps 440 Hz (A4) to pitch class 9 (A). Pitch class is a real
number in [0, 12); quantizing it to integers [0, 11] gives the 12
semitones.

### From spectrum to chroma

Given a spectrum `X[k]` where `k` indexes frequency bins of center
frequencies `f[k]`, the chroma vector `C[n]` for pitch class `n` is
the sum of spectral energy at all frequencies that fall into pitch
class `n`:

```
    C[n] = sum over k where round(pc(f[k])) == n  of  |X[k]|^2
```

In practice librosa uses a weighted mapping (not a hard bin assignment)
so that bin energy is split across adjacent pitch classes based on
distance. This is a triangular or Gaussian weighting in log-frequency
space.

### Why a 12-vector and not 12 separate analyses

The elegance is that all 12 pitch classes can be computed in one pass
over a single spectrum. The chroma operation is essentially a linear
projection from the frequency axis onto a 12-dimensional log-folded
axis. For a spectrum of length K, the operation is an `O(K)` sum.

---

## STFT vs CQT chroma

librosa provides two chroma extractors with different frequency-domain
backbones. acidcat uses both, in different places.

### chroma_stft

```
chroma = librosa.feature.chroma_stft(y=y, sr=sr)
```

Backbone: short-time Fourier transform. Linearly-spaced frequency bins
with constant bandwidth `sr / n_fft`. For `n_fft=2048, sr=44100`, bin
spacing is ~21.5 Hz.

The problem: musical pitch is logarithmic in frequency. A semitone at
A2 (110 Hz) spans ~6 Hz, while a semitone at A6 (1760 Hz) spans
~100 Hz. Linear STFT bins:

- over-resolve the high octaves: dozens of bins inside a single
  semitone, energy smeared across many tiny bins
- under-resolve the low octaves: one or two bins per semitone, pitch
  ambiguity at bass frequencies

For key detection on pitched content this asymmetry hurts. A low bass
note can get mis-classified because not enough frequency resolution
exists to separate adjacent semitones.

Where we use it: `core/features.py`, as part of the general feature
vector used for similarity scoring. Here the bias is acceptable
because all samples go through the same extractor and the feature is
used comparatively, not as an absolute pitch-class reading.

### chroma_cqt

```
chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
```

Backbone: constant-Q transform. Log-spaced frequency bins with constant
relative bandwidth. By construction, each semitone gets the same number
of bins regardless of octave. Low bass and high treble are equally
resolved in pitch.

The tradeoff is computational cost: CQT is slower than FFT-based
STFT. For per-file key detection this is fine; for real-time or
per-frame work it matters.

Where we use it: `core/detect.py:estimate_librosa_metadata`, as the
backbone for the key estimator. Key detection is the one place we
actually want absolute pitch-class accuracy, so the CQT's uniform
semitone resolution matters.

### Policy

| use case | choice | why |
|----------|--------|-----|
| key detection | `chroma_cqt` | uniform semitone resolution, accurate at low frequencies |
| feature vector for similarity | `chroma_stft` | cheaper, bias is acceptable when comparing like-to-like |

---

## Current key estimator (argmax of median chroma)

In `core/detect.py`:

```python
chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
if chroma.size > 0:
    chroma_median = np.median(chroma, axis=1)
    if np.any(chroma_median > 0):
        note_number = int(np.argmax(chroma_median))
        note_names = ["C", "C#", "D", "D#", "E", "F",
                      "F#", "G", "G#", "A", "A#", "B"]
        detected_key = note_names[note_number]
```

Three steps:

1. Compute a chroma matrix of shape `(12, T)` across the whole signal.
2. Median-aggregate along the time axis to get a single 12-vector.
3. Take the pitch class with the highest median energy as the key.

### Why median, not mean

Median is robust to transient spikes. A snare hit adds broadband
energy that briefly pushes every chroma bin up; the median ignores
those frames. Mean would be pulled toward the spikes. For a clip
dominated by a few chord tones, median captures the sustained
harmonic content and ignores the attack transients.

### What this estimator gets right

- Strongly tonic-heavy material (e.g. a bass loop that hammers the
  root note).
- Sustained pad sounds with clear harmonic content.
- Simple melodies centered on the tonic.

### What it gets wrong

- **Mode**: it always returns a letter with no `m` suffix. A piece in
  A minor and a piece in A major with the same tonic produce the same
  argmax output. acidcat currently reconciles this by combining with
  the filename-parsed key (which does carry mode), but if the filename
  has no key, we return major by convention.
- **Non-tonic heaviness**: a lot of modern music emphasizes the 5th
  or 3rd scale degree as loudly as the tonic. A piece in A minor where
  the E string drones can register as E, not A.
- **Borrowed chords**: a clip that spends time outside its home key
  (e.g. a funky passage with many extensions) can get dominant-pitch
  artifacts.
- **Inharmonic material**: drums, glitch textures, or noisy material
  produce essentially uniform chroma. The argmax is then meaningless;
  it picks whichever bin happens to edge out the others by tiny amounts.

The 40% or so accuracy we'd estimate for this algorithm on a diverse
sample library isn't bad for a one-line function, but there's a known
upgrade path.

---

## Proposed upgrade: Krumhansl-Schmuckler

Published in Krumhansl's *Cognitive Foundations of Musical Pitch*
(1990). The core idea: build 24 "key profiles" (one per major and minor
key), and pick the key whose profile best correlates with the
observed chroma vector.

### The profiles

Each profile is a 12-vector indexed by pitch class, where the value at
position `n` says "how characteristic is pitch class `n` of this key."
Krumhansl's original weights were derived from probe-tone experiments
(subjects rated how well each chromatic note fit a given tonal
context).

Canonical major profile (in C major, so pc 0 = C):

```
    C   C#   D    D#   E    F    F#   G    G#   A    A#   B
   [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
```

Canonical minor profile (in A minor, so pc 9 = A is the tonic):

```
    A   A#   B    C    C#   D    D#   E    F    F#   G    G#
   [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
```

Reading these: the tonic has the highest weight. The dominant (5th
above tonic) and median (3rd above tonic) are next. Scale tones in
general outrank chromatic non-scale tones.

To get the profile for a different key, the vector is rotated. Major
in G is the C major profile circularly shifted by 7 (G is 7 semitones
above C). Minor in D is the A minor profile shifted by -7 (D is 7
semitones below A, or equivalently 5 above).

### The algorithm

```
1. Compute observed chroma vector x (length 12), time-averaged.
2. For each of 24 keys k (12 major + 12 minor):
     a. Build profile p_k by rotating the canonical major or minor
        profile to put the tonic at the right pitch class.
     b. Compute Pearson correlation r(x, p_k).
3. Pick the key with the highest r.
```

Pearson correlation is the right similarity measure because it's
scale-invariant and mean-invariant. The observed chroma has arbitrary
absolute scale (depends on signal loudness); we only care about the
pattern.

### Why this outperforms argmax

- **Uses the whole pattern**, not just the peak. Ambiguous cases where
  two pitch classes have similar energy are resolved by checking which
  overall distribution fits better.
- **Models mode explicitly**. Major and minor profiles are distinct,
  so a piece in A minor is correctly distinguished from A major by the
  relative energies of C/C# (minor/major third of A) and the scale
  tones.
- **Robust to missing tonic**. If the tonic is quieter than the 5th
  (which happens often), the full-profile correlation still finds the
  right key because the rest of the scale pattern still fits.

### Implementation sketch

```python
_KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                      2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                      2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

def _correlate(x, p):
    # pearson correlation, stable form
    xm = x - x.mean()
    pm = p - p.mean()
    denom = np.sqrt((xm * xm).sum() * (pm * pm).sum())
    if denom == 0:
        return 0.0
    return float((xm * pm).sum() / denom)

def estimate_key_ks(chroma_median):
    best = (-2.0, None)   # correlation, (pc, mode)
    for pc in range(12):
        profile_maj = np.roll(_KS_MAJOR, pc)
        r_maj = _correlate(chroma_median, profile_maj)
        if r_maj > best[0]:
            best = (r_maj, (pc, 0))
        profile_min = np.roll(_KS_MINOR, pc)   # note: minor profile is rooted at A (pc 9)
        # rotate so tonic ends at pc
        rotated_min = np.roll(_KS_MINOR, pc - 9)
        r_min = _correlate(chroma_median, rotated_min)
        if r_min > best[0]:
            best = (r_min, (pc, 1))
    pc, mode = best[1]
    return pitch_class_to_name(pc, mode)
```

Costs: 24 correlations of 12-vectors per clip. Negligible compared to
the CQT itself.

### Variants worth knowing

- **Temperley-Kostka-Payne profiles** (Temperley 2004): re-derived
  from corpus statistics rather than probe tones. Slightly different
  weighting, often slightly better real-world accuracy.
- **Albrecht-Shanahan profiles** (Albrecht & Shanahan 2013): modern
  re-derivation from a large MIDI corpus, claimed to outperform
  Krumhansl and Temperley on classical, jazz, and rock test sets.
- **Smoothed profiles**: convolve the profile with a small Gaussian
  in pitch-class space to be more forgiving of off-scale notes.

Any of these drops in as a different constant vector. The algorithm
around it doesn't change.

---

## Chroma CQT parameters in detail

The `chroma_cqt` call in `detect.py`:

```python
chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
```

Relevant librosa defaults (as of librosa 0.10):

| parameter | default | what it controls |
|-----------|---------|------------------|
| `hop_length` | 512 | samples between frame centers; sets time resolution |
| `fmin` | C1 (~32.7 Hz) | lowest pitch the CQT analyzes |
| `n_chroma` | 12 | number of chroma bins per octave |
| `n_octaves` | 7 | number of octaves analyzed above fmin |
| `bins_per_octave` | `n_chroma * 3` = 36 | CQT resolution; 3 bins per semitone |
| `norm` | `2` | L2 normalization per frame |

Time resolution at `sr=44100, hop_length=512` is `512/44100 ≈ 11.6ms`
per frame. A 10-second clip gives ~860 frames of chroma.

`bins_per_octave = 36` means each semitone is resolved with 3 CQT bins.
This allows detection of microtonal deviations within a semitone (useful
in some modern production contexts) while still producing a 12-bin
chroma output after folding.

The default `fmin = C1` is low enough to catch sub-bass fundamentals
but not so low that anti-aliasing artifacts dominate. For material
heavier on high-frequency content (percussion, synths) this doesn't
matter much; for bass-heavy material the low fmin is crucial.

---

## Mode ambiguity: why we currently return only major

The argmax estimator has no access to mode information. It returns a
pitch class, period. In `detect.py`:

```python
note_names = ["C", "C#", "D", "D#", "E", "F",
              "F#", "G", "G#", "A", "A#", "B"]
detected_key = note_names[note_number]
```

No `m` suffix ever appears in the detected_key output. If the filename
contains a mode indication (`Am`, `F#min`), `improve_key_detection`
reconciles and uses the filename's mode. Otherwise the final stored
value is a bare letter, which Camelot interprets as major.

This is a known limitation. The Krumhansl upgrade resolves it by
returning `(pc, mode)` directly from the correlation.

---

## Why the two-source reconciliation works

acidcat combines analyzed + filename key via `improve_key_detection`:

```python
def improve_key_detection(detected_key, filename_key):
    if filename_key is None:
        return detected_key, 'detected'
    if detected_key is None:
        return filename_key, 'filename'
    if detected_key == filename_key:
        return detected_key, 'detected'
    return filename_key, 'filename'
```

When they agree, confidence is high. When they disagree, filename
wins. This is the right default because:

- Filenames in sample packs are authored by producers who know the
  key.
- Audio-detected keys can be wrong for all the reasons enumerated
  above.

The exception is when the filename parser itself fires a false
positive on a non-key token (e.g. "Analog" matching `A`). The
whole-token matcher `parse_bare_key_token` in `detect.py` guards
against this by requiring the key to be a standalone dot/dash/space
separated token, not a substring.

When the upgraded Krumhansl estimator lands, the reconciliation rule
should get smarter: if the estimator's best-fit correlation is below
some threshold (say 0.6), we should trust filename unconditionally
because the audio is too ambiguous to override it.

---

## Interpretation

A normalized chroma vector's values lie roughly in [0, 1]. A strongly
tonal sample produces a peaked distribution with a clear maximum
(often 2-3x the median). An inharmonic sample produces a nearly flat
distribution with all bins within ~20% of each other.

### Example chroma patterns

```
strongly-tonal C major:
       C   C#   D   D#   E    F   F#   G   G#   A   A#   B
     [1.0, 0.1, 0.5, 0.1, 0.7, 0.6, 0.1, 0.8, 0.1, 0.4, 0.1, 0.2]
      ^tonic                   ^5th      ^3rd

weakly-tonal / ambiguous:
     [0.5, 0.4, 0.6, 0.3, 0.5, 0.5, 0.4, 0.5, 0.4, 0.5, 0.3, 0.4]
      noticeable but not dominant peaks; argmax picks randomly

drum loop (inharmonic):
     [0.45, 0.42, 0.44, 0.47, 0.43, 0.46, 0.41, 0.48, 0.43, 0.45, 0.44, 0.46]
      effectively uniform; key detection meaningless

noise / glitch:
     [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
      perfectly flat after normalization; any key is equally wrong
```

### Quality heuristics

A **peakiness** measure like `max / median` distinguishes tonal from
non-tonal content:

```python
peakiness = chroma_median.max() / (np.median(chroma_median) + 1e-9)
```

Values above ~2.5 indicate strong tonality. Values near 1 indicate
effectively uniform distribution (drum hits, noise). A future
`key_confidence` field could expose this.

---

## Gotchas

### Transient-heavy material

A clip that's 80% transient (drum-heavy, percussive) has most of its
energy in broadband attacks, not in sustained pitch content. Chroma
analysis produces a nearly flat vector. The argmax output is
effectively random.

Fix options: use HPR (harmonic-percussive residual) separation before
chroma analysis. `librosa.effects.hpss(y)` returns harmonic and
percussive components; running chroma on just the harmonic part
dramatically cleans up results for percussive music.

### Very short samples

Samples under 250ms can have so few analysis frames that the median
isn't statistically meaningful. `detect.py` already guards against
samples shorter than 256 samples (about 6ms at 44.1k), but the
sensible lower bound for key estimation is closer to 500ms.

### Reference tuning

The pitch-class mapping assumes A4 = 440 Hz. Material tuned to A4 =
432 Hz, A4 = 415 Hz (baroque), or any non-standard reference will
have its pitches fall between bins. librosa has a `tuning` parameter
that can be pre-estimated with `librosa.estimate_tuning`. We don't
currently use it; for strictly-tuned modern material this doesn't
matter.

### Frequency-dependent octave errors

Very low bass (below ~60 Hz) can fold into the wrong octave because
the CQT runs out of resolution. In practice this means sub-bass
fundamentals can be misclassified. Mitigations: either raise `fmin`
to exclude the sub-bass region, or use overtone-based key estimation
which infers root from upper partials.

---

## References

- Krumhansl, C. L. (1990). *Cognitive Foundations of Musical Pitch*.
  Oxford University Press. The original source for the KS profiles.
- Temperley, D. (2004). "Bayesian models of musical structure and
  cognition." *Musicae Scientiae* 8: 175-205. Alternative corpus-derived
  profiles.
- Albrecht, J., & Shanahan, D. (2013). "The use of large corpora to train
  a new type of key-finding algorithm." *Music Perception* 31(1): 59-67.
  Modern re-derivation with improved accuracy.
- Bello, J. P., Daudet, L., Abdallah, S., Duxbury, C., Davies, M., &
  Sandler, M. B. (2005). "A tutorial on onset detection in music
  signals." *IEEE Transactions on Speech and Audio Processing*
  13(5): 1035-1047. Background for `rhythm_and_bpm.md`.
- librosa documentation: <https://librosa.org/doc/latest/>
- See `camelot_wheel.md` for how the output of this pipeline gets
  turned into compatibility matches.
- See `rhythm_and_bpm.md` for the BPM side of the same extraction.
