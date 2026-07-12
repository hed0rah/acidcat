# acidcat — architecture map

A byte-level inspection tool for audio and synth/preset file formats: it exposes
every field of a file's headers, chunks, and frame-headers so a human or model can
see exactly what a file is, flag anomalies, and edit or repair its structure.
Closer to readelf / 010 Editor / radare2's format layer than to exiftool, with
some optional audio analysis (BPM/key via librosa).

v0.47.0 · ~23k source LOC · ~11k test LOC · one hard dependency (`mutagen`);
everything heavier is an optional, lazily imported extra, so `import acidcat`
pulls only the stdlib core.

## The data contract (what every layer speaks)

`walk_file(path) -> (label, chunks, file_warnings)`

- **chunk**: `{id, offset, size, summary, fields[], warnings[], payload_base?, rows?}`
- **field**: `{off, len, name, value, note, enc?, raw?, xref?}` — built by `walk/base._f`

`value` is what a human reads; `enc` + `raw` is how to re-encode the field to the
exact on-disk bytes (the editor/repair contract); `xref` marks a pointer field.
This one shape flows through inspect, the TUI, probe, anomalies, and indexing
unchanged.

## Layer stack (bottom to top)

1. **Format primitives** — `core/*.py`: per-format byte decoders (`riff`, `aiff`,
   `mp3`, `mp4`, `flac`, `ni`, `tracker`, `sf2`, ...), the enc-language
   (`fieldcodec.py`), the strict IFF container engine (`structure.py`), sniffing
   (`sniff.py`, `detect.py`).
2. **Walkers** — `core/walk/*.py`: 20 format walkers, one per format, each emitting
   the field model. **The correctness oracle and the default.** Dispatch:
   `core/walk/__init__.py::walk_file`.
3. **Declarative engine (new, v0.46)** — `core/grammar/`: format descriptors as
   data + one interpreter emitting the same field model. Opt-in, test-only,
   validated byte-for-byte against the walkers, which remain the oracle.
4. **Analysis surface** — `core/probe.py` (typed reads, value scan,
   `fmt.sample_rate` addressing), `core/viz.py` (entropy, Hilbert byte-map),
   `core/anomalies.py` (forensic checks), `core/constraints.py` +
   `core/repairers.py` (validate / repair).
5. **Index / DB / MCP** — `core/{index,indexing,registry,search}.py` (per-library
   SQLite + FTS) and `mcp_server.py` (19 tools). A **consumer** of the core; the
   core never imports it, so it is cleanly severable.
6. **Interfaces** — `cli.py` (24 subcommands) + `commands/*.py` (one per verb);
   `tui_app.py` (Textual inspector/editor); the public API in `acidcat/__init__`;
   console scripts `acidcat` and `acidcat-mcp`.

## Two facts that explain most of the design

- **Walkers are the oracle.** Any new parsing path (the grammar engine) is proven
  by diffing its output against the walkers across a large corpus, field for field.
- **Two container engines, on purpose.** `structure.py` is strict (clamps sizes,
  rejects malformed input) and drives write / repair; the lenient traversal
  (`riff.iter_chunks`, and `riff.iter_spans` built on it, which the walker and the
  grammar strategy both consume) reports a chunk's declared-but-wrong size,
  degrades, and never raises, and drives dissection. Malformed files are the
  subject, not an error.

## Invariants (the layering rules, all currently holding)

- `commands/` depends on `core/`; `core/` never imports `commands/`.
- DB connections live only in `core/index.py` and `core/registry.py`.
- The dissection core (walk, grammar, probe, viz, constraints, anomalies) imports
  nothing from the index / DB / MCP layer. The dependency arrow points inward only.

## Directory map

```
src/acidcat/
  core/            primitives, walkers, grammar, analysis, index (48 modules)
    riff.py        RIFF chunk primitives incl. the shared lenient iter_spans
    vocab.py       core-owned value->label tables + the semantic ctx-key set
    walk/          20 format walkers + the field-model base
    grammar/       declarative descriptor engine (v0.46, opt-in)
  commands/        24 CLI verbs
  cli.py  tui_app.py  mcp_server.py  explorer.py  __init__.py
tests/             ~0.49 test:source LOC
docs/              architecture.md (detailed), format anatomy pages
internal_docs/     design + review notes (gitignored, local-only)
```

## Where to go deeper

- Field model + walker contract: `core/walk/base.py`
- Add a format: teach `core/sniff.py` the magic, write `core/walk/<fmt>.py`, add one
  `_WALKERS` entry in `core/walk/__init__.py`
- The enc-language: `core/fieldcodec.py`
- The declarative engine and its design: `core/grammar/` + `internal_docs/grammar-engine-*.md`
