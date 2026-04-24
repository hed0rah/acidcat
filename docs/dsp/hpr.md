# HPR (Harmonic-Percussive Separation)

Decomposing an audio signal into its harmonic (sustained tonal) and
percussive (transient rhythmic) components. Cheap, effective, and a
meaningful accuracy win for any analysis that only cares about one
half of a mixed signal.

Last updated: 2026-04-23

---

## What it is

Most music has two kinds of content sitting in the same spectrogram:

- **Harmonic content**: horizontal stripes in the spectrogram. A
  sustained note lights up specific frequency bins over many
  consecutive time frames. Pads, vocals, basslines, guitar chords.
- **Percussive content**: vertical stripes. A drum hit lights up
  many frequency bins simultaneously in a single time frame.
  Kicks, snares, cymbals, plucks.

HPR separation pulls these apart into two signals that sum (roughly)
to the original. You end up with a "percussion-only" version and a
"harmonic-only" version of the same audio.

For analysis, this is a big deal. Running key detection on a
drum-heavy loop is hard because the drums flood the chroma. Run key
detection on the harmonic residual instead and the pitch content is
suddenly clear. Running beat tracking on a pad-heavy track is hard
because the sustained tones create false onsets. Run it on the
percussive residual and onsets are sharp.

acidcat doesn't currently use HPR, but it's the single most
impactful preprocessing step we could add. Most documented accuracy
improvements in chroma/key/beat-tracking papers since ~2010 rely on
it as a preprocessing step.

---

## The intuition

Look at a spectrogram:

```
freq
 ^
 |  ■■■■■■■■■■■         ■           ■■■    (drum hit at two moments)
 |                                          (mostly vertical)
 |  ■   ■       ■   ■       ■
 |       ■   ■       ■          ■
 |   ■        ■        ■            ■       (sustained tones)
 |       ■    ■   ■    ■    ■       ■      (mostly horizontal)
 |                                           
 +─────────────────────────────────────> time
```

A drum hit is visually a vertical stripe. A sustained note is
visually a horizontal stripe. You can tell them apart just by
looking. The question is how to do that mathematically.

The answer (Fitzgerald 2010): median filter the spectrogram twice.

- A **horizontal median filter** (along the time axis) smooths out
  anything that doesn't persist. Transients get flattened; sustained
  tones survive. The output is the harmonic estimate.
- A **vertical median filter** (along the frequency axis) smooths out
  anything that isn't broadband. Sustained tones get flattened;
  transients survive. The output is the percussive estimate.

Then build soft masks from the two estimates and multiply them back
into the original spectrogram. Invert back to time domain. Done.

---

## The math

### Starting point: a magnitude spectrogram

```
    S[k, t]     magnitude at frequency bin k, time frame t
```

### Horizontal median filter

For each bin `k`, replace the value at frame `t` with the median of
its neighbors along time:

```
    H[k, t] = median( S[k, t-w : t+w] )
```

for some window width `w` (e.g. 17 frames, ~200ms at 44.1k/512 hop).

A transient event at frame `t` has high energy only at that one
frame; the median of a window around it is dominated by the quiet
neighbors, so `H[k, t]` is low. A sustained tone at frame `t` has
similar energy in surrounding frames; the median matches the
original, so `H[k, t] ≈ S[k, t]`.

Result: `H` is the harmonic estimate. Transients are suppressed.

### Vertical median filter

For each frame `t`, replace the value at bin `k` with the median of
its neighbors along frequency:

```
    P[k, t] = median( S[k-w : k+w, t] )
```

for some window width `w` (e.g. 17 bins).

A sustained tone lights up a narrow range of frequency bins (the
fundamental and its harmonics). The median across a wider window
includes many quiet bins, so `P[k, t]` is low. A broadband transient
lights up every bin equally; the median matches the original, so
`P[k, t] ≈ S[k, t]`.

Result: `P` is the percussive estimate. Sustained tones are
suppressed.

### Soft masks

From the two estimates, build masks that sum to 1 at every
(k, t) cell:

```
    mask_h[k, t] = H[k, t]^p / ( H[k, t]^p + P[k, t]^p )
    mask_p[k, t] = P[k, t]^p / ( H[k, t]^p + P[k, t]^p )
```

The exponent `p` sharpens the separation. `p = 1` gives a smooth
Wiener-like mask; `p = 2` gives a harder split; `p → ∞` gives
binary masks (cell belongs fully to one side).

Apply masks to the original spectrogram:

```
    S_h[k, t] = mask_h[k, t] * S[k, t]    harmonic component
    S_p[k, t] = mask_p[k, t] * S[k, t]    percussive component
```

By construction, `S_h + S_p = S`. No energy lost.

### Back to time domain

Inverse STFT of the complex spectrogram (using the masks times the
original magnitude, keeping original phase):

```
    y_h = iSTFT( mask_h * X )
    y_p = iSTFT( mask_p * X )
```

Where `X` is the original complex STFT (not just magnitude).

`y_h` is the harmonic-only audio; `y_p` is the percussive-only audio.
Concatenate them by summing and you get back the original.

### Why median and not mean

Median is the whole trick. Mean filters smooth everything; they
don't discriminate between "a single spike" and "a consistent
value." Median treats the spike as an outlier, ignores it, returns
the surrounding consistent value. This is what makes transients
collapse under the horizontal filter (and sustained tones collapse
under the vertical filter).

### Three-way split with a residual

Some implementations add a third "residual" or "noise" component for
content that doesn't fit either archetype:

- strong harmonic mask ∧ weak percussive mask → harmonic
- strong percussive mask ∧ weak harmonic mask → percussive
- both weak → residual (noise, texture, atmospherics)

librosa offers this via a threshold parameter. Most applications
use the two-way split and throw the residual into either side or
drop it entirely.

---

## librosa implementation

Two entry points with slightly different semantics:

### `librosa.effects.hpss`

```python
y_h, y_p = librosa.effects.hpss(y, kernel_size=31, margin=(1.0, 1.0))
```

Takes the audio signal directly. Internally runs STFT, applies
masks, runs iSTFT. Returns time-domain harmonic and percussive
components.

Parameters:

- `kernel_size`: median filter width. Scalar applies the same
  width to both filters. Tuple (h_kernel, p_kernel) specifies
  them separately.
- `margin`: sharpness of the mask. `(margin_h, margin_p)`. Higher
  margin gives harder separation, more residual.
- `power`: the exponent in the mask formula. Default 2.

Use when you want the separated time-domain signals (for listening,
re-mixing, or running a non-librosa analysis on them).

### `librosa.decompose.hpss`

```python
D = librosa.stft(y)
D_h, D_p = librosa.decompose.hpss(D, kernel_size=31, margin=1.0)
```

Takes a complex STFT and returns the masked STFT components. Saves
the iSTFT step if you're going to re-compute a spectrogram anyway.

Use when you plan to run chroma, onset, or any spectrogram-based
analysis on the separated signals: skip the iSTFT, feed the masked
STFT straight into the next step.

---

## Use cases

### Clean chroma for key detection

Currently `core/detect.py` runs chroma_cqt on the full signal. For
drum-heavy material, the drum onsets add broadband energy that
flattens the chroma vector, hurting key estimation.

Better:

```python
y_h, y_p = librosa.effects.hpss(y)
chroma = librosa.feature.chroma_cqt(y=y_h, sr=sr)  # harmonic-only
```

The drums don't contribute to the chroma. Key estimates become
more reliable on percussive content.

Cost: one extra librosa call. The HPR itself takes roughly the
same time as a chroma computation, so total is about 2x. For
per-file analysis this is acceptable.

### Clean onsets for beat tracking

librosa's beat tracker often struggles with pad-heavy or drone-heavy
material because sustained tones create false onsets (the low-level
energy fluctuations from sustained notes look like onsets). The
percussive component removes those.

```python
y_h, y_p = librosa.effects.hpss(y)
onset_env = librosa.onset.onset_strength(y=y_p, sr=sr)  # percussive-only
tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
```

Cleaner onsets, better BPM estimates on ambient-meets-drums
material.

### Cleaner timbral features

For samples where you want timbre of the "pitched" part:

```python
y_h, y_p = librosa.effects.hpss(y)
mfcc = librosa.feature.mfcc(y=y_h, sr=sr)     # timbre of harmonic part
```

Or for drum-sample similarity where you want only the transient:

```python
mfcc = librosa.feature.mfcc(y=y_p, sr=sr)     # timbre of percussive part
```

A drum loop analyzed for MFCC on the percussive component produces
features centered on the drum kit's character, independent of
whatever melodic element is layered over it.

### Tonnetz on harmonic only

Same argument as chroma. Percussive events create tonnetz noise
because the chroma is noisy. Running on `y_h` stabilizes the tonal
centroid trajectories.

---

## acidcat integration plan

The cleanest way to add HPR without disrupting the existing
pipeline:

### Add an optional `mode` parameter to the extractors

```python
def estimate_librosa_metadata(filepath, hpr_mode=None):
    y, sr = librosa.load(filepath, sr=None, mono=True)

    if hpr_mode == "harmonic":
        y_h, _ = librosa.effects.hpss(y)
        y = y_h
    elif hpr_mode == "percussive":
        _, y_p = librosa.effects.hpss(y)
        y = y_p
    elif hpr_mode == "auto":
        # default to harmonic for key detection
        y_h, _ = librosa.effects.hpss(y)
        y_for_chroma = y_h
        # use full signal for BPM
        y_for_tempo = y
```

Default mode: `None` (current behavior, no HPR). Opt-in per tool.

### Automatic mode selection

Better still: detect whether the signal has mixed content and apply
HPR automatically. A simple heuristic:

```python
def needs_hpr(y, sr):
    # measure harmonic-percussive balance in the first HPSS pass
    D = librosa.stft(y)
    D_h, D_p = librosa.decompose.hpss(D)
    harmonic_energy = np.sum(np.abs(D_h)**2)
    percussive_energy = np.sum(np.abs(D_p)**2)
    ratio = min(harmonic_energy, percussive_energy) / \
            (harmonic_energy + percussive_energy + 1e-9)
    return ratio > 0.15  # substantial energy in the minor component
```

If both components have meaningful energy, HPR is worthwhile. If
one dominates (pure drum loop, pure drone), HPR isn't needed.

The cost of this check is the HPR itself. So in practice you just
run HPR unconditionally when the analysis downstream benefits from
it, and skip it when the analysis doesn't care about the split.

### As an MCP tool

A `separate_hpss` primitive that returns structural separation info
(ratio, time-series of harmonic/percussive energy) without
exposing the audio itself would be useful forensically. Something
like:

```
separate_hpss(path) -> {
    harmonic_ratio: 0.72,
    percussive_ratio: 0.28,
    content_type: "mostly harmonic",
    dynamic: true        # whether content changes over time
}
```

This lets an agent reason about a sample's character without
downloading or processing audio.

---

## Gotchas

### Parameter tuning matters

The default `kernel_size=31` works for typical pop/rock/electronic
material at 44.1k. For very different material the defaults may
need adjustment:

- **Fast drums**: smaller kernel (17 or 19) captures shorter
  transients as percussive.
- **Slow pads**: larger kernel (51 or 63) better captures
  sustained tones without leaking.
- **Odd sample rates**: kernel should scale proportionally if you
  care about preserving time/frequency resolution semantics.

For acidcat's general-purpose use, the default is fine.

### Soft masks leak

Even with `power=2` (default), some harmonic content leaks into
`y_p` and vice versa. Not a problem for most analysis because the
dominant character is correct, but fine-grained timbral separation
is imperfect.

For "clean isolation" use cases, spectrogram-based neural methods
(Spleeter, Demucs) are much better but dramatically more expensive
and require large models.

### Phase and transients

The iSTFT uses the original phase, which is correct for the
dominant component at each cell. For cells where both harmonic and
percussive are present, phase belongs to neither cleanly. You can
hear artifacts in the resulting audio, typically a slight "phase
pumping" quality. Not audible if you're just running further analysis
on the result.

### Harmonic content can fail the "horizontal stripe" test

Pitched percussion (tuned toms, melodic synth leads with fast
attacks) straddles the line. A tuned tom hit is percussive in
attack and harmonic in decay. HPR usually assigns the attack to
percussive and the decay to harmonic, which is often the right
split for analysis purposes.

Vibrato and tremolo (wobbling frequencies) can also partially leak
into percussive because the frequency modulation looks slightly
"vertical" in short-window spectrograms.

### Noise and residual

True noise (hiss, tape noise, broadband texture) doesn't fit
either category. HPR typically splits it roughly evenly between
harmonic and percussive outputs. If noise is a meaningful part of
the sample's character, consider the three-way split with explicit
residual.

### Computational cost

HPR roughly doubles analysis time per file. For a 1449-file index
running the current feature extraction path, add another pass of
chroma+onset on harmonic+percussive components and you've roughly
tripled total compute. Batch feature extraction with HPR
preprocessing is a bigger undertaking.

For on-demand query (one file at a time), HPR cost is imperceptible
to the user.

---

## When NOT to use HPR

### Pure drum libraries

If you know a priori the sample is a drum one-shot, running HPR
is wasted compute. The percussive component will be nearly
identical to the input, and the harmonic component will be
essentially empty.

A `kind` prefilter (see `kind_inference.md`) can skip HPR for
one-shots.

### Pure tonal libraries

Same argument in reverse. A pad or drone has essentially zero
percussive content. Running HPR produces a nearly-empty y_p and a
y_h that matches the input.

### Extremely short samples

HPR needs enough time frames to compute the horizontal median.
Samples under ~300ms (about 26 frames at 512 hop) don't have
enough context for the median filter to work. Results will be
unreliable.

### Where simple features suffice

If the downstream task is "measure brightness" (spectral centroid),
HPR doesn't help because the centroid is a global measure that
averages everything. The overhead is pure waste.

Use HPR specifically when the analysis targets one component type:
pitch/chroma/key/tonnetz benefits from harmonic-only; onset/BPM
benefits from percussive-only. General timbral descriptors don't
care.

---

## Alternatives

### Spleeter / Demucs (deep learning)

Much better separation quality. Can extract vocals, bass, drums,
"other" as separate stems. Cost: multi-hundred-MB model weights,
significantly slower, GPU-preferred.

Not the right choice for acidcat given the Unix-tool philosophy
and the zero-dependencies-for-core principle. If an opt-in heavy
preprocessing path becomes useful, this is what would live in a
hypothetical `[spleeter]` extra.

### REPET (REpeating Pattern Extraction Technique)

Designed to separate repetitive (loop-like) content from unique
elements. Good for stripping a backing loop from a vocal, different
problem from HPR. Less relevant for sample-library use.

### NMF (Non-negative Matrix Factorization)

Classical method for source separation. Decomposes a spectrogram
into a product of two non-negative matrices (basis × activation).
More flexible than HPR (can separate into N components) but much
harder to parameterize sensibly for arbitrary audio.

HPR is specifically the "two-components-with-well-known-structure"
case where the math is cheap and the result is interpretable.

### Filterbank-based separation

Split into frequency bands and apply different thresholds per band.
Crude but cheap. Doesn't actually separate harmonic from percussive;
only separates by frequency range.

---

## Summary

HPR is the single most impactful preprocessing step we could add to
the analysis pipeline. The math is clean (two median filters), the
library support is excellent (librosa native), the accuracy
improvements are well-documented across the ISMIR literature.

Primary applications in acidcat:

- Clean chroma input for key detection (harmonic only)
- Clean onset input for BPM/beat tracking (percussive only)
- Cleaner timbral features for mixed-content samples

Primary costs:

- Roughly 2x-3x analysis time
- Some parameter tuning for edge-case material
- Not meaningful on single-component samples (drum-only, pad-only)

On the roadmap as a medium-term improvement. Best implemented as
an opt-in `mode` parameter on the extractor functions, with an
`auto` mode that detects mixed content and applies HPR only where
it helps.

---

## References

- Fitzgerald, D. (2010). "Harmonic/percussive separation using
  median filtering." *Proceedings of the International Conference
  on Digital Audio Effects (DAFx-10)*. The foundational paper
  introducing the median-filter approach.
- Driedger, J., Müller, M., & Disch, S. (2014). "Extending
  harmonic-percussive separation of audio signals." *ISMIR 2014*.
  Adds the three-way split with residual and refines the margin
  parameter.
- Rafii, Z., & Pardo, B. (2011). "A simple music/voice separation
  method based on the extraction of the repeating musical
  structure." *ICASSP 2011*. REPET, the related-but-different
  method.
- librosa HPSS documentation:
  <https://librosa.org/doc/latest/generated/librosa.effects.hpss.html>
- See `dsp/chroma_and_key.md` for key detection that benefits from
  HPR preprocessing.
- See `dsp/rhythm_and_bpm.md` for BPM estimation that benefits
  from HPR preprocessing.
- See `dsp/feature_pipeline.md` for where HPR would integrate in
  the extractor.
