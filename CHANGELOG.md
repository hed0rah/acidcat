# Changelog

All notable changes to acidcat. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once it leaves alpha.

## [0.9.3] - 2026-06-28

### Fixed

- `inspect` no longer misidentifies ADTS AAC as MP3. The no-ID3 dispatch
  accepted any 11-bit frame sync (0xFFE mask), which ADTS (sync 0xFFF, layer
  bits 00) passed; a forward scan then locked onto a coincidental MPEG frame
  and reported a ~16 s AAC as a ~3 s Layer II MP3. Dispatch now requires a
  fully valid MPEG frame at offset 0.
- `inspect` lints a FLAC metadata block whose declared length overruns the
  file, mirroring the WAV/AIFF overrun checks. A truncated FLAC whose PADDING
  block claimed 8192 bytes previously warned about nothing.
- `inspect` no longer flags `avg_bytes_per_sec` on non-PCM `fmt ` chunks. The
  identity `avg = sample_rate * block_align` is PCM-only; ADPCM (tag 0x0002 /
  0x0011) tripped it. Gated to `tag == 1`, matching the `block_align` check.

### Changed

- The interactive format-anatomy pages (`docs/formats/*-anatomy.html`) share a
  reworked inspector layout: the field-detail panel sits flush with the byte
  diagram and no longer reflows on hover, and hovering or clicking a byte/bit
  square highlights its field both ways.
- Format-internals docs corrected against the specs: MIDI SMPTE decode and a
  tempo-table hex typo, the WAV ACID hex example and bext field widths, the
  MP3 frame-sync value, the AIFF 80-bit float sign and AIFC COMM minimum size,
  and the WAV `format_tag` table gained `0x0002` (MS ADPCM).

## [0.9.2] - 2026-06-28

### Added

- `acidcat inspect --color {auto,always,never}` syntax-highlights the table
  dump. Default `auto` colors only when stdout is a TTY and honors the
  `NO_COLOR` env var; explicit `always`/`never` override both. The palette
  encodes role like syntax highlighting: cyan for structure (chunk ids,
  format label), green for decoded values, dim for offsets/sizes/notes, red
  for warnings. JSON output is unaffected.

## [0.9.1] - 2026-06-28

### Fixed

- `inspect` no longer derives metrics from a chunk size larger than the
  file. A WAV `data` or AIFF `SSND` chunk that overruns the file is linted
  as before, but frames/duration and the reported payload now come from the
  bytes actually present, not the declared size. A 52-byte WAV claiming a
  2 GB `data` chunk previously reported a 24347-second duration.

### Added

- `docs/formats/riff_wav.md` gains edge-case and robustness sections.
- Interactive single-file format references under `docs/formats/`:
  `mp3-anatomy.html`, `wav-anatomy.html`, `flac-anatomy.html`,
  `aiff-anatomy.html`, `midi-anatomy.html`. Hover a field to highlight its
  bytes or bits and read the decode; click to open lookup tables. Byte
  content is verified against acidcat's own parsers.

## [0.9.0] - 2026-06-27

### Added

- `inspect --frames` (`-F`): a per-element deep dump for formats whose
  elements carry their own structure. For MP3 it lists every MPEG frame
  (offset, bitrate, sample rate, channel mode, size), surfacing the
  per-frame bitrate switching that the default summary collapses to a
  range. For MIDI it lists every event (tick, type, decoded detail:
  note names with velocity and channel, tempo/meter/key meta, control
  changes, pitch bend). WAV/AIFF/FLAC carry no per-element structure
  (uniform headerless PCM or opaque codec frames), so the flag is a
  no-op there and says so. Listings are emitted in both table and JSON
  output and capped defensively at 100k rows.

## [0.8.0] - 2026-06-26

### Added

- `inspect` now walks **MP3** and **FLAC**, decoded by hand with no
  mutagen dependency so the dump shows real byte offsets and flags any
  spec violations the tag libraries would paper over. MP3 reports the
  ID3v2 header and frame list, the first MPEG frame fully decoded
  (version/layer/bitrate/sample rate/channel mode/CRC), the Xing/Info
  VBR header with the LAME extension (encoder, VBR method, lowpass,
  gapless delay/padding), a CBR-vs-VBR frame-run summary, and an ID3v1
  trailer when present. FLAC walks the metadata blocks (STREAMINFO,
  VORBIS_COMMENT, PICTURE, SEEKTABLE, APPLICATION, PADDING) plus the
  audio-frame extent, linting the STREAMINFO-first and last-block-flag
  rules.

## [0.7.1] - 2026-06-11

### Fixed

- **Re-registering a pre-0.5.4 library no longer crashes** (or worse).
  The central db filename scheme changed in 0.5.4 (label hash 8 -> 12
  chars), so re-registration computed a different db_path for the same
  root, slipped past the db_path upsert, and hit the root_path UNIQUE
  constraint; without the crash it would have attached a fresh DB and
  orphaned the old one with its tags and descriptions. The registry
  now treats the root as the library's identity and reuses the stored
  db_path (explicit central/in-tree transitions still re-key), and the
  CLI adopts the canonical path the registry returns. Found when a
  `--force` reindex of a v0.5.0-era library crashed.

### Added

- `inspect` walks RF64 (ds64 64-bit size overrides per EBU Tech 3306,
  with sentinel and ordering lints) and Xfer Serum presets (signature,
  decoded JSON metadata, blob extent). Every format acidcat parses
  natively is now inspectable.

## [0.7.0] - 2026-06-11

Apple Loops support and the tagged-format tags gap.

### Added

- **Apple Loops `basc` parsing**: AIFF rows gain their first
  chunk-level tempo and key source. No official spec exists; the
  layout was field-verified against 103 indexed Apple Loops (derived
  bpm `beats / duration * 60` matched the filename bpm on every
  file). Indexing derives bpm and root pitch class ahead of filename
  fallbacks; `inspect` decodes basc and labels the companion
  cate / trns / coll / FLLR chunks. The scale enum is surfaced raw
  pending a verified mapping.
- **Tagged-format genre frames populate the tags table**, so
  `query --tag house` works against mp3/flac/ogg libraries.
  Multi-genre strings split on `,` `;` `/`.

Existing libraries pick these up with `acidcat index DIR --force`.

## [0.6.0] - 2026-06-11

Hardening release closing the deferred-to-v0.6 list from 0.5.5, plus
the inspect verb growing AIFF and MIDI walkers.

### Added

- `acidcat index --force`: re-extract metadata even for files whose
  mtime and size are unchanged. Use after a parser upgrade; preserves
  tags, descriptions, and features (unlike `--rebuild`, which wipes
  them). Mirrored as a `force` param on the MCP `reindex` tool.
- `acidcat inspect` now walks AIFF/AIFC (COMM with the 80-bit rate
  and AIFC compression, SSND with a COMM-frames cross-check, MARK
  enumeration, the 20-byte INST with sustain/release loops, text
  chunks) and Standard MIDI Files (both division forms decoded,
  per-track stats, lints for length lies, missing end-of-track,
  missing tempo, declared-vs-found track counts).

### Fixed

- MCP `locate_sample` and `list_tags` LIKE patterns escape user
  input (`_`/`%` were wildcards); `remove_root`'s legacy LIKE
  fallback escapes the root path the same way. The escape helper
  moved to `core/index.py` as `escape_like`.
- MCP `search_samples` adopts the shared `fts5_syntax_message`
  wording for FTS5 syntax errors.
- Scope filters in `query`, the MCP server, and `--discover` compare
  paths case-insensitively on Windows; `compare_path` now also
  lowercases on macOS (APFS/HFS+ default case-insensitive).
- `_extract_for_index` logs the exception class and message for
  failed files instead of making programming bugs look identical to
  corrupt files.
- The index walk commits before `prune_missing`, so a prune failure
  cannot roll back the trailing batch of upserts.

## [0.5.9] - 2026-06-11

### Fixed

- **acid_beats vetting**: the 0.5.7 layout fix surfaced real beat
  counts, which exposed a second-order problem: batch taggers leave
  boilerplate beats/tempo (8 / 120) in files whose one-shot flag is
  set, and surfacing those made kind inference read 0.1s one-shots
  as loops. New shared helper `effective_acid_beats` vets the field:
  trust beats when the one-shot bit is clear (field-measured ~93%
  reliable), otherwise keep them only when they reconcile with the
  actual duration within 15% (vendors sometimes set the bit on real
  loops with accurate counts). `parse_riff` now also surfaces
  `acid_one_shot`. Wired into info, scan, and the indexer.

## [0.5.8] - 2026-06-11

P1 slice 2: AIFF and MIDI deep-verified against their specs, three
spec-conformance fixes, byte-level diagrams for both formats.

### Fixed

- **MIDI SMPTE division**: files with bit 15 of the division field
  set use SMPTE timing (negative frame rate in the high byte, ticks
  per frame in the low byte). Duration is now computed as
  ticks/(fps*tpf) per SMF 1.0 instead of feeding the raw division
  into the ppqn formula; -29 maps to 29.97 drop-frame.
- **MIDI running status**: meta and sysex events now cancel running
  status per SMF 1.0, so malformed data bytes after a meta event no
  longer decode as phantom notes through the stale status.
- **AIFC `raw ` compression**: the compression 4cc is now matched
  with its trailing space intact, so raw-PCM AIFC reports `raw`
  instead of `unknown:raw`.

### Documentation

- `docs/formats/aiff.md`: byte-ruler maps for COMM, SSND, MARK
  entries and INST, a bit-level diagram of the 80-bit extended
  float, and a corrected sample-rate hex table (the previous
  44100/48000/22050 encodings were one exponent too low).
- `docs/formats/midi.md`: MThd map, division bit diagram for both
  timing forms, status-byte bit split, a worked VLQ example, and
  the running-status cancellation rules.

## [0.5.7] - 2026-06-10

First slice of the format-internals work: a High-severity parser fix
found by verifying the docs against primary sources, byte-level format
documentation, and a new readelf-style inspect verb.

### Fixed

- **acid chunk misparse**: `acid_beats` was read from the unknown
  float at offset 8 (0 in every spec-conformant file) instead of the
  real `num_beats` at offset 12, and the meter fields were read as two
  uint32s spanning the wrong bytes. Verified against libsndfile and
  hex dumps of ACIDized packs from four vendors. The long-standing
  "acid_beats is usually 0" behavior was this bug, and kind inference
  never saw a real beat count. **Reindex libraries to refresh stored
  `acid_beats` values.**

### Added

- `acidcat inspect FILE`: readelf-style structural dump. Chunk table
  with offsets and summaries, decoded per-field breakdown for fmt
  (incl. extensible), data, fact, acid, smpl, inst, cue, LIST and
  bext, `--hex` for raw bytes next to each field, `-f json` for
  machines, and lint warnings for spec violations (riff_size lies,
  loop points past EOF, acid beat/duration drift, cue count lies,
  fmt-after-data ordering).

### Documentation

- `docs/formats/riff_wav.md`: ELF/TCP-style byte-ruler diagrams for
  the RIFF header, chunk envelope, fmt, acid, smpl header and loop
  entries, cue entries, and inst, plus layout provenance notes for
  the acid chunk.

## [0.5.6] - 2026-06-10

Docs and repo hygiene release. No code changes; 263 tests unchanged.

### Documentation

- `docs/codebase_explorer.html` rebuilt for v0.5.x: ingest and fan-out
  flow diagrams, module table with filter tabs, 49 hover cards with
  code snippets covering DSP internals (MFCC, chroma, tempo
  estimation, Camelot math) and format internals (RIFF chunks, 80-bit
  AIFF floats, MIDI running status).
- Added `docs/audio_file_formats.md`, a coverage map of audio formats
  for future readelf-style expansion.

### Housekeeping

- Internal working documents (handover notes, raw audit report)
  removed from the repo; the explorer is the maintained reference.
- `.gitignore` covers bug-hunter state, logo design sources, and a
  local `.stash/` scratch directory.

## [0.5.5] - 2026-05-20

Bug-hunt followup release. Closes all 8 findings from the 2026-05-11
adversarial bug hunt plus the related output-stream encoding cleanup
surfaced in the 2026-05-19 broad review. 263 tests pass (8 new), up
from 254 in 0.5.4.

### Fixed

- **B-1**: MIDI running-status branch advanced `pos` by one fewer
  byte than expected, desynching the parser on any file emitted by
  Ableton, Logic, FL Studio, Cubase or Reaper. `note_count`,
  `note_min`, `note_max` and `duration_ticks` were all wrong on those
  files. Two-byte messages now advance `pos += 2`; one-byte messages
  (program change, channel pressure) advance `pos += 1`.
- **B-2**: `rebuild_fts_for_path` no longer wraps its DELETE + INSERT
  in `with conn:`. Python's sqlite3 connection context manager
  committed the active transaction on normal exit, so the deliberate
  `_COMMIT_EVERY_N_FILES = 100` batching in `_walk_and_upsert` was
  paying a commit + fsync per file. Noticeably faster reindexes on
  HDD-backed sample drives and network mounts.
- **B-3**: Camelot parser no longer lowercases the mode suffix, so
  `CM`, `DM`, `EM` etc. from Beatport, Mixed In Key, Serato and
  Rekordbox resolve to major instead of being mis-classified as
  minor. `find_compatible` returned harmonically wrong neighbors
  for any sample tagged this way.
- **B-4**: librosa key detection returns `None` when chroma cannot
  determine major vs minor mode, letting the filename parser (which
  carries mode explicitly) win instead of always emitting bare-letter
  keys that downstream code interpreted as major. Affected `--deep`
  on files with no filename key hint.
- **B-5**: `acidcat scan` no longer emits `C-1` for samples whose
  SMPL chunk has `root_key=0` (the documented "unset" sentinel).
  Now matches the info and index paths. New shared helpers
  `smpl_root_or_none` / `acid_root_or_none` in `core/riff.py`
  consolidate the three call sites.
- **B-6**: FTS5 syntax errors in `acidcat query --text` (e.g.
  `(foo`, `NOT`, `foo OR`) now surface a single helpful stderr
  message and exit code 1, instead of silently zeroing the result
  set across every library. New `FTSQueryError` and
  `fts5_syntax_message` helpers in `core/index.py` let the MCP server
  share the wording when it adopts them.
- **B-7**: CUE chunk parser caps `num_cues` against payload size, so
  a corrupt or malicious WAV claiming `num_cues=0xFFFFFFFF` no longer
  spins ~4 billion iterations before producing zero output. Reachable
  via `acidcat chunks` / `acidcat survey` walking a bad file.
- **B-8**: `_import_tags` LIKE pattern now escapes `_` and `%` so a
  legacy tags-json entry for `kick_126.wav` cannot accidentally land
  on `kickX126.wav`. New `_escape_like` helper paired with
  `ESCAPE '\\'`. The two read-only LIKE sites in `mcp_server.py`
  (`locate_sample`, `list_tags`) carry the same pattern and will be
  fixed in the next MCP touch.

### Changed

- Output streams in `info`, `chunks`, `survey`, `detect`, `features`
  now open with `encoding='utf-8'`. `scan` and `query` already did;
  the others used the locale default (cp1252 on Windows), mangling
  non-ASCII tag values via the `-o` path.

### Deferred to v0.6

- Unifying `_sniff_format` / `_detect_format` / extension-set checks
  into one canonical `core.detect.classify(filepath) -> kind`. B-5
  proves the drift exists but the per-site fix is enough for 0.5.5.
- Adopting `FTSQueryError` / `_escape_like` in `mcp_server.py`.
- Deprecating the legacy CSV `commands/search.py` in favor of `query`.
- Tagged-format `tags` table population (genre frames currently only
  reach the FTS index).
- macOS APFS case-insensitive overlap-check parity.

---

## [0.5.4] - 2026-05-02

Audit-driven correctness, hardening, and PyPI prep release. 14 stacked
commits closing all 26 actionable findings from the 2026-05-02
codebase review. Test count grew from 232 to 254.

### Breaking

- MCP tool `describe_sample` renamed to `set_sample_description`. The
  old name read like a getter but wrote the description column. Any
  saved MCP client session referencing the old name will break.
- MCP tool `discover_libraries` default flipped from `dry_run=false`
  to `dry_run=true`. A forgetful caller that omits the flag now gets
  a preview rather than a destructive registry mutation. Existing
  callers that pass `dry_run` explicitly are unaffected.
- `mutagen` moves from the optional `[tags]` extra to base
  dependencies. `pip install acidcat[tags]` is no longer valid; use
  `pip install acidcat`. The motivation is that `_extract_for_index`
  routes mp3/flac/ogg/m4a through this module, and a fresh user
  without mutagen would see those files silently skipped on indexing.

### Fixed

- **F-01**: serum preset parser replaces an O(n^2) progressive-slice
  JSON scan with `json.JSONDecoder().raw_decode` for a single linear
  pass.
- **F-02**: `discover_libraries` MCP default now `dry_run=true` (see
  Breaking).
- **F-04**: `analyze_sample` and `detect_bpm_key` now declare
  `idempotentHint=false` so MCP clients do not coalesce or cache
  repeat calls (the underlying file may change between calls).
- **F-05**: WAV parser caps chunk reads at 64 KB. Unbounded
  `f.read(chunk_size)` could OOM on a malformed WAV claiming a 2 GB
  chunk.
- **F-06**: MIDI sysex VLQ length is now bounded against remaining
  track bytes. A malformed SMF could previously push past the MTrk
  boundary into the next track's data, scrambling output.
- **F-07**: FTS5 syntax errors in `search_samples` text now surface as
  a clean `ToolError` with a helpful message, instead of leaking SQL
  internals through the catch-all dispatcher.
- **F-08**: `find_compatible` with a keyless target (drum loops,
  percussion) now restricts results to other keyless samples instead
  of returning random-key samples that are musically nonsensical to
  layer with drums.
- **F-13**: Path comparison is now case-insensitive on Windows so
  `C:/MyLib` and `c:/mylib` cannot both register as separate
  libraries. Stored paths are not mutated; only comparisons change.
- **F-14**: `rebuild_fts_for_path` is now wrapped in an explicit
  transaction. Previously an early return could leave the FTS table
  out of sync with the samples table.
- **F-15**: `register_library` opens a `BEGIN IMMEDIATE` for the
  duration of the no-overlap check + insert, closing a TOCTOU race
  where two concurrent registrations could both pass the check.
- **F-21**: format dispatch now sniffs magic bytes before consulting
  the file extension. Double-suffixed files (e.g. AIFF renamed to
  `foo.aiff.wav`) route by content, not by suffix.
- **F-22**: `_apply_schema` raises `SchemaVersionError` /
  `RegistrySchemaVersionError` on a version mismatch instead of
  silently running old SQL against a future schema.
- **F-25**: label-fallback hash in `--discover` now incorporates the
  candidate root path, so two unrelated roots that both default to
  the same `base_label` no longer collide on the deterministic hash.
- **F-26**: tagged.py strips a leading UTF-8 BOM from ID3v2 / Vorbis
  tag values so it does not leak into the FTS index.

### Changed

- **F-09**: `analyze_sample` description now reads `"SLOW (~1-10s
  after warm-up; first call ~30-60s due to librosa import)"` so the
  LLM and user can set expectations about the cold-start tax.
- **F-10**: filename BPM ceiling raised from 200 to 300. DnB at 174,
  hardcore at 220, gabber at 240 all pass cleanly now.
- **F-12**: AIFC compression types are validated against a known set;
  unknown codes surface as `unknown:<raw>` rather than being silently
  treated as PCM.
- **F-16**: `_close_all` logs close failures to stderr instead of
  swallowing them silently. Past sessions have masked database-locked
  and corruption signals from this exact path.
- **F-17**: destructive MCP tools (`register_library`,
  `forget_library`, `tag_sample`, `set_sample_description`) lead with
  `"Destructive."` in parallel with the existing `"Fast."` /
  `"SLOW."` cost-prefix scheme.
- **F-20**: removed unused `deep` parameter from `analyze_sample`
  schema (handler ignored it).
- **F-23**: `path_hash` widened from 8 hex chars (32 bits, birthday
  collision near 65k) to 12 hex chars (48 bits, near 16M). Existing
  libraries keep their 8-char filenames because the registry stores
  `db_path` explicitly.
- **F-24**: `_walk_and_upsert` passes `followlinks=False` explicitly
  to `os.walk` for clarity.
- **F-29**: comment on `_REGISTRY_PATH` clarifies set-once intent.

### Documentation

- Added `docs/codebase_explorer.html`: a self-contained LaTeX-style
  reference with margin cards and hover details for every module,
  MCP tool, and audit finding.
- `docs/architecture.md` rewritten for the v0.5 per-library + registry
  layout. Previously described the v0.4 single-DB model.

### Verified, no change needed

- **F-03** (claimed `register_library` not idempotent): `idx.open_db`
  opens existing DBs without modification; annotation was correct.
- **F-11** (claimed SMPL note 0 = phantom key on index path):
  `commands/index.py:877-880` already filtered via `if not smpl`.
- **F-19** (claimed `infer_kind` mis-bins 1s loops): review misread
  `or` as `and`.
- **F-27** (claimed RIFF chunk padding bug): pos arithmetic at
  `riff.py:213-215` already adds 1 for odd `chunk_size`.

### PyPI prep

- `pyproject.toml` gains `[project.urls]` (Homepage, Repository,
  Issues), `authors`, expanded classifier set (Development Status
  bumped to `4 - Beta`, per-minor python 3.9..3.13, `OS Independent`),
  and an explicit readme `content-type`.
- `python -m build` and `twine check dist/*` both green.

---

## [0.5.3] - 2026-04-29

CLI safety + post-discover stats + migration helper. Single-PR release.

- CLI collision guard: passing both a target and a management flag
  (e.g. `acidcat index DIR --list`) now errors instead of silently
  ignoring one.
- `--discover` now pre-touches the per-library DBs so list output
  shows accurate sample counts immediately rather than `?`.
- New `acidcat index --refresh-stats` command to populate stale `?`
  sample counts in `--list` for libraries registered before v0.5.1.

## [0.5.2] - 2026-04-28

`--discover` walker for bulk library registration.

- `acidcat index --discover ROOT [--min-samples N --max-depth D]`
  walks a tree and registers every qualifying subfolder as its own
  library.
- New `discover_libraries` MCP tool wraps the same helper.

## [0.5.1] - 2026-04-25

Bug-fix release from real-world testing.

- BPM filename parser regex tightened: `91V_SBH_126_*` now correctly
  parses as 126 BPM, not 91.
- `register_library` now refreshes cached counts when re-attaching
  to an existing per-library DB.
- `find_similar` gains `kind_filter` plus percentile / relative
  scoring.
- CLI walker commits every 100 files instead of once at the end so
  large indexes survive interruption with partial progress.

## [0.5.0] - 2026-04-23

Per-library SQLite indexes + global registry + fan-out queries.
Replaces the v0.4 single global DB.

- Per-library DBs at `~/.acidcat/libraries/<label>_<hash>.db`
  (central default) or `<library>/.acidcat/index.db` (in-tree opt-in).
- Global registry at `~/.acidcat/registry.db` lists every library.
- Mandatory labels, no nested libraries, ambient orphan handling.
- 18-tool MCP surface (read + write + index management).

## [0.4.x]

The single-global-DB era. Documented at this version for historical
reference; not maintained.
