# Adding a format walker

The worked example throughout is the Ableton `.asd` walker, because it hit
nearly every hazard: a two-byte magic, two byte orders, two schema
generations, an impostor file type, and a field everyone expects the format to
contain that it does not.

## The contract

A walker is a function `inspect_x(filepath, ...) -> (chunks, file_warnings)`.
`filepath` is a path or a Source (`core/infra/source.py`): read it with `_open`,
`_size` and `_name` from `walk/base.py`, and `zip_open` / `gzip_open` from
`core/infra/source.py`, never with `open()` or `os.path` (a test enforces it).
A file beside it (a PSF's library, a cue sheet's BIN) is
`as_source(filepath).sibling(name)`, which is None for bytes in memory.

A **chunk** is a dict: `id`, `offset`, `size`, `summary`, `fields`, `warnings`,
and optionally `payload_base` (the absolute offset that field offsets are
measured from, when it is not `offset + 8`) and `rows` (per-element listing for
`--frames`).

A **field** is built with `_f(off, len, name, value, note)` from
`walk/base.py`, where `off` is relative to the payload base. Add `enc` and
`raw` when the field can be re-encoded byte-for-byte, and `xref` when it is a
pointer into the file.

Where a field is, in one rule: its bytes are at `payload_base + off`.

- A field in the chunk's own header (its id, its size word) has a **negative**
  `off`: a RIFF chunk's id is at `-8`.
- A field that describes this chunk but is stored somewhere else (a MOD
  sample's name in the module header) is positioned the same way and passes
  `remote=True`.
- A value with no bytes of its own (a count, a duration, a decoded name) is
  `off=None`, never `(0, 0)`.
- `xref` only ever means "this field points there".

`test_chunk_geometry.py` reads every positioned field back from its bytes on
every seed. A value that is a transform of its bytes (a count stored minus
one) goes in its `KNOWN_TRANSFORMS` ledger with the reason.

**Walkers degrade, they do not raise.** `walk_file` enforces this at the one
boundary every consumer shares, turning an unexpected exception into zero
chunks plus a warning. Do not rely on that: raise `AbletonError`-style
format errors only where you mean them, and turn everything a real-but-damaged
file can do into a warning. `ACIDCAT_WALKER_RAISE=1` (set in CI) re-raises, so
a walker bug stays a loud traceback.

## The five places to touch

1. **`core/formats/<fmt>.py`** -- the primitives. Parsing, constants,
   structural predicates. No I/O policy, no presentation. This is the module
   another command can import without pulling in the walker.
2. **`core/walk/<fmt>.py`** -- the walker. Turns primitives into chunks and
   fields, and decides what is a warning.
3. **`core/infra/sniff.py`** -- the magic, and the id in `KNOWN_FORMATS`.
4. **`core/walk/__init__.py`** -- one `_WALKERS` entry: `id -> (label, lambda)`.
5. **`tests/test_<fmt>.py`** -- generated fixtures, plus an opt-in real-corpus
   test.

### The invariants that will fail you

Two tests in `tests/test_formats.py` guard the format namespace, and both
caught the Ableton work:

- `KNOWN_FORMATS` must equal the set of string literals returned from
  `sniff.py` **itself**. Returning an id computed in another module passes
  silently at runtime and fails here. Spell the ids out:

  ```python
  ab = abletonmod.sniff_gzip_ableton(filepath)
  if ab == "adg":
      return "adg"        # spelled out so the id stays greppable in this file
  ```

- Every `_WALKERS` key must be in `KNOWN_FORMATS`, so a typo is a loud failure
  rather than a silent dict miss.

## The discipline

### Field-test before you design

Run the primitives over the real corpus before building anything on them. The
`.asd` sample-rate inference was checked against 22 files whose source audio
was also on disk (22/22 exact) and then swept over 1,471 more for crashes and
implausible values. That sweep is cheap and it is the difference between a
walker and a hypothesis.

### Weak magic needs corroboration

`.asd` opens with two bytes. That is not an identification. Detection also
requires the reserved `u32` at offset 6 to be zero and a sane entry count --
and then it is measured:

```
4,994 real non-Ableton files sniffed -> 0 claimed as Ableton
```

**Run that sweep.** A new magic that steals files from another walker is a
regression nobody notices until a corpus report looks wrong.

### Cap every read

A Live Set expands about 20x under gzip; one 336 KB file became 6.9 MB of XML.
Any decompression, any length-prefixed allocation, and any chunk chain needs a
ceiling, and hitting the ceiling must be **reported**, not silent:

```python
from acidcat.core.infra.limits import hit

if truncated:
    warns.append(hit("inflate_bytes", _XML_CAP, len(xml),
                     f"XML exceeded the {_XML_CAP // (1024*1024)} MB cap; counts "
                     f"below describe only the prefix read"))
```

A cap that silently truncates turns into a confident wrong answer -- the house
bug class. The rule is: a cap you hit is a warning, always, and it is made with
`hit(name, limit, used, message)`. `name` is the limit it belongs to
(`read_bytes`, `chunk_payload`, `inflate_bytes`, `work_steps`, `list_rows`,
`frame_rows`, `depth`); `limit` is your constant; `used` is how much the file
asked for (the total when you know it, else the count you reached). That makes
it a coverage note: reported, but never a defect, so `audit` does not fail a
clean file for being large. A plain string is a defect. Put the constant in the
module that reads it, name it `_..._CAP`, and register it in
`tests/test_cap_announcements.py`.

### Say what you do not know

The grid's step ceiling pins the sample rate only when the ceiling is actually
reached. A short file may never reach it, and then the rate is a lower bound.
That distinction is carried through the API (`rate_exact`), into the field note
("lower bound -- grid never hit the cap"), and into a test. Reporting the lower
bound as a reading would be indistinguishable from correct until it wasn't.

### Give every warning a code

A warning is read by scripts, `audit` and the TUI, which select it by its code,
never by its words. Make it with the helper for what it is, from
`acidcat.core.infra.findings`:

```python
warns.append(defect("size.overrun", f"chunk {cid!r} claims {size:,} bytes "
                                    f"but only {avail:,} remain"))
warns.append(environment("sibling.missing", f"names library {lib!r} and it "
                                            f"is not beside this file"))
```

`defect` is the file breaking its format, `environment` is something outside
it (a sibling file), `info` is worth knowing. The codes are in `REGISTRY`; add
one there when none fits, with its kind and severity. A plain string still
works and is reported as `legacy`, but `tests/test_findings.py` counts those
and the count only falls.

### Declare a decoded layer

When a chunk's payload is compressed and you decode it, declare the image as a
layer rather than reporting its fields unpositioned: set `chunk["layer"]`
(decoder name from `core/infra/layers.py`, its params, length and the check
you ran) and `chunk["layer_chunks"]` (your walk of the image, offsets in the
image). Register the decoder if it is new. `walk/ym.py` is the example; the
layer's chunks never go on the flat list.

### Report absence as a finding

Everyone expects `.asd` to hold the tempo. It does not: Live stores warp
markers as (sample position, beat time) pairs and derives tempo from them, so
unwarped audio records none. That was established by checking 599 files whose
filenames state their own BPM and finding the value absent in nearly all of
them. The walker therefore reports **no tempo at all** rather than a plausible
number, and the docstring says why -- so the next person does not spend the
same day on it.

### Let impostors be impostors

129 of the 8,196 files with a `.asd` extension were macOS AppleDouble stubs.
The first instinct was to add `appledouble` as a format id; the test suite
disagreed, because `classify` already names it as a **foreign** file, which is
more useful than a walkable-format label. Deleting the new id was the fix.

When something in your corpus wears the extension but is not the format, check
what the existing machinery already says about it before extending the
namespace.

## Tests

Fixtures must be **generated**, not committed binaries, so a fresh clone can
run them. Build a helper that emits a structurally honest file:

```python
def build_asd(frames, order="<", count=None, reserved=0, tail=b""):
    magic = ab.ASD_MAGIC_LE if order == "<" else ab.ASD_MAGIC_BE
    n = len(frames) + 1 if count is None else count
    ...
```

Cover, at minimum:

- every byte order and variant the format has
- a value the format derives, checked against a known-true answer
- **the honesty of every inference** (the "lower bound" case above)
- damage: a count larger than the file, a non-monotonic table, a chunk length
  past EOF, a truncated header, a damaged compression stream
- the sniffer's *negative* space -- what must NOT be claimed
- a round trip through `sniff.sniff`

Then gate the real corpus behind an environment variable so it is available
without being required:

```python
@pytest.mark.skipif(not os.environ.get("ACIDCAT_ABLETON_CORPUS"), reason=...)
```

**Every test must fail without its fix.** Two tests written during one recent
session passed for the wrong reason and only CI caught them. Mutate the source,
watch the test go red, put it back.

### Check whether someone else has written the format down

Everything above measures acidcat against a fixture that encodes *your* reading
of the format, which cannot catch a field you misread the same way twice. So
before you finish, look for the format in
[kaitai_struct_formats](https://github.com/kaitai-io/kaitai_struct_formats) and,
if it is there, add a comparison to `tests/test_kaitai_cross_check.py`.

That file found `synchsafe` decoding without its mask -- four bytes read as
268 MB, in a function two other readers in this repo already disagreed with.
Nothing built from our own fixtures could have.

Two rules it follows, worth keeping: assert the direction that catches an
**invented** constant (every value acidcat names, the spec must also name),
and do **not** assert breadth. acidcat names 32 of 265 WAVE format tags on
purpose; a check that failed on coverage would be a machine for importing
someone else's opinions. Where the spec is silent -- as it is on whether an MP4
atom is a container -- say so in the test's name rather than asserting
something weaker than the name promises.

## Anatomy pages

A format earns a page in `docs/formats/` once it is walked. Generate it from an
existing page's shell so the CSS, favicon, theme toggle and byte-map builder
stay identical. `scripts/build_ableton_anatomy.py` is the worked example: keep
`<head>`, keep the two `<script>` blocks, replace the body and the `SPECS`
literal, and refuse to write if any leftover from the source page survives.

Two rules for the byte maps:

- **Every byte comes from a real specimen**, named on the page. Verify the
  literal against the file afterwards; a datasheet that drifts from the bytes
  is worse than none.
- **Every byte is claimed by exactly one field.** Overlaps and gaps render
  wrong. A dozen lines of Node over the `SPECS` literal checks it.

Related formats belong on **tabs of one page**, not separate pages -- `.asd`,
`.als`, `.adg` and `.amxd` share a page because a project folder contains all
of them at once.
