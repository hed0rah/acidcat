# Tonnetz (Tonal Centroid Features)

A 6-dimensional geometric representation of harmonic content derived
from chroma vectors. Captures tonal relationships that are
music-theoretically meaningful but hidden in the raw 12-dimensional
pitch-class representation. Useful for distinguishing harmonic
content from non-harmonic content, and for measuring harmonic
similarity.

Last updated: 2026-04-23

---

## What it is

The Tonnetz (German for "tone network") is a geometric space whose
points represent tonal content of a short audio frame. It has 6
dimensions organized into three 2D coordinate planes, each capturing
a different harmonic interval:

- dimensions 0-1: **perfect fifth** circle (C-G-D-A-E-B-F#-...)
- dimensions 2-3: **minor third** circle (C-Eb-Gb-A-C)
- dimensions 4-5: **major third** circle (C-E-G#-C)

A chroma vector (12-dim) gets projected into this 6-dim tonal space
by weighted summation along each circle. Points close together in
the Tonnetz represent keys or chords that are harmonically close in
Western tonal music.

The name and construction come from Euler's 1739 tuning network,
formalized for signal processing by Harte, Sandler, and Gasser in
the 2000s.

---

## Why not just use chroma

Chroma is 12-dimensional. Tonnetz is 6-dimensional. Both represent
pitch content. What does Tonnetz give you that chroma doesn't?

### Interval relationships are linear in Tonnetz

In chroma space, the relationship between two notes is encoded in
*which* pitch classes are active, not *how they relate*. Moving from
C (pc 0) to G (pc 7) requires rotating the chroma vector by 7
positions, which is a non-obvious operation in 12-D vector space.

In Tonnetz space, the same move is a simple rotation in the
fifths plane. Harmonic relationships become linear-algebraic
operations instead of combinatorial pattern matching.

### Harmonic similarity has a natural metric

Two keys like C major and G major are harmonically close (one step
apart on the circle of fifths). In raw chroma, the C-major chroma
vector and the G-major chroma vector aren't particularly similar by
cosine distance (they share 6 of 7 notes, but the histogram
distributions differ).

In Tonnetz, C major and G major are literally adjacent points in
the fifths plane. Euclidean distance in Tonnetz space approximates
harmonic distance.

### Tonal vs non-tonal content is easier to separate

A chord produces a clustered point in Tonnetz. Noise or inharmonic
content produces a diffuse spread. The difference between a clean
chord and a dissonance or noise cluster is visually and numerically
obvious.

### Dimensionality reduction with musical meaning

PCA would reduce chroma to 6 dimensions mathematically. Tonnetz
does the same reduction but the resulting axes have music-theoretic
meaning. When you inspect a feature vector row, you can reason
about it.

---

## The math

### The three circle projections

Each of the 12 pitch classes has a unique position on each of three
circles, corresponding to stepping through the pitch classes by 7,
3, and 4 semitones respectively:

**Perfect fifth circle** (step by 7 semitones, mod 12):

```
    C -> G -> D -> A -> E -> B -> F# -> C# -> G# -> D# -> A# -> F -> C
    pc  0    7    2    9    4    11   6     1     8     3     10    5    0
```

**Minor third circle** (step by 3 semitones):

```
    C -> D# -> F# -> A -> C
    pc  0     3     6    9    0
```

**Major third circle** (step by 4 semitones):

```
    C -> E -> G# -> C
    pc  0    4    8     0
```

The minor-third and major-third circles are shorter because 12 /
gcd(12, 3) = 4 and 12 / gcd(12, 4) = 3. Each has only 4 or 3 unique
pitch classes before returning to start.

### The 6D projection matrix

Each pitch class gets assigned 6 coordinates: two per circle. The
position of pitch class `n` on each circle is given by mapping the
circle step to an angle:

For the fifths circle (12 positions around a full circle):

```
    angle_fifth[n] = 2π * position_on_fifth_circle(n) / 12
    x_fifth[n]     = cos(angle_fifth[n])
    y_fifth[n]     = sin(angle_fifth[n])
```

Similarly for minor thirds (angle = 2π * position / 4) and major
thirds (angle = 2π * position / 3).

Arranging these into a 6x12 matrix `T` where each column is one
pitch class's 6D Tonnetz coordinate, the projection of a chroma
vector `c` (length 12) into Tonnetz space is:

```
    tonnetz_vector = T @ c
```

One matrix-vector multiply. The output is a 6-vector.

### Geometric interpretation

Each pair (fifth, minor third, major third) spans a 2D plane. The
projected chroma is a point in each of three 2D planes.

**Fifths plane**:
- C is at (1, 0)
- G is at (cos(30°), sin(30°))
- D is at (cos(60°), sin(60°))
- ...and so on around the unit circle

A pure C chroma (all energy at pc 0) projects to (1, 0) in the
fifths plane. A pure G projects to (cos 30°, sin 30°). A chord of
C and G projects to the midpoint, which has smaller magnitude but
lies on the line between them.

The angle of the point indicates the "position" in fifths space;
the magnitude indicates "how tonal" the content is (bigger = more
concentrated around a specific fifth).

Same geometric intuition for minor thirds (4 equally-spaced points
on a circle) and major thirds (3 equally-spaced points).

### Why these specific intervals

- **Perfect fifth**: most consonant interval, defines tonal center.
- **Major third**: defines major tonality.
- **Minor third**: defines minor tonality.

Together they span all the relationships needed to place a chord or
key in tonal space. Other intervals (seconds, sevenths, tritones)
are derivable from combinations of these three.

### Neo-Riemannian theory connection

The 2D minor-third and major-third planes correspond to Riemannian
transformations (P, L, R) that map triads to related triads. This
is a deep connection to 19th-century music theory that formalizes
"smooth" chord progressions.

Not directly needed for acidcat's current use, but notable because
the Tonnetz representation is why some modern music-theory tools
can reason about chord progressions computationally.

---

## librosa implementation

```python
tonnetz = librosa.feature.tonnetz(y=y, sr=sr, chroma=None)
```

Returns shape `(6, T)` where `T` is the number of frames.

If `chroma=None`, librosa computes chroma internally using
`chroma_cqt`. You can pass a pre-computed chroma matrix if you've
already done it for other purposes (saves compute).

The projection matrix is hardcoded in librosa according to
Harte, Sandler, and Gasser's definition.

---

## acidcat implementation

In `core/features.py`:

```python
tonnetz = librosa.feature.tonnetz(y=y, sr=sr)
features['tonnetz_mean'] = np.mean(tonnetz)
features['tonnetz_std']  = np.std(tonnetz)
```

The full tonnetz matrix is 6x T, but acidcat collapses to a single
mean and std across all 6 dimensions and all frames. This loses
most of the interesting information: a point in fifths space is
different from a point in major-thirds space, but the collapse
conflates them.

**This is the same collapse problem noted in `spectral_features.md`
for contrast, mel, and chroma.** The proper storage would be 12
values (6 dimensions x 2 stats), giving per-plane position and
spread.

Fixing this would improve similarity discrimination meaningfully
for harmonic content. Not fixing it means two chords with similar
overall tonal magnitude but different tonal positions can't be
distinguished.

Future improvement plan:

```python
# proposed: preserve per-dimension stats
for i, dim in enumerate(['fifth_x', 'fifth_y', 'minor3_x', 'minor3_y', 'major3_x', 'major3_y']):
    features[f'tonnetz_{dim}_mean'] = np.mean(tonnetz[i])
    features[f'tonnetz_{dim}_std']  = np.std(tonnetz[i])
```

Adds 10 fields to the feature vector, makes Tonnetz actually useful.

---

## Interpretation

A 6D Tonnetz vector can be read as three 2D points. For a single
frame, each point indicates:

- **Angle**: which key or chord region the frame sits in for that
  interval type.
- **Magnitude**: how concentrated the tonal content is. Close to 0
  means diffuse (multiple chords sounding, or noise). Close to 1
  means a single clear tonality.

### Example vectors

```
C major chord (C-E-G), sustained:
fifth plane:  (0.7, 0.2)    # points toward C direction
minor3 plane: (0.4, 0.1)    # weak, C and E are far apart on m3 circle
major3 plane: (0.8, 0.0)    # strong, C and E are adjacent on M3 circle

G major chord (G-B-D), sustained:
fifth plane:  (0.6, 0.4)    # rotated from C position (G is 1 step away on fifths)
minor3 plane: (0.3, 0.2)
major3 plane: (0.2, 0.7)

C major going to G major:
fifth plane: trajectory rotates by 30° between frames
```

Distance between C major and G major in Tonnetz space is smaller
than distance between C major and F# major (because F# is 6 steps
away on the fifths circle, the maximum distance).

---

## What Tonnetz is good for

### Chord change detection

A consistent Tonnetz vector across frames = same chord. A large
jump between frames = chord change. This is the basis for
automatic chord recognition systems. acidcat doesn't currently do
chord segmentation, but the feature is available if we ever add it.

### Key similarity

Two clips in the same key produce clustered Tonnetz positions. Two
clips in adjacent keys (fifth-related) are close. Two clips in
distant keys (tritone apart) are far.

This is a more nuanced version of Camelot compatibility: Camelot
is a discrete 24-class categorization; Tonnetz is a continuous
space. Combining both gives you the discrete "are these
compatible" answer plus a continuous "how similar are they"
score.

### Harmonic vs inharmonic discrimination

A drum loop has essentially random Tonnetz values per frame (no
stable tonal center). The mean across frames is near zero; the std
is high. A harmonic loop has a stable mean and low std.

Even without the per-dimension breakdown, the current
`tonnetz_mean` and `tonnetz_std` features can flag "this sample
has tonal character" vs "this sample doesn't."

### Style differentiation

Different genres have different characteristic Tonnetz
trajectories. A blues progression (I-IV-V) traces a specific shape
in fifths space. A jazz ii-V-I traces a different shape. Pop
diatonic progressions cluster in a compact region. Extended
harmonies (maj9, sus4 stacks) spread out differently.

For sample-library purposes this is mostly unused, but the
information is there.

---

## What Tonnetz is not good for

### Timbre similarity

Tonnetz says nothing about timbre. Two different instruments
playing the same chord have identical Tonnetz profiles. That's a
feature, not a bug, because it means Tonnetz gives you a clean
harmonic representation independent of timbre. But if you want to
find "sounds similar" matches, use MFCC.

### Key detection (on its own)

Tonnetz is derived from chroma. It encodes the same pitch
information, just in a different geometry. For the specific task
of key estimation, chroma with Krumhansl-Schmuckler correlation
(see `chroma_and_key.md`) is more direct. Tonnetz adds geometric
interpretability but doesn't uniquely determine a key unless you
combine it with mode information.

### Modal or non-Western content

Tonnetz is built around Western tonal assumptions: diatonic scales,
triadic harmony, equal temperament. Modal jazz, non-Western scales,
just-intonation material, microtonal content, and atonal music
can all produce Tonnetz vectors, but the geometric interpretation
breaks down.

A piece in Phrygian mode projects to Tonnetz points that don't
match either the parallel major or parallel minor's typical
location. The interpretation "tonal center at this angle" is
misleading for modal content.

### Short samples

A one-shot (drum hit, pluck) is too short to have stable Tonnetz
content across frames. The vector reflects whatever transient
spectral content is present, which isn't necessarily the
"tonal center" of the sample.

---

## Gotchas

### Magnitude vs angle aren't independent

A frame with diffuse chroma has small Tonnetz magnitude in all
three planes. A frame with clear tonality has large magnitude in
whichever plane best captures it. When aggregating mean and std
across frames, both components contribute.

Currently acidcat's single mean+std loses this nuance. Fixing the
collapse (see implementation section) would separate "how tonal"
from "what tonal direction."

### Percussive onsets inflate variance

A sample with tonal sustain plus drum hits has high Tonnetz std
because the percussive frames don't fit the tonal geometry. This
is actually useful information (it tells you the sample mixes
tonal and percussive content) but could be misinterpreted as
"harmonically unstable."

### Frame size matters

With `hop_length=512`, frames are ~11.6ms. Chord changes faster
than ~80ms (less than 7 frames) can blur together. For detecting
fast chord changes, shorter hop lengths help; for sample-library
classification where we aggregate across the whole clip, the
default is fine.

### Pre-chroma artifacts propagate

Anything that affects chroma affects Tonnetz downstream. Material
tuned to A4 ≠ 440 Hz produces systematically wrong chroma, which
then produces systematically wrong Tonnetz. Same mitigation:
`librosa.estimate_tuning` for non-standard tunings.

---

## Proposed storage enhancement

Current (loses geometry):

```
tonnetz_mean     single scalar, mean across all 6 dims and T frames
tonnetz_std      single scalar, std across all 6 dims and T frames
```

Proposed (preserves geometry):

```
tonnetz_fifth_x_mean, tonnetz_fifth_x_std
tonnetz_fifth_y_mean, tonnetz_fifth_y_std
tonnetz_minor3_x_mean, tonnetz_minor3_x_std
tonnetz_minor3_y_mean, tonnetz_minor3_y_std
tonnetz_major3_x_mean, tonnetz_major3_x_std
tonnetz_major3_y_mean, tonnetz_major3_y_std
```

Twelve fields instead of two. Dataset size grows by roughly 0.5%.
Discrimination for harmonic similarity improves noticeably.

Even better: add derived features:

```
tonnetz_fifth_magnitude_mean  = mean over T of  sqrt(x^2 + y^2)
tonnetz_fifth_angle_mean       = circular mean of  atan2(y, x)
```

The magnitude/angle decomposition is how Tonnetz is typically
reported in the literature. Implementing circular mean correctly
is a little fiddly (can't just average angles that wrap around),
so stick with x/y components unless the downstream task explicitly
benefits from polar form.

This is near-term work on the roadmap under "feature vector
versioning" since it's a schema change.

---

## Comparison with other tonal features

| feature | dim | encodes | pros | cons |
|---------|-----|---------|------|------|
| chroma | 12 | pitch-class histogram | direct interpretation, standard | high-dim, not octave-invariant geometry |
| tonnetz | 6 | tonal geometry | harmonic distance is metric | needs good chroma input, Western-centric |
| key estimate | 1 (discrete) | best-fit key | compact, compatible with Camelot | lossy, discrete |
| MFCC | 13 | timbral envelope | robust, decorrelated | no harmonic content |

For acidcat, Tonnetz complements chroma and key estimate by
providing a continuous harmonic-similarity metric that neither of
the others offers.

---

## References

- Harte, C., Sandler, M., & Gasser, M. (2006). "Detecting harmonic
  change in musical audio." *Proceedings of the 1st ACM workshop
  on Audio and music computing multimedia*: 21-26. The paper that
  introduced the 6D Tonnetz representation for signal processing.
- Cohn, R. (1997). "Neo-Riemannian operations, parsimonious
  trichords, and their Tonnetz representations." *Journal of Music
  Theory* 41(1): 1-66. The music-theoretic background; formalizes
  triadic transformations on the Tonnetz.
- Euler, L. (1739). *Tentamen novae theoriae musicae*. The original
  tone network concept, centuries before digital signal processing.
- Chew, E. (2014). *Mathematical and Computational Modeling of
  Tonality*. Springer. Detailed treatment of tonal spaces including
  Tonnetz.
- librosa tonnetz documentation:
  <https://librosa.org/doc/latest/generated/librosa.feature.tonnetz.html>
- See `dsp/chroma_and_key.md` for the chroma vectors that feed
  Tonnetz.
- See `dsp/camelot_wheel.md` for the discrete harmonic compatibility
  space that complements continuous Tonnetz.
