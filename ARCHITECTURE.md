# acidcat -- architecture map

A byte-level inspection tool for audio and synth/preset file formats: it exposes
every field of a file's headers, chunks, and frame-headers so a human or model can
see exactly what a file is, flag anomalies, and edit or repair its structure.
Closer to readelf / 010 Editor / radare2's format layer than to exiftool, with
some optional audio analysis (BPM/key via librosa).

v1.7.0 · ~56k source LOC · ~52k test LOC · one hard dependency (`mutagen`);
everything heavier is an optional, lazily imported extra, so `import acidcat`
pulls only the stdlib core.

## The data contract (what every layer speaks)

`walk_file(path) -> (label, chunks, file_warnings)`

- **chunk**: `{id, offset, size, summary, fields[], warnings[], payload_base?, rows?}`
- **field**: `{off, len, name, value, note, enc?, raw?, xref?}` -- built by `walk/base._f`

`value` is what a human reads; `enc` + `raw` is how to re-encode the field to the
exact on-disk bytes (the editor/repair contract); `xref` marks a pointer field.
This one shape flows through inspect, the TUI, probe, anomalies, and indexing
unchanged.

## Layer stack (bottom to top)

1. **Format primitives** -- `core/formats/` (per-format byte decoders: `riff`,
   `aiff`, `mp3`, `mp4`, `flac`, `ni`, `tracker`, `sf2`, ...),
   `core/primitives/` (shared byte readers), `core/codecs/` (ADPCM, BRR, VADPCM
   and friends), `core/containers/` (disc images and archives),
   `core/infra/` (`sniff.py` -- 80 recognized formats, `fieldcodec.py` -- the
   enc-language, `geometry.py` -- which bytes a chunk occupies, `mapped.py`,
   `render.py`).
2. **Walkers** -- `core/walk/*.py`: 48 walkers behind one dispatcher, serving 76
   registered format labels, each emitting the field model. **The correctness oracle and the
   default.** Dispatch: `core/walk/__init__.py::walk_file`.
3. **Declarative engine** -- `core/grammar/`: format descriptors as data plus one
   interpreter emitting the same field model. Opt-in, test-only, validated
   byte-for-byte against the walkers, which remain the oracle.
4. **Analysis surface** -- `core/probe.py` (typed reads, value scan,
   `fmt.sample_rate` addressing), `core/forensics/` (entropy and Hilbert byte-map
   in `viz.py`, forensic checks in `anomalies.py`, the statistical audio detector
   in `audioscan.py`, provenance in `provenance.py`), `core/analysis/` (PCM
   decode, BPM/key detection, feature extraction, bandwidth and channel checks),
   `core/write/` (the strict IFF engine `structure.py`, `constraints.py` and
   `repairers.py` behind validate / repair), `core/extract/` (embedded-sample
   recovery).
5. **Index / DB / MCP** -- `core/catalogue/` (per-library SQLite + FTS, the
   registry, the shared filter builder) and `mcp_server/` (19 tools over stdio or
   streamable HTTP). A **consumer** of the core; the core never imports it, so it
   is cleanly severable.
6. **Interfaces** -- `cli.py` (29 subcommands) + `commands/*.py` (one per verb);
   `tui_app/` (Textual inspector/editor); the public API in `acidcat/__init__`;
   console scripts `acidcat` and `acidcat-mcp`.

## Two facts that explain most of the design

- **Walkers are the oracle.** Any new parsing path (the grammar engine) is proven
  by diffing its output against the walkers across a large corpus, field for field.
- **Two container engines, on purpose.** `core/write/structure.py` is strict
  (clamps sizes, rejects malformed input) and drives write / repair; the lenient
  traversal (`formats/riff.iter_chunks`, and `iter_spans` built on it, which the
  walker and the grammar strategy both consume) reports a chunk's
  declared-but-wrong size, degrades, and never raises, and drives dissection.
  Malformed files are the subject, not an error.

## Invariants (the layering rules, all currently holding)

- `commands/` depends on `core/`; `core/` never imports `commands/`.
- DB connections live only in `core/catalogue/index.py` and `core/catalogue/registry.py`.
- The dissection core (walk, grammar, probe, forensics, write) imports nothing
  from the index / DB / MCP layer. The dependency arrow points inward only.
- Every label `walk_file` can dispatch is a label `sniff` can produce
  (`tests/test_formats.py::test_walker_keys_are_known_formats`).

## Directory map

```
src/acidcat/
  core/            176 modules
    formats/       per-format byte decoders (26)
    walk/          48 walker modules -> 76 format labels (49)
    primitives/    shared byte readers (6)
    codecs/        sample-data decoders + the 6510/SID player (16)
    containers/    disc images and archives (5)
    infra/         sniff, fieldcodec, mmap, rendering (9)
    forensics/     anomalies, entropy/viz, audioscan, provenance (19)
    analysis/      PCM decode, BPM/key, features, bandwidth (8)
    write/         strict IFF engine, constraints, repairers (12)
    extract/       embedded-sample recovery (4)
    catalogue/     SQLite index, registry, query builder, search (8)
    grammar/       declarative descriptor engine (opt-in) (9)
    data/          shipped JSON tables (provenance signatures)
  commands/        29 CLI verbs (31 modules)
  mcp_server/      schema, handlers, transport (19 tools)
  tui_app/         Textual inspector/editor
  util/            small shared helpers
  cli.py  explorer.py  tui_theme.py  __init__.py     (233 modules in total)
lab/src/acidcat_lab/
                   the adversarial half, its OWN distribution (acidcat-lab).
                   Constructs files rather than reading them: cavities,
                   polyglots, sample-LSB stego. Depends on acidcat through its
                   public facade only; the arrow never points back
                   (tests/test_lab_boundary.py)
tests/             ~0.94 test:source LOC
docs/              architecture.md (detailed), format anatomy pages
internal_docs/     design + review notes (gitignored, local-only)
```

## Where to go deeper

- Field model + walker contract: `core/walk/base.py`
- Add a format: teach `core/infra/sniff.py` the magic, write `core/walk/<fmt>.py`, add one
  `_WALKERS` entry in `core/walk/__init__.py`
- The enc-language: `core/infra/fieldcodec.py`
- The declarative engine and its design: `core/grammar/` + `internal_docs/grammar-engine-*.md`
