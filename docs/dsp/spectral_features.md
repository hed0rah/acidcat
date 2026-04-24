# Spectral and Energy Features

Single-number and two-number (mean + std) descriptors of timbre and
dynamics: spectral centroid, rolloff, bandwidth, contrast, plus the
time-domain companions zero-crossing rate and RMS energy. These are
the "what does this sample sound like" primitives that feed
similarity scoring and ML clustering.

Last updated: 2026-04-23

---

## What they are

Where chroma and beat tracking answer "what notes, at what times,"
spectral features answer "what does each moment of this sample
actually sound like." They collapse a short audio frame's full
spectrum into a small number of interpretable scalars.

Each feature is computed per STFT frame (usually ~11ms windows) and
aggregated over the clip with mean and standard deviation. The two
numbers give a rough (location, spread) summary of the feature's
trajectory over time. A drum hit with a bright attack and a dull
tail has a high centroid-std because the value swings widely between
frames.

acidcat stores the mean and std of each feature. In `features.py`:

```python
spectral_centroids = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
features['spectral_centroid_mean'] = np.mean(spectral_centroids)
features['spectral_centroid_std']  = np.std(spectral_centroids)
```

Same pattern for rolloff, bandwidth, contrast, ZCR, RMS. This gives
the feature vector (roughly) 2 fields per extractor, plus the
per-band arrays for contrast and the per-coefficient arrays for
MFCC, totaling the advertised 50+ features.

---

## Spectral centroid

Perceptual correlate: **brightness**. How much of the energy is in
high frequencies.

### Math

The spectral centroid is the first moment (center of mass) of the
magnitude spectrum:

```
    C = sum over k of (f[k] * |X[k]|)  /  sum over k of |X[k]|
```

where `f[k]` is the frequency of bin `k` in Hz, and `|X[k]|` is the
magnitude at that bin. The denominator normalizes by total energy so
the output is independent of overall loudness.

Units: hertz. Values typically range from ~100 Hz (extreme bass) to
~10,000 Hz (hissy / cymbal-heavy).

### librosa implementation

```python
librosa.feature.spectral_centroid(y=y, sr=sr,
                                   n_fft=2048,
                                   hop_length=512,
                                   freq=None)
```

Returns array of shape `(1, T)` where `T` is number of frames. The
`freq=None` default uses the STFT bin frequencies directly. Can pass
a custom frequency array for alternative bandings.

### Interpretation

| centroid (Hz) | typical content |
|---------------|-----------------|
| < 500 | sub-bass, pure bass tones, muddy content |
| 500 - 1500 | bass-heavy but complete (kick, deep pad, vocal chest) |
| 1500 - 3000 | mid-forward, voice, most guitar |
| 3000 - 5000 | bright: hi-hats, snare top end, cutting synths |
| > 5000 | hissy, cymbal wash, noise, very bright transients |

Common rule of thumb: a dark pad has centroid < 1500 Hz. A bright
lead has centroid > 3000 Hz. A full mix sits around 2000-3000 Hz.

### acidcat usage

Stored as `spectral_centroid_mean` and `spectral_centroid_std` in
the feature JSON blob. Used in similarity scoring: two samples with
similar centroids likely have similar brightness. A high centroid-std
suggests dynamic timbre (changes over the clip); low std suggests
static timbre (same character throughout).

### Gotchas

- **DC offset**: A signal with DC offset (nonzero mean) has a spurious
  spike at 0 Hz that pulls the centroid down. librosa normally
  handles this, but cheap converters or badly-edited audio can leak
  DC.
- **Narrow-band content**: a pure sine at 440 Hz has centroid =
  440 Hz with near-zero std (trivially). Useful for sanity checks.
- **Inaudible high content**: 20 kHz content still contributes to the
  centroid even though it's inaudible. Some ultrasonic noise or
  digital artifacts can inflate the centroid without affecting how
  the sample actually sounds. Pre-filtering with a low-pass at
  ~16 kHz is sometimes worth doing for perceptually-anchored
  centroid estimates.

---

## Spectral rolloff

Perceptual correlate: **high-frequency cutoff**. The frequency below
which a specified fraction of total spectral energy lies.

### Math

For threshold `r` (typically 0.85), rolloff is the smallest frequency
`f_R` such that:

```
    sum over k where f[k] <= f_R  of  |X[k]|^2   >=   r * total_energy
```

where `total_energy = sum of |X[k]|^2`.

Intuitively: "where does 85% of the energy stop?" A track with most
of its energy below 2000 Hz has rolloff around 2000 Hz. A bright mix
with lots of high content has rolloff at 8000+ Hz.

### librosa implementation

```python
librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.85)
```

Default `roll_percent=0.85` is the standard MPEG-7 definition.
`roll_percent=0.5` gives the spectral median (half the energy below,
half above). Setting the percent lower makes it more sensitive to
energy concentration; higher makes it more sensitive to noise in the
tail.

### Interpretation

| rolloff (Hz) | typical content |
|--------------|-----------------|
| < 1000 | telephone-quality audio, bass-heavy filtered sounds |
| 1000 - 3000 | AM-radio quality, heavily filtered or muted |
| 3000 - 7000 | typical lo-fi or tape-saturated content |
| 7000 - 12000 | modern clean mix, full-frequency response |
| > 12000 | very bright or hissy, cymbal-wash, noise-heavy |

### centroid vs rolloff

They correlate but aren't redundant:

- **Centroid** is a weighted average; a single very bright transient
  mixed with a lot of bass produces a moderate centroid.
- **Rolloff** is a percentile; the same signal produces a high
  rolloff because the high transient by itself represents a
  significant fraction of total energy.

For distinguishing "consistently bright" from "mostly dark with
occasional brightness," having both features is useful.

### acidcat usage

`spectral_rolloff_mean` and `spectral_rolloff_std`. Same pattern as
centroid.

### Gotchas

- **Sensitive to `roll_percent` choice**: comparing across extractors
  with different percentages produces meaningless comparisons. We
  use librosa's default 0.85 everywhere.
- **Noise tail inflation**: a clean mix with 30 seconds of noisy
  silence at the end has an artificially high rolloff because the
  noise dominates the high-frequency tail. Trim silence before
  analysis for accurate rolloff.

---

## Spectral bandwidth

Perceptual correlate: **spectral spread**. How wide the frequency
distribution is around the centroid.

### Math

Bandwidth is the p-th central moment of the spectrum, usually p=2
(variance-analogue):

```
    B = sqrt( sum over k of (f[k] - C)^2 * |X[k]|^2  /  sum of |X[k]|^2 )
```

where `C` is the spectral centroid. Units: hertz. This is the
standard deviation of the frequency distribution weighted by
magnitude.

Intuitively: a narrow sine wave has low bandwidth; white noise has
high bandwidth; a mix sits in between.

### librosa implementation

```python
librosa.feature.spectral_bandwidth(y=y, sr=sr, p=2)
```

Default `p=2` gives the RMS-style spread. `p=1` gives mean absolute
deviation; higher `p` weights outliers more.

### Interpretation

| bandwidth (Hz) | typical content |
|----------------|-----------------|
| < 500 | pure tone, simple sine, isolated note |
| 500 - 1500 | clean harmonic tone, mono instrument |
| 1500 - 3000 | ensemble content, mixed source |
| 3000 - 6000 | full mix, broadband material |
| > 6000 | noisy, wide-band, distorted |

### acidcat usage

`spectral_bandwidth_mean` and `spectral_bandwidth_std`. Useful for
distinguishing focused tonal content from broadband textures in
similarity searches.

### Gotchas

- **Correlates with noise-floor level**: a quiet recording with
  audible noise has higher bandwidth than a silent one. Mostly an
  issue for very quiet source material.
- **Not a great mono-vs-stereo indicator**: two mono sources
  summed produce similar bandwidth to one mono source. For
  stereo-width analysis, use L/R channel comparisons directly.

---

## Spectral contrast

Perceptual correlate: **tonal vs noise character, per frequency
band**. Measures how peaky the spectrum is within each of several
octave-wide bands.

### Math

Divide the spectrum into octave-wide bands (typically 6 bands, e.g.
~0-200 Hz, 200-400, 400-800, 800-1600, 1600-3200, 3200+). Within
each band, take the ratio (or log-difference) between the top
percentile of magnitudes and the bottom percentile:

```
    contrast[b] = log(mean of top  alpha fraction of |X| in band b)
                - log(mean of bot  alpha fraction of |X| in band b)
```

where `alpha` is typically 0.02 (top and bottom 2%).

High contrast in a band means distinct peaks (tonal content, sharp
harmonics). Low contrast means even distribution (noisy content).

### librosa implementation

```python
librosa.feature.spectral_contrast(y=y, sr=sr, n_bands=6, fmin=200)
```

Returns array of shape `(n_bands + 1, T)`. The extra band is the
sub-fmin band (below 200 Hz by default). Six bands gives one per
octave roughly from 200 Hz to 12.8 kHz.

### Interpretation

Per-band values. Reading a contrast vector:

```
[3.2, 2.8, 3.1, 2.5, 1.9, 1.5, 0.8]
 sub  200  400  800  1.6k 3.2k 6.4k

tonal bass, mid-heavy, dull top
```

vs

```
[0.5, 0.4, 0.3, 0.3, 0.4, 0.5, 0.6]
near-uniform = noisy / percussive / inharmonic
```

### acidcat usage

`spectral_contrast_mean` and `spectral_contrast_std`. librosa returns
a per-band array; acidcat currently flattens to mean/std across all
bands. This loses the per-band information, which is a known
simplification worth revisiting for similarity work.

A better storage scheme would preserve each band as its own mean/std
pair, giving 14 features (7 bands x 2 stats) instead of 2. The
feature vector would be larger but more discriminating.

### Gotchas

- **Band definitions matter**: the 200 Hz `fmin` default cuts off
  sub-bass. For bass-heavy content (house, dubstep) lowering fmin
  to 50 or even 20 Hz gives better discrimination.
- **Silence handling**: contrast in a band with no energy is
  ill-defined. librosa returns 0 which is a reasonable convention
  but shouldn't be averaged in without care.

---

## Zero-crossing rate

Technically time-domain, not spectral, but grouped with spectral
features because it's a cheap proxy for high-frequency content.

### What it is

Count of sign changes in the waveform per unit time. A signal that
oscillates quickly crosses zero often; a signal with slow variations
crosses zero rarely.

### Math

For a frame of samples `y[0..N-1]`:

```
    ZCR = (1 / (N-1)) * sum over n in [1, N-1] of  [sign(y[n]) != sign(y[n-1])]
```

Dimensionless (fraction per sample). Multiply by `sr` to get crossings
per second.

### Interpretation

| ZCR | typical content |
|-----|-----------------|
| < 0.01 | very low frequency content, sub-bass, DC-heavy |
| 0.01 - 0.05 | bass, kick drums, sustained low tones |
| 0.05 - 0.15 | mid-range tonal content, voice, melody |
| 0.15 - 0.35 | bright / percussive, hi-hat, snare, noise-adjacent |
| > 0.35 | white noise, cymbal wash, heavy saturation |

Relationship to centroid: high-ZCR signals usually have high
centroids (both are sensitive to fast oscillation). But they can
disagree: a signal with equal energy at 50 Hz and 2000 Hz has
moderate centroid but very low ZCR (dominated by the 50 Hz
oscillation).

### Voiced vs unvoiced speech

Classical use case: in speech analysis, voiced segments (vowels)
have low ZCR and high energy; unvoiced segments (fricatives like
"s", "f") have high ZCR. A ZCR vs energy 2D plot cleanly separates
voiced from unvoiced frames.

For sample libraries this is less directly useful but the same
principle applies: tonal sustained content has low ZCR, noisy
transient content has high ZCR.

### librosa implementation

```python
librosa.feature.zero_crossing_rate(y, frame_length=2048, hop_length=512)
```

### acidcat usage

`zcr_mean` and `zcr_std`. Cheap to compute, good for distinguishing
bass-heavy samples from hat-heavy samples in similarity queries.

### Gotchas

- **Sample-rate dependent if unnormalized**: the same signal at
  44.1k vs 48k has different raw crossing counts per frame. librosa
  returns the normalized fraction (per sample) so this is handled,
  but be aware when computing ZCR manually.
- **DC offset matters**: a signal with DC offset crosses zero only
  when oscillation amplitude exceeds the offset magnitude, which
  suppresses the ZCR. Unlikely in clean recordings but common in
  cheap converters.

---

## RMS energy

Root mean square of the waveform. Perceptual correlate: **loudness**
in a rough sense (true loudness requires perceptual weighting).

### Math

For a frame of samples `y[0..N-1]`:

```
    RMS = sqrt( (1/N) * sum of y[n]^2 )
```

Dimensionless (if samples are in [-1, 1]). Interpretable as an
"average amplitude."

### Relationship to perceived loudness

RMS is not true loudness. True loudness (LUFS, ITU-R BS.1770)
applies K-weighting (pre-emphasis of mid frequencies) and gating
(ignoring silent sections). RMS ignores both.

For sample-library purposes RMS is a useful relative measure:
samples with higher RMS are louder on average. Two different
samples at the same RMS can have very different perceived
loudnesses if their frequency content differs significantly.

### Interpretation

Values depend on normalization. For a signal in [-1, 1]:

| RMS | dBFS approx | typical content |
|-----|-------------|-----------------|
| 0.001 | -60 dB | very quiet, near-silence |
| 0.01 | -40 dB | background music, ambient |
| 0.1 | -20 dB | normal program material |
| 0.3 | -10 dB | loud mix, heavy compression |
| 0.5+ | -6 dB+ | brickwalled, clipped, or very hot |

### librosa implementation

```python
librosa.feature.rms(y=y, frame_length=2048, hop_length=512)
```

Returns per-frame RMS. Array shape `(1, T)`.

### acidcat usage

`rms_mean` and `rms_std`. The mean gives average loudness; the std
indicates dynamic range (high std = highly dynamic, low std = heavily
compressed or sustained).

### Gotchas

- **Normalization assumption**: acidcat loads audio with
  `librosa.load(sr=None, mono=True)` which applies a default
  normalization (max absolute amplitude ≈ 1). This means RMS
  values are comparable across files of different original
  levels. If you want pre-normalization RMS for mastering checks,
  use `librosa.load(normalize=False)` instead.
- **Silence padding**: trailing silence in a file pulls down RMS
  because it adds zero-energy frames to the average. For a loop
  with 1 second of audio and 3 seconds of silence, the RMS is
  roughly 1/4 what it would be without the silence.
- **Not a clipping indicator**: a brickwalled signal has RMS close
  to 0.5 but clipping (hard limit at 1.0) doesn't show up in RMS.
  Peak-to-RMS ratio (crest factor) is the right metric for that.

---

## Mel spectrogram

Used inside MFCC extraction; separately exposed by acidcat as
`mel_mean` and `mel_std`. Deep-dive math lives in `dsp/mfcc.md`;
this is a brief overview.

### What it is

A spectrogram whose frequency axis is warped to the mel scale, which
approximates human pitch perception. The mel scale is roughly linear
below 1 kHz and logarithmic above.

### Formula (Hz to mel)

```
    m = 2595 * log10(1 + f / 700)
```

and inverse:

```
    f = 700 * (10^(m / 2595) - 1)
```

A 128-band mel filterbank is standard. Each band is a triangular
filter centered at a mel-spaced frequency, width tapering linearly.

### librosa implementation

```python
librosa.feature.melspectrogram(y=y, sr=sr,
                                n_fft=2048,
                                hop_length=512,
                                n_mels=128)
```

Returns array shape `(n_mels, T)`. Units: power (not amplitude) by
default.

### acidcat usage

Stored as `mel_mean` and `mel_std` across the full mel spectrogram.
Loses per-band information, similar to the spectral contrast
collapse. Used for rough "energy in perceptual bands" similarity.

MFCC is the more discriminating feature derived from this; see the
MFCC doc.

---

## Feature vector summary

The complete list of "single-number" features stored in acidcat's
feature vector, grouped by type:

### Time domain

```
duration_sec                          length in seconds
sample_rate                           Hz (not really a feature, stored for provenance)
audio_length_samples                  raw frame count
zcr_mean / zcr_std                    zero-crossing rate statistics
rms_mean / rms_std                    RMS energy statistics
```

### Spectrum

```
spectral_centroid_mean / std          brightness
spectral_rolloff_mean / std           high-frequency cutoff
spectral_bandwidth_mean / std         spectral spread
spectral_contrast_mean / std          peak-to-valley ratio (collapsed across bands)
chroma_mean / std                     pitch class energy (collapsed across bins)
mel_mean / std                        mel-band energy (collapsed across bands)
```

### Cepstrum

```
mfcc_1_mean / std ... mfcc_13_mean / std    (13 coefficients x 2 stats = 26 fields)
```

### Rhythm

```
tempo_librosa                         raw librosa tempo estimate
beat_count                            detected beat count
```

### Tonal space

```
tonnetz_mean / std                    tonal centroid coordinates (collapsed)
```

Total: roughly 50 features, depending on exact count. Most are stored
as float32; the MFCC group dominates the vector by count (26 of ~50).

The "collapsed" features (contrast, mel, chroma, tonnetz) lose per-band
information by taking global mean and std. Preserving per-band stats
would multiply the feature count significantly but improve similarity
discrimination. See `feature_pipeline.md` for the discussion of
current vs ideal feature shapes.

---

## Cross-feature correlations worth knowing

Some pairs of features carry mostly redundant information. Knowing
which ones helps when interpreting similarity results or designing
scoring weights.

| feature pair | correlation | why |
|--------------|-------------|-----|
| centroid, rolloff | high | both track high-frequency presence |
| centroid, ZCR | moderate | both reflect fast-oscillation energy |
| bandwidth, spectral_contrast | low | measure different properties of the same spectrum |
| RMS, tempo | none | amplitude vs rate, independent |
| chroma, mfcc | low | pitch class vs timbre, mostly independent |

For similarity scoring a cosine distance over the full feature vector
implicitly weights each feature equally, which means redundant
features (centroid + rolloff) effectively double-count brightness.
Addressing this properly requires either feature weighting or
dimensionality reduction (PCA) before the distance metric. Neither
is currently done.

---

## Gotchas common to all spectral features

### Window length

Default `n_fft=2048` at sr=44100 gives 46ms analysis windows.

- Too short: insufficient frequency resolution, bass gets smeared.
- Too long: poor time resolution, transient details washed out.
- Default is a reasonable compromise for general-purpose use.

For sub-bass-specific analysis, doubling to `n_fft=4096` improves low
resolution at the cost of doubled compute and some latency. For
transient detail analysis, `n_fft=512` or `1024` is better.

### Silence and very short clips

All of these features assume a signal with actual audio content. On a
pure-silence clip they return 0 or undefined values. acidcat's
extractor guards against the zero-audio case but not against near-
silence (very quiet but nonzero content), where features can be
dominated by noise floor rather than intended signal.

### Loudness normalization

acidcat uses librosa's default normalization. If you compare feature
vectors between unnormalized and normalized source material, the RMS
values will obviously differ but the centroid / rolloff / bandwidth
should not (they're ratio-based and level-invariant).

### Sample rate mismatches

A 22 kHz WAV and a 44.1 kHz WAV of the same source produce identical
centroids, rolloffs, etc. because these are frequency-based and
frequency is intrinsic to the content, not the sample rate. Sample
rate bounds the maximum measurable frequency (Nyquist = sr/2), so
files at lower sample rates have less high-frequency information to
work with.

---

## References

- Peeters, G. (2004). "A large set of audio features for sound
  description (similarity and classification) in the CUIDADO
  project." *IRCAM technical report*. Canonical reference for audio
  feature definitions.
- Tzanetakis, G., & Cook, P. (2002). "Musical genre classification of
  audio signals." *IEEE Transactions on Speech and Audio Processing*
  10(5): 293-302. Seminal paper using many of these features for
  classification.
- Fastl, H., & Zwicker, E. (2007). *Psychoacoustics: Facts and
  Models*. Springer. Background on perceptual frequency scales (mel,
  Bark).
- librosa feature documentation:
  <https://librosa.org/doc/latest/feature.html>
- See `dsp/mfcc.md` for the cepstral features that build on the mel
  spectrogram.
- See `dsp/feature_pipeline.md` for how all of these are assembled
  into the final feature vector.
