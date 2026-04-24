# MFCC (Mel-Frequency Cepstral Coefficients)

The workhorse timbre descriptor for almost every audio classification
and similarity task for the last 40 years. Covers the mel scale, the
log-mel spectrogram, the cepstrum concept, the DCT step that produces
the coefficients, and what each of the 13 coefficients tends to encode.

Last updated: 2026-04-23

---

## What they are

An MFCC is one of a set of scalars, typically 13 per analysis frame,
that together describe the **shape of the short-term power spectrum**
on a perceptual frequency scale. The key properties:

- **Compact**: 13 numbers instead of 1024+ spectral bins.
- **Decorrelated**: the DCT step produces coefficients that are
  roughly independent, so they play well with distance metrics.
- **Perceptually scaled**: the mel warp matches human pitch
  perception.
- **Discriminative for timbre**: two instruments playing the same
  note but with different tones produce clearly different MFCC
  profiles.

"Cepstral" comes from reversing "spectral": the cepstrum is the
spectrum of a spectrum. It captures structure in how energy is
distributed across frequency, separated from the absolute level of
any individual frequency bin.

---

## The four-step pipeline

Mental model:

```
    signal
       │ STFT
       ▼
    power spectrum         (linear frequency axis, raw power)
       │ mel filter bank
       ▼
    mel spectrogram        (mel frequency axis, perceptually scaled)
       │ log
       ▼
    log-mel spectrogram    (compressed dynamic range)
       │ DCT
       ▼
    MFCCs                  (decorrelated, low-dim timbre vector)
```

Four operations, each doing one well-understood thing. The output of
each step is a valid and useful feature in its own right; MFCC is
just the most compact and most common final form.

---

## Step 1: STFT power spectrum

Standard short-time Fourier transform. For frame `t` and bin `k`:

```
    |X[t, k]|^2   =  squared magnitude of STFT bin
```

This is the raw frequency content, on a linear Hz axis. Usually
`n_fft = 2048` at sr=44100 gives 1025 unique frequency bins from
0 Hz to ~22 kHz.

---

## Step 2: The mel scale and filter bank

The linear Hz axis isn't perceptually uniform. Humans hear pitch on
a roughly logarithmic scale: the interval between 100 Hz and 200 Hz
feels like the same musical distance as 1000 Hz and 2000 Hz (an
octave each). The **mel scale** is a perceptually-motivated warp.

### Hz to mel formula

```
    m = 2595 * log10(1 + f / 700)
```

At low frequencies (below ~500 Hz) this is roughly linear. At high
frequencies it becomes logarithmic. Some alternative formulas exist
(Slaney, Zwicker-Bark) but librosa uses the above by default.

### The filter bank

A mel filter bank is a set of triangular filters, each centered at a
mel-equal-spaced frequency, with width spanning the distance between
neighbors:

```
  mag
   │        ___
   │       /   \
   │    __/     \__
   │   /           \_ ...
   │  /              \
   └──┴───┴───┴────┴──── freq
      f1  f2   f3   f4
```

Passing the linear power spectrum through this filter bank produces
a mel-warped representation where each output is the energy in a
perceptual band. With `n_mels = 128` (librosa default for
melspectrogram), you get 128 values per frame instead of the 1025
raw STFT bins.

For MFCC use, a smaller bank (40 or 64 mel filters) is more
traditional, because too many filters makes subsequent DCT
compression less meaningful.

### Why triangular and not rectangular

Triangular filters have smooth rolloff. A sharp rectangular edge
causes ringing artifacts in the output. Triangles are the simplest
shape that smoothly averages adjacent bins without artifacts, and
they're what every speech processing pipeline has used since the
1970s.

---

## Step 3: Log compression

Human loudness perception is roughly logarithmic. A sound 10x more
intense is perceived as twice as loud, not 10x. To make downstream
distance metrics behave more like perceptual distances, take the
logarithm of the mel spectrogram:

```
    log_mel[t, m]  =  log( max(mel[t, m], epsilon) )
```

`epsilon` is a small positive value (like 1e-10) to avoid log(0).
Alternative: `log(1 + mel)`, which is more stable for quiet frames.

Log compression has two effects:

1. **Compresses dynamic range**. A transient 100x louder than the
   surrounding content becomes 2 log units higher instead of 100x
   larger. Subsequent processing is less dominated by loudest frames.
2. **Converts multiplicative to additive**. Source-filter models of
   sound production say a signal is `source * filter` in frequency
   domain. Taking logs turns this into `log(source) + log(filter)`,
   which the DCT can then partially separate.

---

## Step 4: Discrete Cosine Transform

The log-mel vector for one frame is `N_mels`-dimensional (e.g. 40).
The DCT projects this vector onto a set of cosine basis functions
and keeps the first `N_mfcc` coefficients (typically 13).

### The DCT basis

For a length-N signal, the DCT produces coefficients:

```
    C[k] = sum over n in [0, N-1] of  x[n] * cos( pi * (n + 0.5) * k / N )
```

for `k` from 0 to N-1. The basis functions are cosines at increasing
frequencies:

```
    k=0:  ──────────    (flat, captures overall mean/level)
    k=1:  ───     ───   (one half-cycle, captures tilt)
    k=2:  ─  ─  ─  ─    (one cycle, captures broad shape)
    k=3:  - - - - - -   (higher cycles, finer detail)
    ...
```

Keeping only the first `N_mfcc` coefficients throws away the
high-frequency details in the log-mel spectrum, keeping the smooth
shape.

### Why this is the trick

The log-mel spectrogram of a typical signal has a smooth envelope
plus high-frequency fluctuations. The envelope encodes the vocal
tract shape, instrument body resonance, or other slowly-varying
timbre characteristics. The high-frequency fluctuations mostly
encode noise and fine spectral detail that's not perceptually
distinguishing.

The DCT compactly represents the envelope in the first few
coefficients and pushes the noise into higher coefficients that
get discarded. This is dimensionality reduction that preserves the
discriminative information.

### Why 13 coefficients

Standard choice from the speech recognition literature. The first
13 coefficients capture most of the envelope information for speech
(which is the historical use case). Some applications use more (20,
26, or 40); ours uses 13 because it's the standard and
cross-compatible with most published feature datasets.

### Coefficient 0

`MFCC[0]` encodes the overall log-energy (roughly the average of the
log-mel vector). Some implementations discard it explicitly because
it's essentially a loudness measure and correlates heavily with RMS.
librosa returns it by default; acidcat keeps it.

---

## What each coefficient tends to mean

This is hand-wavy because exact interpretation depends on the
specific filter bank, window size, and input content. But general
tendencies:

| coefficient | loose interpretation |
|-------------|----------------------|
| MFCC[0] | overall log-energy (level/loudness proxy) |
| MFCC[1] | spectral tilt (brighter vs darker overall) |
| MFCC[2] | bimodality (two-peaked vs one-peaked spectrum) |
| MFCC[3] | finer shape: three lobes |
| MFCC[4-12] | progressively finer spectral shape detail |

For distinguishing a kick drum from a snare, the first 3-4
coefficients do most of the work. For distinguishing two similar
violins, you need coefficients 5-12 to pick up the finer body
resonance differences.

### Visualizing MFCC differences

Two samples with different timbres plotted as 13-D points:

```
kick drum:     [4.2,  -1.1,  0.3,  -0.2,  0.1, ...] (dark, one big peak in bass)
snare:         [3.8,   0.5,  2.1,  -0.4,  1.3, ...] (mid-forward, multi-peak)
hi-hat:        [2.1,   3.2,  1.8,   1.5, -0.2, ...] (bright, narrow high peak)
pad (dark):    [3.5,  -2.3,  0.1,  -0.1,  0.0, ...] (sustained, low coefficients small)
pad (bright):  [3.5,   1.8,  0.2,  -0.1,  0.0, ...] (same shape category, brightness inverted)
```

Distance in 13-D MFCC space correlates reasonably well with
perceptual timbre distance, which is why similarity scoring works.

---

## librosa implementation

```python
mfccs = librosa.feature.mfcc(y=y, sr=sr,
                              n_mfcc=13,
                              n_fft=2048,
                              hop_length=512,
                              n_mels=128,
                              htk=False,
                              lifter=0)
```

Returns shape `(n_mfcc, T)` where `T` is the number of frames.

Parameters worth knowing:

| parameter | default | effect |
|-----------|---------|--------|
| `n_mfcc` | 13 | how many coefficients to keep |
| `n_mels` | 128 | size of mel filter bank before DCT |
| `htk` | False | use HTK's slightly different mel formula vs Slaney's |
| `lifter` | 0 | cepstral liftering, emphasizes higher coefficients |
| `dct_type` | 2 | DCT variant; type 2 is the standard |
| `norm` | 'ortho' | orthonormal DCT, preserves energy |

### HTK vs Slaney mel

Two competing definitions of the mel scale exist. HTK's formula:

```
    m = 2595 * log10(1 + f / 700)
```

Slaney's auditory toolbox formula:

```
    piecewise: linear below 1000 Hz, logarithmic above
```

librosa's default (`htk=False`) uses Slaney's version, which is
slightly closer to human perception measurements. Most published
MFCC datasets use HTK. If comparing against external datasets, set
`htk=True`.

---

## acidcat implementation

In `core/features.py`:

```python
mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
for i in range(13):
    features[f'mfcc_{i+1}_mean'] = np.mean(mfccs[i])
    features[f'mfcc_{i+1}_std']  = np.std(mfccs[i])
```

Stores 26 values total: mean and std of each of the 13 coefficients.
Indexed 1-13 in the field names (acidcat uses 1-based naming to match
MPEG-7 conventions; librosa returns 0-indexed rows).

The mean captures the average timbre character over the clip; the
std captures how much the timbre varies. A static drone has low
std across all coefficients; a dynamic synth lead has higher std
especially in coefficients 1-3 (which track brightness).

---

## Why MFCCs work so well for similarity

Three properties that are hard to find together in other features:

### Decorrelation

The DCT projects onto orthogonal basis functions. The coefficients
are therefore much less correlated with each other than raw
spectrogram bins would be. This matters for distance metrics:
Euclidean or cosine distance assumes independent dimensions, and
redundant dimensions effectively double-count.

### Dimensionality

13 numbers is small enough to store cheaply, index efficiently, and
compute distances over without special tricks. The full mel
spectrogram of a 10-second clip is ~100k numbers.

### Robustness

MFCCs are relatively insensitive to:

- Overall loudness changes (coefficient 0 captures this separately
  and can be discarded)
- Pitch shifts within reason (the log-mel envelope shifts vertically,
  coefficients 1+ largely preserve shape)
- Minor spectral noise (DCT discards high-frequency fluctuations)

They are sensitive to:

- Timbre differences (which is what we want)
- Major formant shifts
- Time-varying articulation (when looking at the time series)

---

## Interpretation examples

Numerical intuition for the first three coefficients across common
sample types. Values are approximate, from a handful of test clips.

```
                        MFCC[0]  MFCC[1]  MFCC[2]
808 kick, long decay    4.1      -2.8     0.2
snare, tight            3.9       0.1     1.8
closed hi-hat           2.0       3.5     1.2
crash cymbal            2.2       4.1     2.8
deep pad                3.3      -1.9    -0.3
bright pad              3.3       1.4     0.1
piano C4 chord          3.5      -0.3     0.4
distorted guitar chord  3.8       0.8     1.9
vocal "ah" sustained    3.2      -0.1     1.5
white noise             2.5       0.0     0.0
```

Patterns to notice:

- Low MFCC[0] (under 3) correlates with quieter or more sparse signal.
- Negative MFCC[1] correlates with darkness (bass-heavy content).
- Positive MFCC[1] correlates with brightness.
- MFCC[2] distinguishes mid-forward (piano, snare, vocal) from
  purely high (cymbal) or purely low (kick).

These patterns are not rigid rules but they hold well enough that
humans can often classify unlabeled samples by looking at the first
three coefficients.

---

## Gotchas

### Linearly separable is not the same as perceptually matching

Two samples with close MFCC vectors usually sound similar. Two
samples with different MFCC vectors usually sound different. But the
mapping isn't 1:1. Two distinct timbres can produce similar MFCCs
(e.g. a muted guitar and a soft woodblock have surprisingly close
profiles). Useful for "close matches" but not for absolute
perceptual classification.

### Log-mel before DCT vs power-mel before DCT

Some papers use power (mel-spectrogram without log). This produces
very different coefficients because the log compression was critical
to getting a smooth envelope. librosa's default is log-mel, which
is correct. Don't mix conventions across datasets.

### Silence and near-silence

For silent or near-silent frames, the log-mel vector is dominated by
the `epsilon` floor. MFCC values become numerically unstable or
content-free. The extractor doesn't special-case this; frames where
`rms < threshold` could be excluded from the mean/std aggregation,
but currently aren't.

### Very short clips

Samples under ~200ms produce only a few MFCC frames. Mean and std
are unreliable estimators of the distribution. For one-shots, the
coefficient values from a single frame in the middle of the sample
are often more meaningful than statistics over a few frames.

### Window length tradeoff

`n_fft=2048` gives 46ms analysis windows. For percussive samples
with fast transients (< 50ms), this window is longer than the
sample itself, smearing the attack across multiple frames. For
analysis of transient-rich material, `n_fft=512` or `1024` is a
better choice at the cost of frequency resolution.

acidcat uses the librosa default. If the index grows and we find
percussive material poorly discriminated, switching to `n_fft=1024`
specifically for percussive-classified samples would be worth
trying.

### Cepstral liftering

The `lifter` parameter applies a sinusoidal weighting to the MFCCs
that de-emphasizes the lowest coefficients and emphasizes the
higher. This was useful in old speech recognition systems; modern
feature vectors usually set `lifter=0` (no liftering). acidcat uses
the default.

---

## Alternatives to MFCC worth knowing

- **PLP (Perceptual Linear Prediction)**: similar idea, different
  filter bank (Bark scale) and a linear prediction step instead of
  DCT. Used more in speech than music.
- **RASTA-PLP**: PLP with adaptive temporal filtering, good for
  noisy environments.
- **Constant-Q Cepstral Coefficients (CQCC)**: same idea but using a
  CQT instead of STFT for the initial spectrum, giving uniform
  semitone resolution. More musically appropriate but less widely
  used.
- **Spectrogram features**: raw mel spectrogram as input to a CNN.
  Modern deep-learning approach, sidesteps the dimensionality
  reduction question by letting the network learn it.

For acidcat's current scope (fast similarity over a local library),
MFCC is the right choice. If we add learned embeddings later,
they'd go alongside MFCC, not replace it.

---

## References

- Davis, S. B., & Mermelstein, P. (1980). "Comparison of parametric
  representations for monosyllabic word recognition in continuously
  spoken sentences." *IEEE Transactions on Acoustics, Speech, and
  Signal Processing* 28(4): 357-366. The original MFCC paper.
- Logan, B. (2000). "Mel frequency cepstral coefficients for music
  modeling." *ISMIR 2000*. Makes the case for MFCC in music contexts.
- Slaney, M. (1998). "Auditory toolbox." *Technical Report 1998-010,
  Interval Research Corporation*. Defines Slaney's mel formula that
  librosa uses by default.
- Young, S., et al. (2006). *The HTK Book*. Cambridge University
  Engineering Department. Reference for HTK-style mel formula and
  MFCC conventions used in most published speech datasets.
- librosa MFCC documentation: <https://librosa.org/doc/latest/generated/librosa.feature.mfcc.html>
- See `dsp/spectral_features.md` for the non-cepstral timbre features
  that complement MFCC.
- See `dsp/feature_pipeline.md` for how MFCC fits into the full
  feature vector acidcat stores.
