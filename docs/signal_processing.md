# Signal Processing Internals

Math, DSP, and music-theory reference for acidcat's analysis and
harmonic-matching subsystems. Each topic has a dedicated deep-dive
document in `dsp/`.

Last updated: 2026-04-23

---

## Document index

### Harmonic matching

| topic | file | status | covers |
|-------|------|--------|--------|
| [Camelot Wheel](dsp/camelot_wheel.md) | `dsp/camelot_wheel.md` | Complete | pitch-class math, Camelot code lookup, compatibility rules, enharmonic normalization policy |

### Audio analysis

| topic | file | status | covers |
|-------|------|--------|--------|
| [Chroma and key detection](dsp/chroma_and_key.md) | `dsp/chroma_and_key.md` | Complete | chroma_stft vs chroma_cqt, argmax key estimation, Krumhansl-Schmuckler proposal |
| [Rhythm and BPM](dsp/rhythm_and_bpm.md) | `dsp/rhythm_and_bpm.md` | Complete | onset strength envelope, autocorrelation, beat tracking, octave-error correction |
| [Onset detection](dsp/onset_detection.md) | `dsp/onset_detection.md` | Complete | spectral flux variants, peak picking, parameter tuning, alternative methods |
| [Spectral features](dsp/spectral_features.md) | `dsp/spectral_features.md` | Complete | centroid, rolloff, bandwidth, contrast, ZCR, RMS, mel spectrogram |
| [MFCC](dsp/mfcc.md) | `dsp/mfcc.md` | Complete | mel scale, log-mel spectrogram, cepstral DCT, coefficient interpretation |
| [Tonnetz](dsp/tonnetz.md) | `dsp/tonnetz.md` | Complete | tonal centroid geometry, fifths/minor-3rd/major-3rd planes, harmonic distance |
| [Feature pipeline](dsp/feature_pipeline.md) | `dsp/feature_pipeline.md` | Complete | how features.py composes all of the above into the 50-field vector |

### Preprocessing

| topic | file | status | covers |
|-------|------|--------|--------|
| [HPR (harmonic-percussive separation)](dsp/hpr.md) | `dsp/hpr.md` | Complete | median-filter separation, soft masks, use cases for chroma/onset cleanup |

### Classification and similarity

| topic | file | status | covers |
|-------|------|--------|--------|
| [Similarity scoring](dsp/similarity.md) | `dsp/similarity.md` | Complete | cosine over feature vectors, normalization, weighting, clustering |
| [Loop vs one-shot](dsp/kind_inference.md) | `dsp/kind_inference.md` | Complete | duration + acid_beats heuristic, edge cases, proposed augmentations |

---

## Extraction flow

```
    audio file
        │
        ▼
    librosa.load  →  y (float32 array), sr (sample rate)
        │
        ├────────────────┬────────────────┬────────────────┐
        ▼                ▼                ▼                ▼
    onset_strength   chroma_cqt      spectral_*       mfcc
        │                │                │                │
        ▼                ▼                ▼                ▼
    beat.tempo       argmax median   mean / std        mean / std
        │                │                │                │
        ▼                ▼                ▼                ▼
       BPM              key          brightness       timbre
                                     proxies          signature
```

The pipeline never holds references to the audio buffer longer than it
has to. `features.py` loads, computes, returns a dict; `detect.py` does
the same for bpm+key only. No warm caches beyond librosa's internal
numba JIT, which is a process-lifetime concern and not a design choice.

---

## Provenance and confidence

Every field that can come from multiple origins carries a source tag.
Canonical ordering, best to worst:

1. **chunk**: producer-authored metadata embedded in the file (ACID,
   SMPL, ID3 BPM, mutagen `tmpo`). Treated as ground truth unless
   flagrantly wrong.
2. **corrected**: detected value that agreed with a filename value
   after an octave-ratio adjustment (see
   `dsp/rhythm_and_bpm.md`).
3. **detected**: librosa analysis output, no corroborating source.
4. **filename**: regex-extracted from the filename. Usually accurate
   but prone to false positives if the parsing rules are too loose.
5. **oneshot**: file too short for meaningful analysis.
6. **failed**: extraction errored. Fall back to null.

The `source` fields are currently computed in `core/detect.py` but not
yet plumbed into the `samples` table columns. Adding `bpm_source`,
`key_source`, and a numerical `confidence` column is on the near-term
work list.

---

## Why separate chunk-authored from analyzed

The two sources answer different questions:

- **Chunk metadata encodes intent**. The producer set 125 BPM because
  that's what they tracked to. The producer set root note D3 because
  that's the key center of the sample. This is what they meant.
- **Analyzed metadata encodes observation**. librosa ran a beat tracker
  on the waveform and found periodicity at 125 BPM. The chroma vector
  peaked at pitch class 2. This is what the audio is.

Usually these agree. When they disagree, the disagreement is the
signal: the file may have been re-pitched, re-timed, re-purposed, or
mislabeled. A future `compare_metadata(path)` tool would surface this
as a first-class result.

acidcat currently resolves the disagreement by preferring the chunk
value if present, using the analyzed value otherwise, and running a
validation pass on the analyzed BPM to catch librosa's common
octave-doubling failure mode. See `core/detect.py:validate_and_improve_bpm`.

---

## Optional dependencies

All DSP functionality is behind the `[analysis]` extra:

```
pip install acidcat[analysis]
```

This pulls in `librosa`, `numpy`, and `scipy`. Without these, `acidcat
detect`, `acidcat features`, and the MCP `analyze_sample` /
`detect_bpm_key` / `find_similar` tools return a structured error
pointing to the install step.

See `architecture.md` for the full optional-dependency matrix.

---

## Reading order for new contributors

If you're coming to the DSP code fresh:

1. **`camelot_wheel.md`** first. It's closed-form math with no audio
   processing involved. Gives you the musical-theory vocabulary for
   reading the rest.
2. **`chroma_and_key.md`** next (once written). Introduces the
   pitch-class vector representation that feeds into Camelot.
3. **`rhythm_and_bpm.md`** (once written). Onset detection and beat
   tracking as the other half of the "when does a sample play what"
   question.
4. **`spectral_features.md`**, **`mfcc.md`**, **`tonnetz.md`** in any
   order. These are the feature extractors used for similarity work.
5. **`feature_pipeline.md`** last, because it depends on understanding
   every feature type individually.

---

## Conventions

Every `dsp/*.md` document follows the same structure:

1. **What it is**: one paragraph, plain language.
2. **The math**: equations, derivations, motivations.
3. **librosa implementation**: what the library actually computes,
   parameter defaults, edge cases.
4. **acidcat usage**: where in the codebase, what we do with it.
5. **Interpretation**: how to read the numbers.
6. **Gotchas**: pitfalls and alternatives considered.
7. **References**: further reading when applicable.

Equations are rendered in plaintext or simple LaTeX-style markup. No
MathJax dependency. Diagrams are ASCII. Code snippets are Python or
pseudocode, never runnable scripts unless the section specifically
illustrates an implementation.
