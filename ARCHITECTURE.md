# acidcat -- architecture map

A byte-level inspection tool for audio and synth/preset file formats: it exposes
every field of a file's headers, chunks, and frame-headers so a human or model can
see exactly what a file is, flag anomalies, and edit or repair its structure.
Closer to readelf / 010 Editor / radare2's format layer than to exiftool, with
some optional audio analysis (BPM/key via librosa).

v2.0.0rc2 · ~72k source LOC · ~64k test LOC · one hard dependency (`mutagen`);
everything heavier is an optional, lazily imported extra, so `import acidcat`
pulls only the stdlib core.

## The path of a file

```
bytes ─> Source ─> sniff ─> walker ─> (label, chunks, notes) ─> contract ─> Document
                                                                    │
            inspect --json, od, carve, probe, edit, check, tui <────┘
```

1. **Source** (`core/infra/source.py`): a file mapped once per walk, or bytes
   in memory, so stdin, a carved region or a decoded image walks without a
   temp file. Walkers never open or stat a path; a test enforces it.
2. **sniff** (`core/infra/sniff.py`) names the format from its magic.
3. **A walker** (`core/walk/<fmt>.py`) reads it into the field model: a flat
   list of chunks, each with positioned fields, and its notes.
4. **The contract** (`core/infra/contract.py`) turns that into the v1
   Document: a node tree with stable ids, fields located absolutely with a
   machine value, the capabilities, and one findings list.
5. **The Document** (`core/document.py`, `acidcat.open()`) is read-only views
   over that dict, with the forensic scan's findings joined in. Every 2.0 verb
   that names a location reads it.

## The walker contract (what a walker emits)

`walk_file(path_or_source) -> (label, chunks, file_warnings)`

- **chunk**: `{id, offset, size, summary, fields[], warnings[], payload_base?, rows?}`
- **field**: `{off, len, name, value, note, enc?, raw?, xref?}`, built by `walk/base._f`

`value` is what a human reads; `enc` + `raw` is how to re-encode the field to
the exact on-disk bytes (the editor and repair contract); `xref` marks a
pointer field. This is the internal shape; `acidcat.walk()` and
`acidcat.walk_file()` still return it, with a `DeprecationWarning`, until 3.0.

A warning is a `Note`: a `str` that also carries its kind and a finding code
(`core/infra/findings.py`). The kinds are `defect` (the file breaks its
format), `coverage` (the walk stopped at one of our limits), `environment`
(something outside the file, like a sibling not beside it), `info` and `error`
(acidcat failed). A walker makes one with `defect(code, message)`,
`environment(...)` or `info(...)`, and a coverage note only with
`core/infra/limits.hit(name, limit, used, message)`, so it always names the
limit it hit. Consumers select by kind and code, never by message text. Of
the kinds, only a `defect` can make a verb exit 1.

A chunk whose payload decodes to another byte string (a packed YM's LHA body,
a PSF's zlib program, an Ableton document's gzip) may declare it as a layer:
`chunk["layer"]` names the decoder and the check it passed, and
`chunk["layer_chunks"]` walks the decoded image, positioned in it.
`core/infra/layers.py` holds the decoders.

## The Document (contract v1)

Specified in `docs/contract/node-v1.md`, with `node-v1.schema.json` beside it.

- **Nodes** are a tree. An IFF file's header is its root node (`RIFF`,
  `FORM`) and its chunks are the root's children. A node's `id` is its
  address: the slugged names from the root (`RIFF/fmt_`), with a repeated
  name indexed on every occurrence from 0 (`RIFF/LIST[0]`, `RIFF/LIST[1]`).
  Gaps inside a payload become `padding` or `unwalked` nodes, so the tree
  covers the bytes. A chunk that runs past a short header size is still the
  root's (the root grows to hold it), and a node with no extent (a
  SoundFont's presets) is the root's child after the positioned ones.
- **Fields** carry `key`, `value` (the machine value: a number where the
  display states one, in its base unit), `display`, `type` (the storage type
  they were read as, `u32le`) and `at`, a locator `{layer, off, len}`. Where
  the walker gave no type, the contract infers one from the bytes and says
  so in `typing`.
- **Capabilities** (`core/infra/capabilities.py`): what a node can do (play
  as audio, decode, render, descend into a layer, edit), inferred from the
  walk and marked as inferred. An audio cap names its ffmpeg codec, and is
  inferred only where the walk states the sample layout.
- **Findings**: the walker's notes and the forensic scan's, once each, with
  `kind`, `code`, `severity` and the node they sit in.
- **Limits** (`core/infra/limits.py`): what the walk ran under
  (`Limits(decode=True)` is `--deep`) and which caps it hit.

**ADDR** (`core/infra/addr.py`) is the one grammar for a location: a node id,
glob, name or unique last step; `#KEY` for a field; `@OFF+LEN` or
`@START..END` for bytes; `N:` for a layer. `commands/_addr.py` is the CLI's
door to it, so every verb resolves an address the same way.

**Editing** (`core/edit.py`): `doc.edit({...})` returns a `Patch`, the new
file image plus a record per edit. A field edit is encoded through its type
and written in place; a tag goes through the file's edit profile
(`core/write/profiles.py`). `repair()` sets the derived fields the edit
disturbed, from the constraint model; `verify()` re-reads every edit from
the new bytes; `commit()` writes atomically with a `_original` backup.

## Layer stack (bottom to top)

1. **Format primitives** -- `core/formats/` (per-format byte decoders: `riff`,
   `aiff`, `mp3`, `mp4`, `flac`, `ni`, `tracker`, `sf2`, ...),
   `core/primitives/` (shared byte readers), `core/codecs/` (ADPCM, BRR, VADPCM
   and friends), `core/containers/` (disc images and archives),
   `core/infra/` (`sniff.py` -- 98 recognized formats, `fieldcodec.py` -- the
   enc-language, `geometry.py` -- which bytes a chunk occupies, `source.py`,
   `limits.py`, `findings.py`, `layers.py`, `contract.py`, `addr.py`,
   `capabilities.py`, `render.py`).
2. **Walkers** -- `core/walk/*.py`: 62 walkers behind one dispatcher, serving 94
   registered format labels, each emitting the field model. **The correctness oracle and the
   default.** Dispatch: `core/walk/__init__.py::walk_file`.
3. **Document and edit** -- `core/document.py` (`acidcat.open()`, the views),
   `core/edit.py` (`Patch`).
4. **Analysis surface** -- `core/probe.py` (typed reads, value scan, offset
   tables), `core/forensics/` (entropy and Hilbert byte-map in `viz.py`,
   forensic checks in `anomalies.py`, the statistical audio detector in
   `audioscan.py`, provenance in `provenance.py`, triage in `classify.py`),
   `core/analysis/` (PCM decode, BPM/key detection, feature extraction,
   bandwidth and channel checks), `core/write/` (the strict IFF engine
   `structure.py`, `constraints.py` and `repairers.py` behind `check`, the
   tag-edit profiles), `core/extract/` (embedded-sample recovery).
5. **Index / DB / MCP** -- `core/catalogue/` (per-library SQLite + FTS, the
   registry, the shared filter builder) and `mcp_server/` (19 tools over stdio or
   streamable HTTP). A **consumer** of the core; the core never imports it, so it
   is cleanly severable.
6. **Interfaces** -- `cli.py` (17 verbs, plus the 1.8 spellings as aliases in
   `cli_aliases.py`) + `commands/*.py` (a module per verb; the 1.8 verbs' modules
   are the implementations behind the 2.0 ones);
   `tui_app/` (Textual inspector/editor over the Document); the public API in
   `acidcat/__init__`; console scripts `acidcat` and `acidcat-mcp`.

## Two facts that explain most of the design

- **Walkers are the oracle.** Any new parsing path is proven by diffing its
  output against the walkers across a large corpus, field for field. The
  Document is derived from the walk, never parsed separately.
- **Two container engines, on purpose.** `core/write/structure.py` is strict
  (clamps sizes, rejects malformed input) and drives edit and `check --fix`;
  the lenient traversal (`formats/riff.iter_chunks`, and `iter_spans` built on
  it, which the WAV walker consumes) reports a chunk's declared-but-wrong
  size, degrades, and never raises, and drives dissection. Malformed files are
  the subject, not an error.

## Invariants (the layering rules, all currently holding)

- `commands/` depends on `core/`; `core/` never imports `commands/`.
- DB connections live only in `core/catalogue/index.py` and `core/catalogue/registry.py`.
- The dissection core (walk, contract, document, probe, forensics, write)
  imports nothing from the index / DB / MCP layer. The dependency arrow
  points inward only.
- Every label `walk_file` can dispatch is a label `sniff` can produce
  (`tests/test_formats.py::test_walker_keys_are_known_formats`).
- Every seed's every node and positioned field has an ADDR that resolves back
  to it (`tests/test_document.py`), and every Document validates against the
  schema (`tests/test_contract.py`).

## Directory map

```
src/acidcat/
  core/            215 modules
    formats/       per-format byte decoders (42)
    walk/          62 walker modules -> 94 format labels (63)
    primitives/    shared byte readers (6)
    codecs/        sample-data decoders, unpackers, the 6510/SID and SPC700/S-DSP players and their registry (22)
    containers/    disc images and archives, a PS1 disc's audio catalog (6)
    infra/         sniff, fieldcodec, Source, Limits, finding codes, layers, the v1 contract, ADDR, capabilities, rendering (16)
    forensics/     anomalies, entropy/viz, audioscan, provenance (19)
    analysis/      PCM decode, BPM/key, features, bandwidth (8)
    write/         strict IFF engine, constraints, repairers, tag-edit profiles (14)
    extract/       embedded-sample recovery (4)
    catalogue/     SQLite index, registry, query builder, search (8)
    data/          shipped JSON tables (provenance signatures)
    document.py    acidcat.open() and the Document views
    edit.py        Patch: edit, repair, verify, commit
  commands/        17 CLI verbs and the 1.8 implementations behind them (38 modules)
  mcp_server/      schema, handlers, transport (19 tools)
  tui_app/         Textual inspector/editor; model.py is its state over the
                   Document, no Textual; state.py what it remembers between
                   runs (tui.json)
  util/            small shared helpers
  cli.py  cli_aliases.py  explorer.py  tui_theme.py  __init__.py     (283 modules in total)
lab/src/acidcat_lab/
                   the adversarial half, its OWN distribution (acidcat-lab).
                   Constructs files rather than reading them: cavities,
                   polyglots, sample-LSB stego. Depends on acidcat through its
                   public facade only; the arrow never points back
                   (tests/test_lab_boundary.py)
tests/             ~0.89 test:source LOC
docs/              architecture.md (detailed), contract/ (the v1 Document,
                   the 2.0 CLI), format anatomy pages
internal_docs/     design + review notes (gitignored, local-only)
```

## Testing: which tier runs when

The suite is about 5,800 tests: half an hour serial, three minutes with
`pytest -n 8` (the `dev` extra brings pytest-xdist; `pyproject` sets
`--dist loadgroup` so `tests/conftest.py` can pin every TUI pilot to one
worker and the memory profiles to another, without which they time out
under contention). Three tiers, by what changed:

| tier | when | command | time |
|---|---|---|---|
| scoped | a walker, codec, command or page changed | `python scripts/tests_for.py --run` | under a minute |
| quick | anything wider, before a commit | `pytest -n 8 -m "not slow"` | ~2 min |
| full | the release commit, and CI on every push | `source scripts/corpus_env.sh; pytest -n 8` | ~3 min local, plus the corpora |

`tests_for.py` maps `git diff` to tests: a walker to its own file plus the
fleet sweeps keyed on its name (fuzz, cap ledger, sniff, seeds, geometry)
and the doc guards; a page or builder to the anatomy fleet; anything it
cannot map, or a change to `sniff.py`, `walk/__init__.py`, `geometry.py`
or `seeds.py`, to the full suite. It prints why it chose each part.

`@pytest.mark.slow` marks the TUI pilots, the memory profiles, the read-cap
adversaries and the MCP wire test; a test earns it at ten seconds. The
corpus tests need `ACIDCAT_<FMT>_CORPUS` and skip without it, and a skipped
corpus test is a green run that checked no real file, so the release run
sources `scripts/corpus_env.sh` and includes every corpus for every format
touched since the last release. Docs-only and test-only commits after a
green release run do not re-trigger it. CI runs the full tier with coverage
on three operating systems and three Pythons; it is the second opinion, not
the first.

## Where to go deeper

- The Document and ADDR: `docs/contract/node-v1.md`; the CLI: `docs/contract/cli-2.0.md`
- Field model + walker contract: `core/walk/base.py`
- Walk to Document: `core/infra/contract.py`; the views: `core/document.py`
- Add a format: `docs/adding-a-walker.md` (teach `core/infra/sniff.py` the
  magic, write `core/walk/<fmt>.py`, add one `_WALKERS` entry in
  `core/walk/__init__.py`)
- The enc-language: `core/infra/fieldcodec.py`
