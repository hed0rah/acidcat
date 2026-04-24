# Camelot Wheel

Harmonic compatibility math: why two keys sound good together, how the
Camelot notation encodes that, and how `core/camelot.py` implements the
lookup.

Last updated: 2026-04-23

---

## What it is

The Camelot wheel is a DJ-friendly renaming of the circle of fifths. It
assigns every major and minor key a code of the form `NL` where `N` is
a number 1-12 and `L` is either `A` (minor) or `B` (major). Two keys are
harmonically compatible when their codes are close on the wheel under a
specific set of relations: same code, same number (relative major/minor),
adjacent number same letter (perfect 4th or 5th).

Why the notation exists: most DJs don't want to reason about "Eb major's
subdominant is Ab major and its relative is Cm." They want to see `5B`
and know that `5A`, `4B`, and `6B` are safe.

---

## The circle of fifths, briefly

Western tonal music uses 12 pitch classes numbered 0-11:

```
pc 0  C
pc 1  C#  (Db)
pc 2  D
pc 3  D#  (Eb)
pc 4  E
pc 5  F
pc 6  F#  (Gb)
pc 7  G
pc 8  G#  (Ab)
pc 9  A
pc 10 A#  (Bb)
pc 11 B
```

The circle of fifths orders these so each neighbor is a perfect fifth
(7 semitones) away:

```
C -> G -> D -> A -> E -> B -> F# -> C# -> G# -> D# -> A# -> F -> C
```

Two keys feel harmonically close when they share many of the same notes
in their scales. A perfect-fifth relationship shares 6 of 7 scale tones,
which is why it's the canonical "safe" modulation. A perfect-fourth
relationship (the inverse, also 6 shared tones) is equally safe. The
relative minor of a major key (the key starting on its 6th scale degree)
shares all 7 notes, just with a different tonic. That's 3 of the 4 most
useful compatibility relations right there.

---

## The Camelot numbering

Camelot walks the circle of fifths starting from A minor / C major and
assigns sequential numbers. A and B labels distinguish minor (inner ring)
from major (outer ring).

```
            1B (B maj)
         12B      2B
   (E maj)        (F# maj)

 11B                    3B
(A maj)              (Db maj)

10B    inner ring is       4B
(D maj)   A (minor)    (Ab maj)
          outer ring is
 9B          B (major)        5B
(G maj)                   (Eb maj)

   8B                    6B
(C maj)              (Bb maj)
         7B
      (F maj)
```

And for the minor ring, same wheel, same numbers, different tonics:

```
            1A (Abm / G#m)
         12A      2A
   (C#m)           (D#m / Ebm)

 11A                    3A
(F#m)                   (Bbm)

10A                      4A
(Bm)                     (Fm)

 9A                       5A
(Em)                     (Cm)

   8A                    6A
  (Am)                  (Gm)
         7A
       (Dm)
```

Same number means relative major/minor. `8A` (Am) and `8B` (C) share all
7 scale tones. That's why the "letter swap" relation is compatible.

---

## Compatibility rules

For any code `NL` (say `8A`, which is A minor), the harmonically
compatible set is:

| relation | code | example for 8A |
|----------|------|----------------|
| same key | `NL` | `8A` (A minor) |
| relative | `N{swap L}` | `8B` (C major) |
| down one | `(N-1)L` | `7A` (D minor) |
| up one | `(N+1)L` | `9A` (E minor) |

Wrapping: going down from `1A` gives `12A`, going up from `12A` gives `1A`.
The wheel is cyclic.

The down-one / up-one relations correspond to perfect fourth and perfect
fifth modulations, respectively. Why that's true mechanically: each step
on the Camelot wheel is a perfect fifth in pitch-class space (7 semitones
modulo 12). Going from `8A` (A minor) up to `9A` gives E minor, whose
tonic E is 7 semitones above A. Going down to `7A` gives D minor, whose
tonic D is 5 semitones above A (which is the same as 7 semitones below
via octave wrap, i.e. a perfect fourth down or a perfect fifth up
depending on register).

This produces four compatible keys per input. Some DJs expand further
(diagonals, energy boost, mood change) but acidcat currently implements
the core four.

---

## Implementation in `core/camelot.py`

### The pitch-class map

```python
_PITCH_CLASS = {
    "c": 0, "b#": 0,
    "c#": 1, "db": 1,
    "d": 2,
    "d#": 3, "eb": 3,
    "e": 4, "fb": 4,
    "f": 5, "e#": 5,
    "f#": 6, "gb": 6,
    "g": 7,
    "g#": 8, "ab": 8,
    "a": 9,
    "a#": 10, "bb": 10,
    "b": 11, "cb": 11,
}
```

Every enharmonic spelling maps to the same integer. `Cb` -> 11 (B),
`B#` -> 0 (C), `Fb` -> 4 (E), `E#` -> 5 (F). This is where enharmonic
equivalence gets resolved in the lookup direction.

### The Camelot map

```python
_CAMELOT_MAP = {
    # major (B ring)
    (11, 0): "1B",   # B
    (6,  0): "2B",   # F#
    (1,  0): "3B",   # Db
    (8,  0): "4B",   # Ab
    (3,  0): "5B",   # Eb
    (10, 0): "6B",   # Bb
    (5,  0): "7B",   # F
    (0,  0): "8B",   # C
    (7,  0): "9B",   # G
    (2,  0): "10B",  # D
    (9,  0): "11B",  # A
    (4,  0): "12B",  # E
    # minor (A ring)
    (8,  1): "1A",   # G#m
    (3,  1): "2A",   # Ebm
    (10, 1): "3A",   # Bbm
    (5,  1): "4A",   # Fm
    (0,  1): "5A",   # Cm
    (7,  1): "6A",   # Gm
    (2,  1): "7A",   # Dm
    (9,  1): "8A",   # Am
    (4,  1): "9A",   # Em
    (11, 1): "10A",  # Bm
    (6,  1): "11A",  # F#m
    (1,  1): "12A",  # C#m
}
```

Reading the pattern: on the major ring, each entry is 7 pitch classes
higher (mod 12) than the previous, which is the fifth-by-fifth stepping.
Same for the minor ring. The ring offset between a major code and its
relative minor is 9 pitch classes: C (pc 0) -> Am (pc 9), which is a
minor 6th up or a major 3rd down, the standard relative-minor
relationship.

### Parsing input keys

`parse_key` is intentionally lenient. It accepts:

- `"C"`, `"Cm"`, `"C#m"`, `"Db"`
- `"A minor"`, `"F#min"`, `"Bb maj"`
- Camelot codes directly: `"5A"`, `"12B"`
- MIDI-note-ish spellings: `"C4"` (treated as C, octave ignored)

The regex is:

```python
r"^\s*([A-Ga-g])([#b]?)\s*(m|min|minor|maj|major|M)?\s*(\d+)?\s*$"
```

Groups:

1. Root letter (A-G)
2. Accidental (# or b, optional)
3. Mode suffix (m/min/minor = minor, maj/major/M = major)
4. Trailing octave digit (ignored, exists to eat "C4" style notations)

Absence of a mode suffix defaults to major. This matches common sample
pack convention where "F" means F major unless explicitly marked `Fm`.

### Camelot neighbors

Given a code, compute the compatible set:

```python
def camelot_neighbors(code):
    num, letter = _split_camelot(code)   # "8A" -> (8, "A")
    other = "B" if letter == "A" else "A"
    down = 12 if num == 1 else num - 1
    up   = 1  if num == 12 else num + 1
    return [
        f"{num}{letter}",    # same
        f"{num}{other}",     # relative
        f"{down}{letter}",   # perfect 4th
        f"{up}{letter}",     # perfect 5th
    ]
```

The wrap logic at `num == 1` and `num == 12` is where the cyclic nature
of the wheel shows up. `12A + 1 -> 1A`, not `13A`.

### Turning neighbors back into pretty names

`compatible_keys(key_str)` returns a set of canonical names like
`{"Am", "C", "Dm", "Em"}`. It does the inverse lookup: for each
neighbor code, find the `(pc, mode)` tuple in `_CAMELOT_MAP`, then
render that via `pitch_class_to_name`:

```python
def pitch_class_to_name(pc, mode):
    name = _NOTE_NAMES_SHARP[pc % 12]
    return name + ("m" if mode == 1 else "")
```

Always renders to sharps. This is the "sharps preferred" policy used
throughout the index.

---

## Enharmonic normalization policy

Flats and sharps that name the same pitch class are equivalent
harmonically. `Db == C#`, `Eb == D#`, `Gb == F#`, etc. Two rarely-used
equivalences: `Cb == B` and `Fb == E` (these are enharmonic but not
accidental-less, so they appear in real sample pack filenames only
occasionally).

### At ingest (parse_bare_key_token in core/detect.py)

When parsing a key out of a filename, flats are normalized to sharps
using a lookup table:

```python
flat_to_sharp = {"Db": "C#", "Eb": "D#", "Gb": "F#",
                 "Ab": "G#", "Bb": "A#",
                 "Cb": "B",  "Fb": "E"}
```

This gives the DB consistent spellings (all sharps) so a simple
equality filter works.

**However**, if the key comes from a chunk (SMPL/ACID) or from the
filename via `parse_key_from_filename` (which preserves the original),
the spelling may end up in the DB as a flat. This is by design: we
don't want to destroy user intent at ingest time.

### At query time (enharmonic_spellings in core/camelot.py)

To prevent the DB storing `F#` from missing a user query for `Gb`, the
query layer expands the input into every equivalent spelling before
hitting SQL:

```python
def enharmonic_spellings(key_str):
    parsed = parse_key(key_str)
    if parsed is None:
        return set()
    pc, mode = parsed
    canonical = _NOTE_NAMES_SHARP[pc]
    suffix = "m" if mode == 1 else ""
    out = {name + suffix for name in _ENHARMONICS.get(canonical, {canonical})}
    out.add(str(key_str).strip())
    return out
```

So a filter for `Gb` becomes SQL:

```sql
WHERE key IN ("Gb", "F#")
```

And a filter for `F#` becomes:

```sql
WHERE key IN ("F#", "Gb")
```

Both match the same DB rows.

### The two-layer policy

| layer | behavior | why |
|-------|----------|-----|
| ingest | normalize flats to sharps when parsing bare tokens | keeps the DB clean |
| chunk/filename | preserve original spelling | don't destroy intent |
| query | expand enharmonics before SQL | match regardless of DB spelling |

This is why `Cb` can legitimately appear in the key column (from
`TAB - Kalimba Shot Cb.wav`) and still match a query for `B`: the query
layer expands `B -> {B, Cb}` before hitting SQL.

---

## Why Camelot is what we use

Alternatives considered:

**Raw note names**: "F" and "Dm" are compatible, but that's only
discoverable by running through the circle-of-fifths logic every time.
Keeps the DB simple but pushes math into the query path.

**Scale degree sets**: represent each key as a set of pitch classes and
compute Jaccard overlap. More expressive (you can encode non-diatonic
scales, modes), but slower, fuzzier, and loses the human-meaningful
"compatible vs not" binary.

**Just the circle of fifths**: sufficient for major-key work, but
conflates relative majors and minors.

Camelot gives us:

- A single lookup table mapping `(pc, mode) -> 2-char code`.
- Four neighbor codes per input, computable in constant time.
- Direct interop with DJ tools that already speak Camelot (Mixed In Key,
  Rekordbox, Serato, Traktor).
- Enharmonic-agnostic matching via the pitch-class integer space.

The tradeoff is that we hardcode diatonic tonality. Modal or non-tonal
material (a drone in Phrygian, an atonal texture, a microtonal sample)
doesn't fit. For those, raw feature-vector similarity via
`find_similar` is the escape hatch.

---

## Edge cases worth knowing

### C4, F3 in the DB

Some sample tools embed the root note as a MIDI-note-with-octave
(`C4`, `F3`, `B2`). These end up in the key column verbatim. The
Camelot parser handles them by consuming the trailing digit in the
regex (group 4) and treating the result as a major key on that pitch
class.

This is not ideal. The value is really a `root_note`, not a `key`.
Future work: route octave-suffixed values into the `root_note` column
at ingest and leave `key` empty in those cases. Until then, querying
for `C` will match `C4` rows via the regex fallback.

### Bare note vs mode-free key

`F` is interpreted as F major. `F3` is interpreted as F major (octave
ignored). There's no way to currently express "F as a root note, mode
unknown." If we ever need that distinction, it goes in `root_note`.

### Cb and B

They're the same pitch class but different spellings. Preserved in the
DB, normalized at query time. Users who want strict spelling matching
would need an escape hatch flag; not currently exposed.

### Modal content

`Dorian on D` has the same pitch classes as `C major` and `A minor`.
acidcat doesn't model modes, so a `D dorian` sample labeled `Dm` will
be Camelot-compatible with `D minor` even though the parallel major of
D (D major) would be more accurate from a modal standpoint. This is a
limitation of the underlying model, not a bug in the parser.

---

## Test vectors for verification

Useful for sanity-checking any change to the parser or lookup table.

| input | parse_key | camelot | compatible_keys |
|-------|-----------|---------|-----------------|
| `"C"` | `(0, 0)` | `8B` | `{C, Am, F, G}` |
| `"Am"` | `(9, 1)` | `8A` | `{Am, C, Dm, Em}` |
| `"F#"` | `(6, 0)` | `2B` | `{F#, D#m, B, Db}` |
| `"Gb"` | `(6, 0)` | `2B` | `{Gb, F#, D#m, Eb, B, Db}` (enharmonic expansion) |
| `"Cb"` | `(11, 0)` | `1B` | `{B, Cb, G#m, E, F#}` |
| `"8A"` | `(9, 1)` | `8A` | `{Am, C, Dm, Em}` (same as Am) |
| `"C4"` | `(0, 0)` | `8B` | `{C, Am, F, G}` (octave ignored) |
| `"Bbm"` | `(10, 1)` | `3A` | `{Bbm, A#m, Db, Abm, Ebm, D#m, G#m}` (enharmonic expansion) |

When the direct neighbor and its enharmonic spelling both resolve, the
`compatible_keys` output contains both. This is a feature, not a bug:
the DB might contain either spelling and both should match.

---

## Proposed extensions

None of these are implemented. Notes for future consideration.

### Energy/mood shifts

DJ literature defines two additional useful relations:

- **Energy boost**: +7 on the wheel (up a whole step, same letter).
  `8A -> 3A`. Pitches the key up a major second, common for set-rising
  transitions.
- **Mood change**: diagonal moves (e.g. `8A -> 9B`). Shifts major/minor
  while also stepping the wheel, good for contrast.

These could be exposed via an optional `extended: true` flag on
`find_compatible`.

### Semitone and tritone filters

Some producers want "anything within 3 semitones" or "tritone-away
only." These require different pitch-class math than the wheel. A
separate `find_by_pc_distance` primitive would cover this without
muddying Camelot.

### Mode-aware scoring

Real modal music (Phrygian flamenco, Dorian jazz) shares more tones
with some keys than others. A full modal-scale Jaccard score would be
more accurate for these cases but significantly more expensive. Would
live as a separate primitive, not an override of Camelot.

### Configurable BPM tolerance

Current default is 6%, derived from the commonly accepted DJ rule of
thumb that most tracks can be pitched ±6% without dramatic timbre
artifacts. Could be a user preference in config, not just a per-call
argument.

---

## References

- **Camelot Wheel**: Mark Davis, "Harmonic Mixing." Original notation
  introduced in the 1980s, popularized by Mixed In Key software in
  the 2000s.
- **Circle of Fifths**: standard Western music theory, treated in
  Krumhansl's *Cognitive Foundations of Musical Pitch* (1990) for the
  cognitive-psychology angle.
- **Pitch class set theory**: Allen Forte, *The Structure of Atonal
  Music* (1973). Not directly relevant to tonal Camelot work, but
  useful background on pitch-class arithmetic.
- **librosa chroma**: see `dsp/chroma_and_key.md` for how we get from
  an audio waveform to a key estimate that can be fed into this
  Camelot machinery.
