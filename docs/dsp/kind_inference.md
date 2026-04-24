# Kind Inference (Loop vs One-Shot)

A small but high-value classification: is this sample a loop or a
one-shot? The heuristic is simple but it prevents a whole class of
"wrong result type" failures in search and compatibility queries.

Last updated: 2026-04-23

---

## What it is

"Kind" in acidcat refers to one of three categories:

- **one_shot**: a single event, meant to trigger once. Drum hits,
  vocal chops, stabs, plucks. Usually under 1 second.
- **loop**: a rhythmic or tonal passage meant to play repeatedly.
  Bars of drums, chord progressions, basslines. Usually 1-16 bars.
- **ambiguous**: can't tell from available metadata. Drones, pads,
  sound design elements, uncertain short files.

The classification is a hint, not a hard category. Samples can live
at edges: a very long one-shot (like a crash cymbal with 6 second
decay) looks like a loop by duration, a very short loop (a 1-beat
fill) looks like a one-shot. The heuristic handles the common case
well and flags edge cases as `ambiguous`.

---

## Why it matters

Most sample-library use cases care about this distinction:

- **Search**: when a user searches for "128 bpm samples," they
  usually want loops, not every one-shot that happens to match
  the BPM band.
- **Similarity**: "find samples like this kick" should return other
  drum hits, not 4-bar kick-heavy loops.
- **Compatibility**: "find samples compatible with this loop"
  should return layering candidates of similar duration (other
  loops, pads), not 0.25-second one-shots.
- **Layering**: loops can be stacked and synced; one-shots are
  rhythmic punctuation.

Without kind inference, `find_compatible` can return one-shots as
matches for loop queries (a real bug observed during testing).

---

## The heuristic

Three signals, combined:

```python
def infer_kind(duration, acid_beats):
    dur = duration or 0
    beats = acid_beats or 0

    # strong signals
    if beats > 0 and dur >= 1.0:
        return "loop"
    if dur < 1.0 and beats <= 0:
        return "one_shot"

    # weaker signals
    if dur >= 2.0 and beats > 0:
        return "loop"
    if dur < 0.5:
        return "one_shot"

    return "ambiguous"
```

The inputs are already in the index:

- `duration`: in seconds, from the RIFF/AIFF parser or librosa.
- `acid_beats`: from the ACID chunk when present. `> 0` means
  producer-authored loop metadata.

Two strong signals that match the archetypes:

1. **ACID beats present + duration >= 1 second**: definitely a
   loop. The producer explicitly marked it as such.
2. **Short duration + no ACID beats**: definitely a one-shot. No
   rhythmic context declared, too short for a musical phrase.

The weaker signals handle samples without ACID metadata:

3. **Duration >= 2 seconds + ACID beats present**: loop (even
   without the 1-second threshold being met, ACID beats are
   authoritative).
4. **Duration < 0.5 seconds**: one-shot (too short to be a loop
   regardless of metadata).

Everything else: `ambiguous`. This includes mid-length samples
(0.5-2 seconds) without ACID metadata, long drones, and sound
design elements that could be either.

---

## Why these thresholds

### 1 second boundary for one-shot

At 120 BPM, one bar is 2 seconds and one beat is 500ms. A 1-second
clip could be:

- 2 beats of a loop (half-bar, common in breakbeat culture)
- A long one-shot with decay (crash cymbal, long kick tail)
- A short vocal phrase

Setting the lower bound at 1 second keeps most legitimate
half-bar loops on the loop side. Dropping the bound would
misclassify those as one-shots.

### 0.5 second boundary for strong one-shot

Anything under 500ms is almost certainly not musical material. A
kick drum decay, a single vocal word, a plucked sample. Even at
240 BPM, one beat is 250ms, so 500ms is 2 beats which is the edge
of loop-ness.

### 2 second boundary for strong loop

At 60 BPM, two seconds is two beats. At 120 BPM, it's four beats
(one bar). The 2-second threshold captures "at least a full bar"
for most common tempos.

### ACID beats is authoritative

If ACID beats is present and greater than zero, the producer
intentionally labeled the clip as a rhythmic pattern. Even a very
short loop (like a 1-beat 170 BPM DnB hit at ~350ms with ACID
beats=1) gets classified as a loop. This is the whole point of
ACID chunks: producer intent.

### `acid_beats == 0` is not authoritative

Important inverse rule: `acid_beats == 0` is treated as equivalent
to `NULL`, not as a one-shot signal. The current implementation
reflects this:

```python
b = acid_beats or 0   # both None and 0 collapse to 0
if b > 0: ...         # only positive values are informative
```

And in the SQL filters:

```sql
-- loop filter (acid_beats=0 does NOT reject, falls through to duration)
WHERE (s.acid_beats > 0 OR s.duration >= 2.0)

-- one_shot filter (explicitly allows acid_beats=0)
WHERE ((s.acid_beats IS NULL OR s.acid_beats = 0)
   AND (s.duration IS NULL OR s.duration < 1.0))
```

**Why this matters in practice**: some commercial sample packs
write an ACID chunk to the file but leave the `beats` field at
zero. The Producer Loops Hypnotize pack is one observed example:
126 BPM wav stems with `chunks: "inst,LIST,ID3 ,acid,strc"` (ACID
chunk present), `acid_beats: 0`, and durations of 7.619s or 15.238s
(exact 2-bar and 4-bar loops). These are unambiguously loops despite
the zero beats field. The duration fallback catches them.

**Do not "fix" this to treat `acid_beats == 0` as a one-shot
signal.** It would misclassify legitimate loops from any pack that
uses this writing convention.

The general rule: `acid_beats > 0` is a strong loop signal;
`acid_beats == 0` is uninformative and must fall through to
duration-based classification.

---

## Integration with the index

Currently kind is computed on the fly, not stored. A proposed
addition to the `samples` table:

```sql
ALTER TABLE samples ADD COLUMN kind TEXT;
-- values: "loop", "one_shot", "ambiguous", null
CREATE INDEX idx_samples_kind ON samples(kind);
```

Populated at index time:

```python
row['kind'] = infer_kind(duration, acid_beats)
```

With this column:

- `search_samples({kind: "loop"})` fast-path filters to loops.
- `find_compatible(target, kind_match=True)` auto-filters to samples
  of the same kind as target.
- Tag taxonomies can be kind-aware ("drums" cluster of one-shots
  vs "drum loops" cluster of loops).

Schema migration is small. This is on the near-term work list.

---

## Augmentations

The simple heuristic works well but misses a few cases. Possible
enrichments if accuracy matters.

### RMS envelope shape

A one-shot typically has a percussive envelope: sharp attack, decay
to silence. A loop typically has a flatter envelope with sustained
energy throughout.

```python
rms_env = librosa.feature.rms(y=y)[0]
decay_ratio = rms_env[-10:].mean() / rms_env[:10].mean()
```

- `decay_ratio < 0.2`: strong decay to silence, likely one-shot.
- `decay_ratio > 0.8`: sustained, likely loop.
- In between: less certain.

Costs a feature extraction step, so not free. Could be computed
lazily only for ambiguous cases.

### Onset density

Number of onsets per second. Loops tend to have regular onset
patterns (one or more per beat). One-shots usually have one big
onset at the start and nothing else.

```python
onsets = librosa.onset.onset_detect(y=y, sr=sr)
density = len(onsets) / duration
```

- Density > 2 per second: rhythmic content, likely loop.
- Density < 0.5: sparse, likely drone or one-shot.

### Spectral stationarity

Loops have relatively stable spectral content over time. One-shots
evolve rapidly during the attack-decay envelope.

```python
mfcc_std = np.std(mfcc, axis=1).mean()
```

- Low mfcc_std: stable timbre, loop-like.
- High mfcc_std: rapidly evolving, one-shot-like.

### Bar-count check

If BPM is known, we can compute "is the duration a clean multiple
of a bar?":

```python
seconds_per_bar = 60 / bpm * 4  # assuming 4/4
bars = duration / seconds_per_bar
is_clean = abs(bars - round(bars)) < 0.05
```

Sample-pack loops are almost always exact multiples (1, 2, 4 bars).
A sample whose duration is an integer number of bars at its
declared BPM is almost certainly a loop. This is the highest-
confidence secondary signal after ACID metadata.

### File-naming patterns

Producers commonly encode kind in filenames:

- `_loop`, `_lp`, `loop_` in filename: strong loop signal
- `_shot`, `_stab`, `_hit`, `_oneshot` in filename: strong one-shot signal
- Drum-name patterns (kick, snare, hat, clap): strong one-shot
- Loop-name patterns (drums, groove, beat): strong loop

A regex-based classifier over filenames would catch many cases
where duration and ACID metadata are unclear.

---

## Proposed enhanced classifier

Combining signals:

```python
def infer_kind_full(sample):
    # strong authoritative signals
    if sample.acid_beats and sample.acid_beats > 0:
        return "loop"
    if sample.duration and sample.duration < 0.3:
        return "one_shot"

    # filename hints
    path_lower = sample.path.lower()
    if any(kw in path_lower for kw in ["_loop", "_lp", "loop_", "groove", "beat"]):
        return "loop"
    if any(kw in path_lower for kw in ["_shot", "_stab", "_hit", "_oneshot",
                                         "kick_", "snare_", "hat_", "clap_"]):
        return "one_shot"

    # bar count check (requires bpm)
    if sample.bpm and sample.duration:
        seconds_per_bar = 60 / sample.bpm * 4
        bars = sample.duration / seconds_per_bar
        if abs(bars - round(bars)) < 0.05 and bars >= 0.5:
            return "loop"

    # duration heuristic (fallback)
    if sample.duration:
        if sample.duration < 1.0:
            return "one_shot"
        if sample.duration > 4.0:
            return "loop"

    return "ambiguous"
```

Runs through signals in confidence order. First match wins. Falls
back to the simple duration heuristic if nothing else fires.

---

## Edge cases

### Drones and pads

Long sustained samples without rhythmic content: duration > 2s,
no ACID beats, probably no clean bar-count match. The classifier
returns "loop" based on duration, which is arguably wrong (a
drone doesn't loop in the rhythmic sense).

Options:
- Add "drone" as a fourth kind category.
- Accept the loop classification and use additional features
  (zcr, onset density) to distinguish "rhythmic loop" from
  "sustained loop" at search time.

Probably option 2 for now. A drone classified as a loop is still
useful in compatibility and layering queries because both are
sustained content.

### Long one-shots with decay

A cymbal crash can decay for 6+ seconds. Classified as loop by
duration. But there's no rhythm, no ACID metadata, and the file
is just a single event.

Fix: the RMS envelope shape augmentation would catch this. If the
envelope decays sharply to silence in the second half, it's a
one-shot regardless of total duration.

Current classifier misclassifies these. Not a priority if the use
case is rare.

### Very short loops

A half-beat fill at 160 BPM is 190ms. ACID-labeled as 1 beat, but
duration < 0.3 threshold would classify it as one-shot. Since ACID
beats > 0 wins in the classifier, this works out correctly.

### Vocal phrases and atmospherics

A 3-second vocal phrase without ACID metadata classifies as loop
by duration. It's not rhythmic, but it's also not a one-shot. Might
actually want "sample" or "clip" as a category.

The current "loop" classification works OK because vocal phrases
do often layer like loops. The distinction might not be worth the
complexity.

---

## Kind filter in search

Once kind is indexed, search by kind is trivial SQL:

```sql
SELECT * FROM samples
WHERE bpm BETWEEN 118 AND 128
  AND kind = 'loop'
LIMIT 20;
```

Or via the MCP:

```
search_samples({
    bpm_min: 118,
    bpm_max: 128,
    kind: "loop"
})
```

New parameter on `search_samples`. Matches the existing filter
pattern.

Similarly for `find_compatible`:

```
find_compatible({
    path: "/path/to/loop.wav",
    kind: "loop"              # default: match target kind
})
```

Default should auto-match to the target's kind. Explicit override
lets the caller cross-kind (e.g. "find one-shots that harmonically
match this loop" for drop-in decoration material).

---

## Gotchas

### Kind is a hint, not truth

The classifier is lossy. There will always be misclassifications.
Treat kind as a soft filter that can be relaxed with an explicit
override, not a hard categorical assertion.

### Mid-duration ambiguity is real

Samples between 0.5 and 2 seconds without ACID metadata are
often ambiguous by design. The classifier marks them as such. UI and
query surfaces should expose this as a first-class category, not
hide it under "loop" or "one_shot" randomly.

### Kind might change with upstream improvements

If `acid_beats` becomes more reliable (better parsers, more format
support), the ACID-beats-authoritative rule becomes more useful.
If RMS envelope becomes part of the index, decay shape becomes a
signal. Kind is a derived field; re-derive it when inputs improve.

### Caching vs recomputing

Store kind in the DB for fast filtering, but mark it as a derived
field. If the inference rules change, all stored kinds should be
re-derived. Same migration pattern as features versioning.

---

## Integration with the agent workflow

Kind classification enables entire query patterns that don't work
without it:

### "Find me drums at 128 BPM"

```
search_samples({
    bpm_min: 126, bpm_max: 130,
    kind: "one_shot",
    text: "drum"            # could be filename/tag hit
})
```

Without kind filter, returns loops too and the user has to manually
sort.

### "Find me tonal loops in F that could go under this loop"

```
find_compatible({
    path: target_loop,
    kind: "loop",            # auto-match from target
    min_duration: 2.0        # at least a 1-bar loop
})
```

Without kind, returns compatible one-shots mixed with loops.

### "Suggest layering candidates for this kick"

```
find_compatible({
    path: kick_oneshot,
    kind: "loop"             # explicit override to cross-kind
})
```

Returns loops that harmonically match the kick's detected root
note, so you can sit the kick under a tonal loop.

---

## References

- No direct academic reference; this is engineering pragmatics.
- The loop/one-shot distinction comes from sample-library
  conventions established by ReCycle / ACID / Ableton Live
  browsers in the 1990s-2000s.
- Duration-based classification is standard in commercial sample
  librarians (Sononym, Atlas, ADSR) but rarely documented.
- See `riff.py` for how `acid_beats` gets into the index.
- See `dsp/rhythm_and_bpm.md` for how `bpm` and `beat_count` are
  computed (needed for the bar-count augmentation).
- See `dsp/similarity.md` for how kind filtering integrates with
  similarity search.
