# Architecture

How acidcat is wired together: the data flow from a file on disk to a query
result on stdout or over MCP.

Last updated: 2026-05-02 (v0.5.x architecture; previously updated 2026-04-23
under the v0.4 single-DB layout).

---

## High-level picture

```
                            ┌─────────────────────┐
                            │  sample files on    │
                            │  disk               │
                            └──────────┬──────────┘
                                       │ walker + mtime cache
                                       ▼
            ┌───────────────────────────────────────────────┐
            │  format dispatch  (_sniff_format + ext fallback) │
            └─┬──────────────┬──────────────┬──────────────┬─┘
              │              │              │              │
              ▼              ▼              ▼              ▼
          core/riff.py   core/aiff.py   core/midi.py   core/serum.py
         core/tagged.py  (mutagen wrap for mp3/flac/ogg/m4a)
                                       │
                                       ▼
            ┌───────────────────────────────────────────────┐
            │  core/detect.py  (filename + librosa + verify)│
            │  core/features.py  (optional feature vector)  │
            └──────────┬────────────────────────────────────┘
                       │ upsert_sample / upsert_tags / upsert_features
                       ▼
            ┌───────────────────────────────────────────────┐
            │  per-library SQLite indexes                   │
            │  ~/.acidcat/libraries/<label>_<hash>.db       │
            │  (or <library>/.acidcat/index.db if --in-tree)│
            │  samples, tags, descriptions, samples_fts,    │
            │  features, scan_roots, meta                   │
            └──────────┬────────────────────────────────────┘
                       │ longest-prefix routing for writes,
                       │ fan-out + dedup for reads
                       ▼
            ┌───────────────────────────────────────────────┐
            │  global registry  ~/.acidcat/registry.db      │
            │  libraries(db_path PK, root_path UNIQUE,      │
            │            label, in_tree, sample_count, ...) │
            └──────────┬────────────────────────────────────┘
                       │ query API
                       ▼
            ┌──────────────────┬────────────────────────────┐
            │  CLI transport   │  MCP transport             │
            │  commands/*.py   │  mcp_server.py             │
            └──────────────────┴────────────────────────────┘
```

Five layers, each replaceable: scanner, parsers, per-library indexes, registry,
transports. The DSP subsystem (`detect.py`, `features.py`) hangs off the parser
layer as an optional extension. Transports share the same query API so that
`acidcat query --bpm 120:140` and the MCP `search_samples` tool run the same SQL
against every registered library and merge the results.

The v0.4 architecture had a single global `~/.acidcat/index.db` with a
`scan_roots` table tracking origin per row. v0.5 inverts the relationship:
each registered directory gets its own DB (so writes never contend across
libraries, drives can be unmounted cleanly, and per-pack indexes can travel
with the data via `--in-tree`). The registry is the only join point.

---

## Source tree map

```
src/acidcat/
  __main__.py            entrypoint (python -m acidcat)
  cli.py                 argparse wiring + dispatch
  commands/              user-facing command handlers (one per verb)
    info.py              single-file metadata dump
    scan.py              batch directory walk with CSV output
    chunks.py            RIFF chunk walker
    survey.py            chunk frequency counter
    dump.py              hex-dump chunk payload
    detect.py            librosa BPM/key estimator
    features.py          feature extraction to CSV
    similar.py           similarity + clustering over features CSV
    search.py            legacy CSV text search
    index.py             per-library index management + --discover walker
    query.py             fan-out filter across registered libraries
  core/                  reusable library layer
    riff.py              RIFF/WAV chunk parser (caps reads at 64KB)
    aiff.py              AIFF/IFF chunk parser
    midi.py              MIDI meta parser
    serum.py             XferJson preset parser (linear-pass JSON)
    tagged.py            mutagen wrapper (mp3/flac/ogg/m4a) + BOM strip
    detect.py            filename parsing + librosa + validation pipeline
    features.py          librosa feature vector extractor
    camelot.py           Camelot wheel math + enharmonic normalization
    index.py             per-library SQLite schema, upsert, query
    paths.py             path normalize + central/in-tree DB layout + hash
    registry.py          global registry table, no-overlap guard, fan-out
    formats.py           output formatters (table/json/csv)
  util/
    midi.py              MIDI note number / name helpers
    csv_helpers.py       pandas shim for features commands
    stdin.py             piped-input detection + tempfile landing
    deps.py              optional-dependency error messages
  mcp_server.py          MCP tool definitions + dispatch
```

Two hard rules keep the tree healthy:

1. `commands/` depends on `core/`. `core/` never imports from `commands/`.
2. Anything that holds a DB connection is in `core/index.py` or `mcp_server.py`.
   Command handlers receive a connection, they never manage one.

---

## The registry layer

Each registered directory becomes a *library* with its own SQLite DB.
The global registry at `~/.acidcat/registry.db` tracks every library:

```sql
CREATE TABLE libraries (
    db_path           TEXT PRIMARY KEY,
    root_path         TEXT UNIQUE NOT NULL,
    label             TEXT NOT NULL,
    in_tree           INTEGER NOT NULL DEFAULT 0,
    sample_count      INTEGER,
    feature_count     INTEGER,
    last_indexed_at   REAL,
    last_seen_at      REAL,
    schema_version    INTEGER,
    created_at        REAL NOT NULL
);
```

Two policy invariants are enforced at registration time by
`core/registry.py:_assert_no_overlap`:

1. **Mandatory labels.** Every library has a label, defaulted from
   `os.path.basename(root)` if the user did not pass `--label`. The
   label is how the LLM identifies a library in MCP tool calls and how
   the user passes `--root LABEL` on the CLI.
2. **No nested libraries.** A root that is `==`, parent of, or child of
   any registered root is rejected. Comparison is case-insensitive on
   Windows (`paths.compare_path`) so `C:/MyLib` and `c:/mylib` cannot
   both register independently.

Writes use longest-prefix routing: `find_library_for_path(sample_path)`
returns the most-specific registered root that contains the sample, or
`None`. Reads fan out across every registered library, dedup by `path`,
and silently skip orphans (libraries whose `db_path` is missing on
disk, e.g. external drive unmounted).

DB filenames in central mode are `<safe_label>_<hash>.db` where the
hash is 12 hex chars of `sha1(normalized_root)`. 12 chars is well past
any realistic single-user catalog (collision near 16M libraries) and
the registry stores `db_path` explicitly so existing libraries with
older 8-char filenames keep working.

---

## Data flow: indexing a directory

`acidcat index DIR --label NAME` or its MCP equivalent
`register_library` + `reindex`.

```
caller                    walker                parser              index
  │                         │                      │                   │
  │  acidcat index DIR      │                      │                   │
  │────────────────────────▶│                      │                   │
  │                         │  os.walk(DIR)        │                   │
  │                         │──────────────┐       │                   │
  │                         │              │       │                   │
  │                         │   for each file:     │                   │
  │                         │              │       │                   │
  │                         │  mtime / size │      │                   │
  │                         │  diff against DB     │                   │
  │                         │              │       │                   │
  │                         │  if changed: │       │                   │
  │                         │──────────────┼──────▶│                   │
  │                         │              │       │  detect format    │
  │                         │              │       │  parse chunks     │
  │                         │              │       │  extract metadata │
  │                         │              │       │                   │
  │                         │◀─────────────┼───────│  dict of fields   │
  │                         │              │       │                   │
  │                         │  upsert_sample(row) ─┼──────────────────▶│
  │                         │                      │                   │
  │                         │  (optional) extract_features             │
  │                         │  upsert_features ────┼──────────────────▶│
  │                         │                      │                   │
  │  summary counts         │                      │                   │
  │◀────────────────────────│                      │                   │
```

Skip-if-unchanged uses `get_sample_stat(conn, path)` to pull the stored
`(mtime, size)` pair and compares against `os.stat`. If both match, the
walker calls `touch_last_seen` and moves on. This is what makes reindexing
idempotent and fast on the common case of unchanged libraries.

Deletions are detected as a separate pass: after the walk, any row whose
`last_seen_at` is older than the walk's start time and whose `scan_root`
matches the one we just walked gets pruned. See the docstring on
`prune_missing` for the timing model and the (rare) edge case where a
file added near the end of a long walk could be wrongly pruned; re-running
the index always recovers it.

Bulk registration is available via `acidcat index --discover ROOT
[--min-samples N --max-depth D]` which walks a tree and registers every
qualifying subfolder as its own library. The MCP equivalent is
`discover_libraries`, which defaults to `dry_run=true` so a forgetful
LLM cannot bulk-mutate the registry by omission.

---

## Data flow: a query

`acidcat query --bpm 120:140 --key F` and MCP `search_samples` take the
same path through the layers, with fan-out over every registered library.

```
caller                  query API              registry        per-lib DBs
  │                          │                     │                  │
  │  bpm=120-140, key=F      │                     │                  │
  │─────────────────────────▶│                     │                  │
  │                          │  list_libraries()   │                  │
  │                          │  only_existing=True │                  │
  │                          │────────────────────▶│                  │
  │                          │◀────────────────────│ rows             │
  │                          │                     │                  │
  │                          │  build WHERE once   │                  │
  │                          │                     │                  │
  │                          │  for each library:  │                  │
  │                          │    SELECT * FROM    │                  │
  │                          │      samples ...    │                  │
  │                          │    LIMIT N          │                  │
  │                          │────────────────────────────────────────▶│
  │                          │◀────────────────────────────────────────│
  │                          │                                          │
  │                          │  dedup_by_path                           │
  │                          │  sort, slice [:LIMIT]                    │
  │  list[dict]              │                                          │
  │◀─────────────────────────│                                          │
```

Enharmonic expansion happens at the SQL boundary. If a user asks for `F`,
the query layer expands that to the set of enharmonic spellings that exist
in the DB and uses `IN` instead of `=`. This is why the
`enharmonic_spellings` function in `core/camelot.py` exists as a public
helper.

---

## The two transports

The CLI and MCP server are thin shells over the same core API. Neither
implements domain logic; both call into `core/`.

### CLI (commands/*.py)

Each command module follows the same pattern:

```python
def register(subparsers):
    p = subparsers.add_parser("info", help="...")
    p.add_argument("target")
    p.add_argument("-f", "--format", choices=["table","json","csv"])
    p.set_defaults(func=run)

def run(args):
    # args parsed, call into core/
    rec = _info_wav(args.target, args)
    output(rec, fmt=args.format)
    return 0
```

`cli.py` discovers command modules, calls `register()` on each, parses, and
dispatches to `args.func(args)`. Output formatting goes through
`core/formats.py` so every verb can emit table/json/csv on the same flag.

### MCP (mcp_server.py)

Each tool is registered via `_tool(name, description, input_schema,
handler, annotations)`. The handlers call into the same `core/` functions
as the CLI counterparts, so no logic is duplicated. For example:

```
CLI:   acidcat query --bpm 120:140 --key F --json
MCP:   search_samples({ bpm_min: 120, bpm_max: 140, key: "F" })
```

Both resolve to the same SQL SELECT generated by the same Python code.
The surface has one rule: MCP tool input schemas never reshape the core
function's arguments. If you find yourself translating between two
parameter conventions, push the rename down into the core API.

### Why the symmetry matters

- No drift between transports. Bug in one surface is a bug in the core.
- LLM-suggested shell pipelines can be copy-pasted back into the CLI.
- Shell pipelines can be suggested back through the LLM.
- Documentation writes itself: one example illustrates both surfaces.

### Current gap

The MCP server opens a fresh SQLite connection per library per tool call
via `_open_all_libraries()` and `_close_all()`. For small DBs this is
fast; for the typical 20-library catalog it adds a few ms per call. Not
a correctness issue.

The bigger cost is librosa cold-start (~30-60s of numba JIT on the
first analysis call in a new process). The `analyze_sample` and
`detect_bpm_key` tool descriptions were updated to set expectations
about this; the deferred fix is a pre-warmed worker subprocess that
holds the import.

---

## The SQLite schema

Per-library DB at `~/.acidcat/libraries/<safe_label>_<hash>.db` (central
default) or `<library_root>/.acidcat/index.db` (in-tree opt-in via
`--in-tree`). WAL journaling, foreign keys on. Schema version tracked in
the `meta` table; mismatched versions raise `SchemaVersionError` rather
than running old SQL against a future schema.

The schema below describes one library DB; every registered library has
the same shape. The global registry at `~/.acidcat/registry.db` is a
separate DB with the `libraries` table documented under "The registry
layer" above.

### `samples` table

Primary table. One row per indexed file. Immutable facts from parsers,
plus bookkeeping.

```sql
CREATE TABLE samples (
    path             TEXT PRIMARY KEY,
    scan_root        TEXT,
    mtime            REAL,
    size             INTEGER,
    format           TEXT,       -- wav, aiff, mp3, flac, ogg, m4a, midi, serum
    duration         REAL,
    bpm              REAL,
    key              TEXT,       -- "Am", "C#", "F", etc. (sharps preferred)
    title            TEXT,
    artist           TEXT,
    album            TEXT,
    genre            TEXT,
    comment          TEXT,
    acid_beats       INTEGER,    -- from ACID chunk
    root_note        INTEGER,    -- MIDI note, from smpl or acid chunk
    sample_rate      INTEGER,
    channels         INTEGER,
    bits_per_sample  INTEGER,
    chunks           TEXT,       -- comma-separated chunk IDs for forensic queries
    indexed_at       REAL,
    last_seen_at     REAL
);

CREATE INDEX idx_samples_bpm       ON samples(bpm);
CREATE INDEX idx_samples_key       ON samples(key);
CREATE INDEX idx_samples_duration  ON samples(duration);
CREATE INDEX idx_samples_format    ON samples(format);
CREATE INDEX idx_samples_scan_root ON samples(scan_root);
```

Design notes:

- Path is the primary key. Paths are normalized with forward slashes
  regardless of OS (`normalize_path` in `index.py`). This keeps Windows
  and Unix paths comparable.
- The `chunks` column stores a CSV of chunk IDs seen in the file. It's
  denormalized on purpose. Forensic queries like "show me all files with
  a `bext` chunk" are much cheaper against a text column with `LIKE
  '%bext%'` than a proper chunks junction table would be, and the data
  is low-volume per row.
- `root_note` is a MIDI note number (0-127). This is what ACID and SMPL
  chunks natively store.
- `acid_beats` is the `beats` field from the ACID chunk. Combined with
  `bpm` it gives loop length in beats, which is a reliable loop-vs-one-shot
  signal for files that have ACID metadata.

### `tags` junction

```sql
CREATE TABLE tags (
    path TEXT,
    tag  TEXT,
    PRIMARY KEY (path, tag)
);
CREATE INDEX idx_tags_tag ON tags(tag);
```

User-assigned tags. Many-to-many. No cascade from `samples` deletion
because tags are user intent and outlive the file lifecycle (though
orphans are cleaned on reindex).

### `descriptions` table

```sql
CREATE TABLE descriptions (
    path        TEXT PRIMARY KEY,
    description TEXT
);
```

One free-text description per sample. User-writable. Populated either
manually by the user, or (in the LLM-era workflow) by an agent reading
the analysis + metadata and writing back a natural-language description.

### `samples_fts` virtual table (FTS5)

```sql
CREATE VIRTUAL TABLE samples_fts USING fts5(
    path, title, artist, album, genre, comment, description, tags,
    tokenize='porter'
);
```

Full-text search index. Populated from the other tables via
`rebuild_fts_for_path`. Porter stemming so "dusty" matches "dust". The
`tags` column is a space-joined flat string of all tags on the row, which
lets a single FTS query match across structural metadata + descriptive
text + user tags in one shot.

### `features` blob table

```sql
CREATE TABLE features (
    path             TEXT PRIMARY KEY,
    features_json    TEXT,       -- JSON blob, 50+ float fields
    features_version INTEGER,    -- bump when extractor changes
    extracted_at     REAL
);
```

Optional. Populated by `acidcat index --features` or the MCP
`reindex_features` tool. Stored as JSON on purpose: the column schema
for a feature vector changes as librosa versions and extractor choices
evolve. JSON preserves whatever shape was written, and a version number
per row lets future code detect stale vectors and trigger recompute.

### `scan_roots` table

```sql
CREATE TABLE scan_roots (
    path             TEXT PRIMARY KEY,
    added_at         REAL,
    last_indexed_at  REAL,
    file_count       INTEGER
);
```

One row per directory ever passed to `acidcat index`. Tracks provenance:
when a sample row's `scan_root` points here, the user can remove the
whole root in one call.

### `meta` table

```sql
CREATE TABLE meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
```

Key-value store. Currently just `schema_version`. Reserved for future
use: feature extractor version, last global reindex timestamp, etc.

---

## Format detection dispatch

`commands/index.py:_sniff_format` reads the first 12 bytes of the file
and identifies the format. Extension is the fallback, used only when
the magic bytes are unrecognized:

```
b"MThd"                       -> "midi"
b"FORM" + b"AIFF"|b"AIFC"     -> "aiff"
b"RIFF" + b"WAVE"             -> "wav"
b"XferJson"                   -> "serum"
b"fLaC"                       -> "flac"
b"OggS"                       -> "ogg"
b"ID3" or 0xFF (sync frame)   -> "mp3"
b"ftyp" at offset 4           -> "mp4"
unknown -> fall back to extension
```

Order matters: magic-byte sniff has priority over the extension so a
double-suffixed file (e.g. AIFF renamed to `foo.aiff.wav` from a bad
batch convert) routes by content, not by trailing suffix. This is the
F-21 fix from the 2026-05-02 audit; before it, the extension dispatch
could mis-route those files into the WAV parser and silently mis-tag.

Adding a new format is a three-step process:
1. Add a `core/<format>.py` module with `is_<format>`, `parse_<format>`,
   and a documented return dict.
2. Extend `_sniff_format` in `commands/index.py` to recognize the magic
   bytes, and route through `_extract_for_index` to the new parser.
3. Add an `_info_<format>(filepath, args)` builder in `commands/info.py`.
4. Add a doc under `docs/formats/<format>.md`.

---

## The validation pipeline (core/detect.py)

The most subtle logic in the project, worth calling out because it's the
difference between a library with plausible BPM/key values and a library
with correct ones.

```
filename_bpm  = parse_bpm_from_filename(path)    # regex extraction
detected_bpm  = librosa.beat.tempo(...)          # audio analysis

final_bpm, source = validate_and_improve_bpm(detected_bpm, filename_bpm)
```

Rules inside `validate_and_improve_bpm`:

```
filename_bpm is None              -> use detected, source="detected"
detected_bpm is None              -> use filename, source="filename"
detected not in [60, 300]         -> reject detected, use filename
|detected - filename| <= 20       -> accept detected, source="detected"
|detected*2  - filename| <= 20    -> use detected*2, source="corrected"
|detected/2  - filename| <= 20    -> use detected/2, source="corrected"
|detected*1.5 - filename| <= 20   -> use detected*1.5, source="corrected"
|detected/1.5 - filename| <= 20   -> use detected/1.5, source="corrected"
else                              -> use filename, source="filename"
```

This catches the two most common librosa failure modes:

- **Octave doubling**. librosa reports half-time or double-time when the
  track emphasizes a different metric level (e.g. a 140 BPM track with
  strong 70 BPM accents). If the filename says 140 and librosa says 70,
  the `*2` rule fires and we store 140.
- **Triplet feel**. Swing or triplet-heavy material can pull the
  estimate toward `2/3x` or `1.5x` of the true tempo. The 1.5 rules
  catch these.

Key detection has a simpler pipeline (currently `argmax` of median
chroma) but a similar pattern: audio-estimated key is compared against
a filename-parsed key, and they're reconciled. See
`dsp/chroma_and_key.md` for the signal-processing details and a
proposed upgrade to Krumhansl-Schmuckler.

### `source` fields are the confidence channel

Every field that can come from multiple origins carries a source tag:

```
bpm_source  = "chunk" | "detected" | "filename" | "corrected" | "oneshot" | "failed"
key_source  = "chunk" | "detected" | "filename" | "failed"
```

Callers can filter by source to get high-confidence results only:

```python
# only use chunk-authored BPM (ACID metadata)
SELECT * FROM samples WHERE bpm_source = "chunk"
```

**Current gap**: these source fields are computed inside `detect.py` but
aren't plumbed through into the `samples` table columns yet. Adding
`bpm_source` and `key_source` columns (plus a `confidence` float) is on
the near-term work list.

---

## The optional-dependency story

Core metadata reading depends on `mutagen` only (a pure-Python lib used
for MP3/FLAC/OGG/Opus/M4A tag parsing). `pip install acidcat` gives you
all common audio formats out of the box.

Three extras add capability:

```
[analysis]  librosa, numpy, scipy BPM/key detection, feature extraction
[ml]        pandas, scikit-learn  similarity + clustering
[mcp]       mcp SDK               MCP server binary
```

The MCP server registers analysis-backed tools even when librosa isn't
installed, but the handlers return a structured error message explaining
the install step. This is deliberate: it lets the LLM discover what's
possible and surface the fix to the user, rather than hiding the tool
entirely.

The check is implemented as:

```python
def _librosa_available():
    # cheap: does python see librosa + numpy on the import path?
    return (importlib.util.find_spec("librosa") is not None
            and importlib.util.find_spec("numpy") is not None)
```

`importlib.util.find_spec` avoids actually importing librosa on every
tool call, which is crucial because the import itself takes several
hundred milliseconds cold.

---

## Known performance characteristics

**Fast path (SQLite-backed tools)**. All `list_*`, `search_samples`,
`get_sample`, `locate_sample`, `find_compatible`: sub-100ms on indexes
up to tens of thousands of files. FTS queries on `samples_fts` are
well-indexed by FTS5 internals.

**Slow path (librosa tools)**. `analyze_sample`, `detect_bpm_key`,
`find_similar` when features aren't pre-indexed: 0.5-10s per file
warm. First call after server start can be much slower because numba
and scipy JIT-compile hot paths on first use.

**Bulk operations**. `reindex` walks a directory. Rough ceiling is
limited by filesystem stat() throughput and file-open rate, not by
Python. On an NVMe SSD, expect a few thousand files per second for
metadata-only indexing (no `--features`).

**Cold librosa**. First call can take 60s+ because of numba JIT
compilation. A pre-warm pattern on server startup would cut
time-to-first-analysis dramatically:

```python
# spin this up at server launch, non-blocking
def _prewarm_analysis():
    import librosa, numpy as np
    silence = np.zeros(22050, dtype=np.float32)
    librosa.beat.beat_track(y=silence, sr=22050)
    librosa.feature.chroma_cqt(y=silence, sr=22050)
```

---

## Extension points

Three clean places to extend without touching existing code:

### New parser

Add `core/<format>.py`. Provide `is_<format>(path) -> bool` and
`parse_<format>(path) -> dict`. Add routing in
`commands/info.py:_detect_format`.

### New command

Add `commands/<verb>.py`. Implement `register(subparsers)` and
`run(args)`. Commands are auto-discovered by `cli.py`.

### New MCP tool

Add a `_tool(...)` registration in `mcp_server.py`. The handler should
call into `core/` rather than duplicate logic. If the tool needs a new
primitive that doesn't exist in `core/` yet, add the primitive to
`core/` first and call it from both the MCP handler and any CLI
equivalent.

---

## Non-goals (architectural)

These are deliberately not part of the system and shouldn't be added
without re-reading the direction doc:

- No daemon process, no long-running server beyond what MCP requires.
- No background indexing. Indexing runs when the user asks for it.
- No cloud sync or remote DB.
- No per-user auth or multi-user permissions.
- No GUI layer.
- No workflow verbs that fuse multiple primitives.

The composable primitives are the whole product. Anything that looks
like "acidcat decides how to use itself" belongs in the client, which in
the MCP case is the LLM agent.
