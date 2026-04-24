# Feature Pipeline

How `core/features.py` composes all the DSP primitives into a single
feature vector per audio file. The capstone doc that ties together
the spectral, cepstral, rhythmic, and tonal components.

Last updated: 2026-04-23

---

## What it does

`extract_audio_features(filepath)` takes one audio file path and
returns a flat Python dict of roughly 50 scalar features. That dict
becomes the row stored in the SQLite `features` table (as a JSON
blob) or serialized into a CSV row for the `acidcat features`
command.

The features are used for:

- Similarity search via `find_similar` (cosine distance over the vector)
- Clustering via k-means or agglomerative methods (`acidcat similar cluster`)
- ML experiments that treat the sample library as a labeled or unlabeled dataset
- Any downstream analysis that needs a compact numerical description of each sample

---

## The 50-field vector

Grouped by type:

### Time domain (5 fields)

```
duration_sec                length in seconds
sample_rate                 Hz (provenance, not a discriminating feature)
audio_length_samples        raw frame count
zcr_mean, zcr_std           zero-crossing rate statistics
rms_mean, rms_std           RMS energy statistics
```

### Spectral descriptors (12 fields)

```
spectral_centroid_mean / std      brightness
spectral_rolloff_mean / std       high-frequency cutoff
spectral_bandwidth_mean / std     spread around centroid
spectral_contrast_mean / std      peak-to-valley ratio (collapsed)
chroma_mean / std                 pitch class energy (collapsed)
mel_mean / std                    mel-band energy (collapsed)
```

### Cepstral (26 fields)

```
mfcc_1_mean, mfcc_1_std
mfcc_2_mean, mfcc_2_std
...
mfcc_13_mean, mfcc_13_std
```

### Rhythm (2 fields)

```
tempo_librosa       raw librosa tempo estimate
beat_count          detected beat count
```

### Tonal geometry (2 fields)

```
tonnetz_mean, tonnetz_std       collapsed 6D tonal centroid
```

Total: ~50 fields depending on exact counting. The cepstral group
dominates the vector by count.

---

## The extraction flow

```python
def extract_audio_features(filepath):
    y, sr = librosa.load(filepath, sr=None, mono=True)
    if len(y) < 256:
        return None
    features = {}

    # basic properties
    features['duration_sec']          = len(y) / sr
    features['sample_rate']           = sr
    features['audio_length_samples']  = len(y)

    # spectral
    sc  = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    sro = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
    sb  = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
    features['spectral_centroid_mean']  = np.mean(sc)
    features['spectral_centroid_std']   = np.std(sc)
    features['spectral_rolloff_mean']   = np.mean(sro)
    features['spectral_rolloff_std']    = np.std(sro)
    features['spectral_bandwidth_mean'] = np.mean(sb)
    features['spectral_bandwidth_std']  = np.std(sb)

    # zero-crossing rate
    zcr = librosa.feature.zero_crossing_rate(y)[0]
    features['zcr_mean'] = np.mean(zcr)
    features['zcr_std']  = np.std(zcr)

    # mfcc
    mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    for i in range(13):
        features[f'mfcc_{i+1}_mean'] = np.mean(mfccs[i])
        features[f'mfcc_{i+1}_std']  = np.std(mfccs[i])

    # chroma
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    features['chroma_mean'] = np.mean(chroma)
    features['chroma_std']  = np.std(chroma)

    # mel spectrogram
    mel = librosa.feature.melspectrogram(y=y, sr=sr)
    features['mel_mean'] = np.mean(mel)
    features['mel_std']  = np.std(mel)

    # tempo and beats
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
    features['tempo_librosa'] = float(np.atleast_1d(tempo)[0])
    features['beat_count']    = len(beats)

    # rms energy
    rms = librosa.feature.rms(y=y)[0]
    features['rms_mean'] = np.mean(rms)
    features['rms_std']  = np.std(rms)

    # spectral contrast
    contrast = librosa.feature.spectral_contrast(y=y, sr=sr)
    features['spectral_contrast_mean'] = np.mean(contrast)
    features['spectral_contrast_std']  = np.std(contrast)

    # tonnetz
    tonnetz = librosa.feature.tonnetz(y=y, sr=sr)
    features['tonnetz_mean'] = np.mean(tonnetz)
    features['tonnetz_std']  = np.std(tonnetz)

    return features
```

Essentially one librosa call per feature family, followed by
`np.mean` and `np.std` aggregation.

---

## Pipeline shape: why every feature is mean + std

Each librosa feature call returns a 1D or 2D array indexed by time
frame. The file might have 200 frames or 20000 depending on
length. To get fixed-size features usable for distance metrics, we
aggregate across time.

Mean captures the "typical value" of the feature. Std captures how
much it varies. A sample with static timbre has low std; a sample
that evolves has high std.

Two numbers per feature is the lightest informative summary. You
could use:

- **mean only**: loses dynamic information. A steady pad and a
  pulsing pad with the same average look identical.
- **mean + std**: captures location + spread. Current choice.
- **mean + std + skewness + kurtosis**: full four-moment summary.
  More discriminating but 4x the field count and diminishing
  returns on most features.
- **median + IQR**: more robust to outliers than mean + std. Could
  be better for transient-heavy material.
- **full time series**: maximum information, but variable length
  and doesn't fit into a flat row.

acidcat's choice (mean + std) is the pragmatic standard for audio
feature vectors.

---

## The "collapse" problem

Four features in the current vector are aggregated across both
time AND frequency/dimension, which loses the internal structure:

- **`spectral_contrast`**: natively 7 bands x T frames. Collapsed to
  a single mean+std, losing per-band information.
- **`chroma`**: natively 12 pitch classes x T frames. Collapsed.
- **`mel`**: natively 128 mel bands x T frames. Collapsed.
- **`tonnetz`**: natively 6 dimensions x T frames. Collapsed.

What we lose:

- A kick-heavy sample and a hi-hat-heavy sample have different
  contrast band distributions but might collapse to similar scalar
  means.
- Two tonal samples in different keys have different chroma peak
  locations but collapse to similar scalar means.
- Two chords at different tonal-wheel positions have different
  Tonnetz vectors but collapse to similar scalar means.

The collapse was a historical choice for vector compactness. With
modern storage it's not worth it. The better approach:

```
# preserve per-dimension stats for multi-dim features
for i in range(n_bands):
    features[f'contrast_band_{i}_mean'] = np.mean(contrast[i])
    features[f'contrast_band_{i}_std']  = np.std(contrast[i])
```

Expanded feature counts per extractor:

| feature | current fields | proposed fields | net gain |
|---------|---------------|-----------------|----------|
| contrast | 2 | 14 | +12 |
| chroma | 2 | 24 | +22 |
| mel | 2 | 256 | +254 |
| tonnetz | 2 | 12 | +10 |

Mel is the outlier. Keeping mel collapsed and expanding only
contrast, chroma, and tonnetz gives:

- Total fields: ~50 → ~96
- Storage per row: ~400 bytes → ~800 bytes (JSON)
- Similarity accuracy: meaningfully improved for harmonic and
  timbral distinctions

Schema change would require a `features_version` bump. The existing
`features_version INTEGER` column in the `features` table was added
exactly for this kind of migration. See "versioning" below.

### Why keep mel collapsed

128 mel bands x 2 stats = 256 extra fields. Most of them are
highly correlated (adjacent bands produce similar values).
Dimensionality-reduction of mel (via PCA or an autoencoder) would
give better compression with similar information preservation.
MFCCs already do exactly this: DCT of log-mel is a principled
dimensionality reduction.

If you want mel-derived features beyond MFCC, consider just
increasing MFCC count to 20 or 26 instead of expanding raw mel.

---

## Current similarity strategy

In `commands/similar.py`, similarity uses cosine distance over the
feature vector:

```python
from sklearn.metrics.pairwise import cosine_similarity
sim = cosine_similarity(vec_a.reshape(1, -1), vec_b.reshape(1, -1))[0, 0]
```

Cosine similarity measures the angle between two vectors,
independent of magnitude. Two samples with proportional feature
values (same "shape" of vector) get similarity 1; orthogonal
vectors get 0; opposite vectors get -1.

### Why cosine and not Euclidean

Features have wildly different scales:
- `duration_sec`: 0.1 to 300+
- `sample_rate`: 8000 to 96000
- `mfcc_*_mean`: typically -20 to 20
- `zcr_mean`: 0 to 1
- `rms_mean`: 0 to 1

Euclidean distance on unnormalized features is dominated by the
biggest-magnitude fields (sample rate, then duration). Two samples
with wildly different MFCCs but the same sample rate end up close.

Cosine is less sensitive because it normalizes by vector length,
but it's still biased by the relative magnitude of each dimension.
A proper approach requires per-feature normalization before
distance computation:

```python
# proposed: StandardScaler across the full indexed set
from sklearn.preprocessing import StandardScaler
scaler = StandardScaler().fit(all_feature_vectors)
normalized = scaler.transform(feature_vectors)
sim = cosine_similarity(normalized_a, normalized_b)
```

This is what `acidcat features --ml-ready` does: runs the extraction,
then fits and applies a StandardScaler to produce a normalized CSV.
The MCP `find_similar` tool does not currently do this. Adding
scaler state to the index (or computing a per-query scaler from the
current feature population) is on the near-term work list.

### Weighted similarity

Different features matter differently for different similarity
questions. For "find samples with similar timbre":

```
weight mfcc     highly
weight contrast moderately
weight chroma   zero (pitch isn't timbre)
weight bpm      zero (tempo isn't timbre)
```

For "find samples that could layer harmonically":

```
weight chroma   highly
weight tonnetz  highly
weight bpm      highly
weight mfcc     zero
```

The current implementation treats all features equally, which is
a reasonable default but not optimal for any specific query. A
weighted-similarity API would let the caller (or agent) express
intent:

```python
similar(target, weights={'mfcc': 3.0, 'chroma': 0, 'rhythm': 0})
```

Not currently exposed. Would be a clean addition that doesn't
change storage, just query-time scoring.

---

## Feature versioning

The schema includes a `features_version INTEGER` column for exactly
the kind of migration scenarios above. Current version is 1.

When the feature extractor changes in a way that invalidates old
vectors, bump the version. `reindex_features` can then skip or
recompute rows based on their version:

```python
# pseudocode
for path in indexed_paths:
    stored_version = get_features_version(path)
    if stored_version != CURRENT_VERSION:
        recompute(path)
```

Currently not plumbed through the code, but the schema supports it.

Trigger scenarios for a version bump:

- Adding new fields (contrast per-band, tonnetz per-dim): version 2
- Changing librosa to a version with different defaults: version 3
- Switching from chroma_stft to chroma_cqt for features: version 4

---

## Pipeline failure modes

### Import failures

```python
try:
    import librosa
    import numpy as np
except ImportError:
    from acidcat.util.deps import require
    require("librosa", "numpy", group="analysis")
    return None
```

Returns `None` if librosa isn't available. Upstream callers should
check for `None` and handle gracefully.

### Short audio

```python
if len(y) < 256:
    return None
```

Samples shorter than 256 samples (~6ms at 44.1k) are too short for
meaningful feature extraction. Return `None`.

This is conservative; many one-shots longer than this are still too
short for reliable feature stats. A better threshold might be 5000
samples (~100ms), below which most time-frequency features produce
only a handful of frames.

### librosa errors

```python
try:
    ...
except Exception as e:
    return None
```

Catch-all. If anything in the extraction pipeline fails, return
None. Lossy but prevents a bad file from crashing a batch index.

Improvements:

- Log the specific failure reason instead of silently returning None.
- Distinguish recoverable failures (bad header, unusual codec) from
  unrecoverable (out of memory, disk I/O).
- Have different fallback strategies for different failure types
  (e.g. retry with mono-forced or resampled audio on codec errors).

### Cold librosa

First call after Python starts takes 30-60 seconds because of numba
JIT compilation. Subsequent calls in the same process are fast.
This is why the MCP server benefits from the pre-warm pattern
documented in `architecture.md`.

For batch CLI use (`acidcat features ~/Samples -n 500`), the cold
cost amortizes over the batch. For one-off MCP calls it matters.

---

## Proposed pipeline improvements

Ordered by ease and impact.

### 1. Expand collapsed features

Preserve per-band and per-dimension stats for contrast, chroma,
tonnetz. ~50 → ~96 features. Done in a single commit, bump version
to 2, reindex.

### 2. Per-feature normalization in similarity

Fit a StandardScaler across the current index's feature vectors.
Apply before cosine distance. Significant accuracy improvement with
no schema change.

### 3. Weighted similarity API

Expose `weights` parameter on `find_similar` that multiplies each
feature family's contribution. Enables intent-driven similarity
queries.

### 4. HPR (harmonic-percussive separation) pre-pass

For harmonic-heavy analysis, run `librosa.effects.hpss(y)` and use
just the harmonic component for chroma/tonnetz/key. For
percussive-heavy analysis, use just the percussive component for
onset and beat tracking. Modest compute cost, big accuracy win for
mixed content.

### 5. Confidence/quality fields

Derive per-file "this feature is trustworthy" scores:

- Key confidence: peakiness of chroma (see `chroma_and_key.md`)
- BPM confidence: energy at detected tempo peak in tempogram
- Timbre stability: 1 / (1 + std of mfcc_1)
- Tonality: magnitude of tonnetz vector mean

Low-confidence fields can be excluded from similarity by the
caller.

### 6. Learned embeddings

Eventually, a learned embedding from a pretrained model (CLAP,
encodec, etc.) would outperform hand-crafted features for many
similarity tasks. Not a replacement for the current feature vector
but a parallel field.

Major caveats: adds a large model dependency, introduces
uncertainty about bias and domain mismatch, and complicates the
"zero deps for core metadata" principle. If added, should be
strictly opt-in through a new optional extra like `[embeddings]`.

---

## When to recompute features

Features should be recomputed when:

- The file changes (`mtime` or `size` differs from stored value)
- The extractor version bumps (`features_version < CURRENT_VERSION`)
- The user explicitly requests (`--force` flag on reindex)

Features should NOT be recomputed when:

- Only tags or description change (user-editable metadata doesn't
  affect the feature vector)
- The file moves but content is unchanged (new path, same content,
  but the index uses path as primary key so it looks like a new
  file anyway)

Currently `reindex_features` skips files that already have feature
rows, regardless of version. A small upgrade would check version
and recompute stale rows.

---

## Gotchas

### Sample rate in the vector

We store `sample_rate` but it's not discriminating for music
samples (most are 44.1k or 48k). It's there for provenance and
sanity checking, not for similarity scoring. If computing distance,
this field can be excluded without loss of information.

### Duration dominates distance

If duration is included in the feature vector (it is), samples with
very different durations end up far apart in cosine space
regardless of their timbral similarity. A 0.5 second drum hit and
a 30 second drone cannot possibly be "similar" by Euclidean or
cosine distance if duration is weighted.

Mitigations:

- Exclude duration from similarity computation (use it as a filter
  instead)
- Normalize duration strongly before distance (log-transform or
  quantile-transform)
- Use it only in weighted queries where the caller decides

### NaN handling

Some feature values can be NaN under specific conditions (silent
frames, all-zero inputs). Not currently guarded. A single NaN in
the vector makes cosine distance NaN, breaking similarity.

Fix: post-process features, replace NaN with 0 (or the feature's
median across indexed samples).

### Cross-library comparability

Features computed with different librosa versions can disagree
slightly. For within-library similarity this doesn't matter
(consistent extractor). For cross-library or cross-tool comparison
(sharing vectors with another user's acidcat instance), record the
librosa version in the features blob as provenance.

---

## Summary

The pipeline is a sequence of independent librosa calls, each
producing a feature array, each reduced to mean+std, all assembled
into a flat dict. Simple, robust, and mostly correct for the scope
of "a sample library I can search over."

The primary improvements needed (in priority order):

1. Fix the feature collapse for contrast, chroma, tonnetz.
2. Add StandardScaler normalization before similarity distance.
3. Expose weighted similarity.
4. HPR pre-pass for mixed content.
5. Confidence/quality fields.

Each is a focused change. None require rethinking the architecture.

---

## References

- Peeters, G. (2004). "A large set of audio features for sound
  description." *IRCAM*. Canonical reference for what to include in
  a general-purpose audio feature vector.
- Gouyon, F., et al. (2006). "Evaluating rhythmic descriptors for
  musical genre classification." *AES*. On combining rhythmic and
  timbral features.
- Chen, K., Du, X., Zhu, B., Ma, Z., Berg-Kirkpatrick, T., & Dubnov,
  S. (2022). "HTS-AT: A hierarchical token-semantic audio
  transformer for sound classification and detection." *ICASSP*.
  Modern learned alternative to hand-crafted features.
- librosa feature overview:
  <https://librosa.org/doc/latest/feature.html>
- sklearn similarity metrics:
  <https://scikit-learn.org/stable/modules/metrics.html>
- See `dsp/spectral_features.md`, `dsp/mfcc.md`, `dsp/tonnetz.md`,
  `dsp/chroma_and_key.md`, and `dsp/rhythm_and_bpm.md` for deep
  dives on each feature family this pipeline composes.
