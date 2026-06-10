# Changelog

All notable changes to acidcat. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once it leaves alpha.

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
