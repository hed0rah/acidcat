# Changelog

All notable changes to acidcat. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project will
adopt [Semantic Versioning](https://semver.org/spec/v2.0.0.html) at 1.0.

## [Unreleased]

## [0.58.0] - 2026-07-19

### Added

- **IFF 8SVX walker** (`core/walk/svx.py`) -- the Amiga 8-bit sampled-voice
  format, a direct big-endian ancestor of RIFF/WAVE. Decodes the `VHDR` voice
  header (rate, octave count, raw vs Fibonacci-delta compression, 16.16 volume),
  the `NAME`/`AUTH`/`ANNO`/`(c) ` text set (`ANNO` is the Amiga-side `ISFT`
  authoring-tool tell), `ATAK`/`RLSE` envelopes, `CHAN`, and `BODY` with derived
  duration; flags the FORM-size undercount writer bug. Field-tested on 492 real
  Amiga specimens (0 crashes). Plus a mirrored 8SVX anatomy datasheet in
  `docs/formats/`.
- `clm ` chunk recognized as **Xfer Serum** wavetable export (corpus-confirmed
  against 43 files, all wavetables, none carrying an `acid` chunk -- correcting
  the old "Sonic Foundry" web lore). Format tag **0x0039** named Roland RDAC
  (RFC 2361), noting mmreg.h's unregistered Crystal IMA ADPCM squat at the same
  value.

## [0.57.0] - 2026-07-19

### Added

- **`acidcat census`** -- a scaled-up, read-only chunk-ID histogram and
  open-question survey over a corpus of RIFF-family files (containers, format
  tags, LIST types, fact sizes, bext versions, and flags for the rare/
  undocumented chunks). Engineered for millions of files: an explicit-stack
  `os.scandir` traversal that never stats a file it will not open (extension +
  `d_type` off the dirent), is loop-safe and boundary-aware (unconditional
  `(st_dev, st_ino)` directory dedup catches symlink loops and same-device bind
  mounts; autofs mountpoints read from the mount table, never probed; optional
  `--one-file-system`), positioned `pread` reads of chunk headers only (no audio
  payload), with `posix_fadvise` readahead suppression + `DONTNEED` so a scan
  does not evict the page cache, and an SSD/HDD-aware reader thread pool. Fixes
  two bugs carried by the prototype: the bext version was read at payload offset
  602 instead of 346, and non-printable FOURCCs from corrupt files were emitted
  raw (now grouped as `hex:` tokens so the JSON stays well-formed). RF64/BW64
  data-chunk sizes are resolved from the `ds64` chunk, so the walk reaches
  trailing chunks past the `0xFFFFFFFF` sentinel instead of stopping at `data`.

## [0.56.1] - 2026-07-19

### Fixed

- **Kurzweil `.KRZ` keymap sample references.** The keymap decoder assumed the
  modern 5-byte entry layout (method `0x13`: tuning, sampleID, subSample) and
  always read the referenced sampleID at entry offset +2. Real Sweetwater
  soundset banks use method `0x03`: a 3-byte entry with no tuning prefix, so the
  sampleID sits at offset 0 and was misread (e.g. sample 200 reported as 256).
  The decoder now locates the sampleID after the optional 2-byte tuning prefix,
  keyed on the method `0x10` bit, with a bounds-guarded read; `entry_size`
  remains the authoritative stride. Correct for every no-tuning method
  (`0x01`-`0x0f`); tuning methods (`0x11`-`0x19`) unchanged. Swept the Sweetwater
  corpus (0 crashes); verified against real banks (e3_bass -> sample 200,
  angkorw -> 201/203/204).

### Docs

- Added the Kurzweil `.KRZ` anatomy datasheet (`docs/formats/krz-anatomy.html`):
  a 7-tab interactive byte map (PRAM header, object framing, Sample, Keymap,
  Program, SROM) built from real specimen bytes. Homogenized the anatomy fleet
  onto one canonical renderer and added the color-key legend to the emu and mpc
  sheets.

## [0.56.0] - 2026-07-19

### Docs

- Regenerated `docs/codebase_explorer.html` (was a v0.9.0 fossil): reframed from a
  WAV metadata explorer to the current byte-level dissection / reverse-engineering
  tool, updated to 23 CLI verbs, 26 walkers, the dissection surface
  (inspect/probe/carve/shape + the repair/validate/audit constraint model), the
  grammar engine, and provenance -- in the locked amp-orange aesthetic with the
  hover margin cards.

### Changed

- Provenance writer signatures now live in a sidecar data file
  (`core/data/provenance_signatures.json`) instead of being hardcoded in
  `provenance.py` -- signatures are data, editable without touching code. A user
  override at `~/.acidcat/provenance_signatures.json` is merged on top (its
  `canon` rules win first-match, its `chunk_signatures` are appended), so
  signatures can be added with no code change or re-release.

### Added

- New WAV writer signatures, validated against a 807,394-file corpus census:
  `strc`/`str2`/`SyLp` (Sony/Magix ACID / Sound Forge), `SNDM` (Soundminer),
  `LGBM` (Logic, folded in with LGWV/ResU), `DIGI` (Digidesign, with DGDA),
  `RLND` (Roland), `Cr8r` (GoPro CineForm / Adobe). `_PMX` (Adobe XMP) is
  recorded as a deliberately-shared, corroborate-only tell, not a standalone
  attribution.
- ffmpeg RF64_AUTO structural fingerprint: a 28-byte `JUNK` chunk immediately
  after `fmt ` (the ds64 placeholder) is detected positionally and attributed to
  FFmpeg/libav at "likely" confidence.

## [0.55.1] - 2026-07-18

### Fixed

- `acidcat info` (and bare-path `acidcat file.ext`) no longer mis-parses a
  non-WAV structural format as a headerless WAV (the misleading "Chunks (none)"
  output). Any format the walkers decode but `info` has no dedicated builder for
  -- Kurzweil `.KRZ`, E-mu, Akai, ReCycle, VST FXP, trackers, SF2, the MPC
  family, synth presets, RF64, ... -- now gets a walker-backed summary (format
  label, top-level summary, region count) and a pointer to `acidcat inspect` for
  the full decode. Previously only a hardcoded preset list was handled.

### Changed

- Package description aligned with the project's direction: "Binary analysis and
  reverse-engineering for audio file formats and music-production hardware".

## [0.55.0] - 2026-07-18

### Added

- Kurzweil K2000 / K2500 / K2600 (VAST) `.KRZ` bank walker (`core/walk/krz.py`).
  Decodes the flat object-database container (big-endian `PRAM` header,
  negative-blocksize object walk, `type<<10 | id` hashing, the int32 end marker,
  and the trailing 16-bit PCM region) and the Sample (root key, rate from
  samplePeriod, loop/one-shot flag, PCM word offsets), Keymap (method, referenced
  sample ids), and Program (VAST layer count) object bodies. Setup / Master /
  Studio-FX and other object types are surfaced by type/id/name. `SROM`
  effects/sample-ROM files (same `.krz` extension) are recognized header-only.
  Reverse-engineered from the mpc2emu GPL reference + the KurzFiler source and
  verified structurally against 242 real Sweetwater soundset banks (zero
  crashes). The VAST program's per-parameter decode and the CAL keymap-ref offset
  are follow-ups.

## [0.54.0] - 2026-07-18

### Added

- `acidcat similar FILE` -- an index-backed similarity CLI verb, replacing the
  retired CSV-era one. It fans out across every registered library and ranks by
  z-standardized cosine over the stored feature vectors, filtered to the target's
  kind (loop/one-shot) by default. The scoring logic moved into
  `core/search.py`, so this verb and the MCP `find_similar` tool now call one
  shared implementation instead of the tool owning private logic -- the CLI and
  MCP can no longer drift. If the reference is not indexed, its vector is
  extracted live (needs `[analysis]`).

- Descriptor-driven fuzzing (`tests/test_descriptor_fuzz.py`): a generator
  derives the walker's decision points mechanically from the WAVE grammar
  descriptor (every `Valid` range edge, every `Switch` case at each window size
  around its `min_window`, every `min_len` truncation edge, each
  `Requires`/`Order` rule) and asserts the walker, the interpreter, and the
  strict `structure` parser agree at each one. It reaches boundary-exact inputs
  the seeded-random differential fuzz cannot, and its interpreter-vs-walker
  parity check catches a walker regression even when the walker swallows it into
  a per-chunk warning (a class the never-raise contract otherwise hides). This
  is the first non-test consumer of the grammar interpreter.
- Grammar engine, FLAC foundation: the declarative descriptor now reproduces
  FLAC STREAMINFO byte-for-byte against the walker (fields + summary, validated
  across the fixtures and a descriptor-derived fuzz sweep). This is the second
  container format and the first non-RIFF one, and it introduces three engine
  primitives WAV never needed: a second container strategy (`flac_blocks`, over
  `core/flac.iter_metadata_blocks`), the `Codec` type (the 3-byte big-endian
  frame sizes via `u24be`) and `Raw` type (the md5 signature), and the
  `BitGroup` construct for the overlapping bit-packed fields
  (sample_rate/channels/bits/total_samples share one 8-byte word). Opt-in and
  test-only, like the WAV descriptor. The other FLAC block types stay
  walker-side until the repeat-over-records construct lands.

### Fixed

- The "degrade with warnings, never raise" walker contract is now enforced
  structurally, at the `walk_file` boundary every consumer shares: a walker
  bug on hostile input degrades to a walker-error warning instead of a
  traceback out of `od`, `audit`, the TUI, or the public `acidcat.walk()`
  (previously only `inspect` and `shape` carried their own catch-all).
  `ACIDCAT_WALKER_RAISE=1`, set by the test suite, re-raises so a walker
  defect stays a loud CI failure.
- Two walker conventions became mechanically enforced invariants
  (`tests/test_walker_invariants.py`): no argless `.read()` in `core/walk/`
  (the class behind the historical sf2/rmid memory-amplification bugs), and
  every semantic ctx key the wav/aiff/midi walkers publish must be registered
  in `vocab.CTX_KEYS` (so a walker rename cannot silently desynchronize from
  the scan path). The ctx check immediately surfaced three unregistered MIDI
  keys (`division`, `format`, `tracks`), now registered.

### Removed

- The CSV-era `similar` and `search` verbs (pandas/sklearn pipelines over
  one-off scan CSVs, untested and index-unaware; the index-backed
  `find_similar` MCP tool is the real implementation) and the `--ml-ready`
  flag on `scan`/`features`, whose only consumer they were. The `[ml]`,
  `[viz]`, and `[notebook]` extras are gone with them; `[all]` now means
  `[analysis,mcp-http,tui]`.
- The legacy parsers the 2026-07 walker unification left behind:
  `parse_riff`, `get_duration` (core/riff.py), `parse_aiff`, `parse_midi`,
  `parse_serum_preset`. All had zero production callers except `parse_riff`,
  whose one caller (`acidcat chunks`) now reads parsed fields from the
  inspect walker, so every format is decoded in exactly one place. Their
  malformed-input safety tests (cue-count bomb, acid layout/padding/
  truncation, running status, SMPTE division, key signatures, AIFC
  compression codes, non-finite 80-bit rates) were retargeted at the
  walkers before deletion.

### Changed

- `acidcat chunks` parsed-field output now comes from the WAV walker:
  every decoded field with its real name (previously a fixed subset of
  acid/smpl/fmt/cue/LIST/bext fields).

## [0.53.0] - 2026-07-18

### Added

- E-MU Emulator X / Proteus X (E5B0) voice DSP decode in verbose mode. `inspect
  --verbose` / `-F` now dumps each voice's front-panel controls: the filter
  (type, normalized frequency, resonance from `E5Fl`), the three envelopes
  (Amp/Filter/Aux, six stages with levels as percent from `LIST/EvL ` -> `E5Ev`),
  and the active modulation cords (source -> destination -> amount from
  `LIST/CrdL` -> `E5Cd`, with a partial index -> name table). The default view
  stays compact. Every parameter is anchored to a named `E5V1` sub-chunk; the two
  nonlinear scales (filter frequency, envelope rate) are shown as the raw
  normalized float rather than a fabricated physical value. All offsets verified
  by save-and-diff against Emulator X. The anatomy sheet gains an `E5V1` tab.

## [0.52.0] - 2026-07-17

### Fixed

- MP3 frame walker performance: on a sync loss it rescanned with a seek + read
  per byte, so `acidcat audit` on a crafted MP3 (a valid frame then a long run
  of `0xFF`) took ~21 s for 30 MB. It now scans a buffered window in memory and
  bounds the resync distance; the same input is ~1 s.
- MIDI System Common / Real-Time message advance: 0xF1 (MTC) and 0xF3 (song
  select) carry 1 data byte and 0xF2 (song position) carries 2, but all were
  skipped by a blind 2 bytes, misaligning the events that followed (wrong track
  stats). Fixed in both the walker and the legacy parser.
- Command-layer memory: `audit`, `od`, and `probe` read the whole input with
  `f.read()`, so peak memory scaled with file size (~96 MB on a 48 MB file).
  They now memory-map the input (`core/mapped.map_file`), keeping peak memory
  flat regardless of size (audit 96 -> 0.2 MB, od 96 -> 0.03 MB, probe 48 ->
  0.04 MB) with no loss of full-file coverage. The mmap-safety fixes this
  surfaced are included: memoryview element access in `structure`, and
  copy-on-read paths in `mp4repair`/`countrepair`/`repairers`.

### Added

- E-mu Emulator X / Proteus X (E5B0) voice decode: the walker now reads each
  voice's `LIST`/`TWL ` crossfade windows and reports the per-voice key range
  and (when narrowed) velocity window on the E5P1 preset. It also stops
  deduping by sample index, so a multisample preset shows its full
  keyboard/velocity map instead of collapsing voices that reuse a sample. The
  window byte layout (lo at [4], hi at [7]) was confirmed by save-and-diff
  against Emulator X.
- A differential + round-trip fuzz harness (`tests/test_differential_fuzz.py`):
  seeded mutations of a hermetic IFF file assert the lenient walker degrades and
  the strict `structure.parse`/`emit` either round-trips byte-exactly or raises
  `StructError`, exercising the strict/lenient split the audit noted was untested.

## [0.51.0] - 2026-07-17

### Fixed

- Robustness hardening, driven by an external audit (untrusted-input safety).
  Crashes that violated the "degrade with warnings, never raise" contract now
  degrade: the tracker walkers (`.xm`/`.it`/`.s3m`) on a truncated header, and
  the SigMF walker on a `.sigmf-meta` sidecar whose JSON has the wrong types
  (non-object top level, non-dict `global`/captures/annotations, or a string
  where a number is expected). Memory-amplification gaps closed with read caps:
  RMID, SF2 (now validated before the whole-file read), the `.xpn`
  `Expansion.xml` member (streamed), the NI `.ksd` edit path (inflate cap), the
  Serum metadata reader, and the MIDI `--frames` event listing (now bounded
  while collecting rather than built in full then sliced). A cross-walker
  truncated-magic test guards the crash class against regression.

## [0.50.0] - 2026-07-17

### Added

- E-MU E5B0 (Emulator X / Proteus X) preset voice/zone decode: each `E5P1`
  preset now reports its voice count, zone count, and the SamplePool sample each
  zone plays (1-based index) with its root key, by walking the nested
  `E5VL` / `E5V1` / `E5ZL` / `Zhdr` chunk tree. The root key is verified against
  named sample pitches; the interior chunk walk uses the same adaptive
  word-alignment as the top level (the Proteus module banks do not pad odd
  interior chunks), and a desync or cap surfaces as a warning instead of
  degrading silently. Corpus: 169 banks, 0 crashes, 225,578 zones decoded.

## [0.49.0] - 2026-07-16

### Added

- E-MU sampler bank walker (`.E4B`, `.exb`, `.ebl`): reads both the EOS hardware
  bank (`FORM E4B0`) and the Emulator X / Proteus X software bank + sample
  library (`FORM E5B0`). Surfaces the container, the table of contents (with an
  offset cross-check against the chunk chain that flags corruption), presets
  (resolving voice/zone sample references back to names), and samples (name,
  rate, loop). Validated on ~19k real Emulator X / Proteus X files and 48 real
  EOS banks carved from a native E-MU CD-ROM; adaptive chunk word-alignment
  handles the non-padding Proteus-module banks, and CD-streamed banks (no EMSt,
  padded tails) are reported as informational notes rather than corruption.
- Format-anatomy datasheet for the E-MU family, `docs/formats/emu-anatomy.html`
  (interactive byte-level register maps for FORM/TOC2/E5P1/E5S1/E5SL/E4B0).

## [0.48.0] - 2026-07-14

### Added

- New format walkers: Arturia Analog Lab bank (`.labx`), ScreamTracker 3
  (`.s3m`), SigMF recordings and bare IQ captures (`.sigmf-meta`/`.sigmf-data`,
  `.cu8`, GQRX `.raw`, PortaPack `.C16`+`.TXT`), Akai S5000/S6000 programs
  (`.akp`), and the Akai MPC family: `.mpcpattern` sequences, `.xpm` keygroup
  programs, `.xpn` expansion packages, `.xtd` kits, plus the vintage MPC1000/2500
  and MPC2000 `.pgm` programs and MPC2000 `.snd` sounds. The tracker, SigMF, and
  vintage MPC formats expose their samples/segments as carveable byte regions.
- Provenance: DAW structural chunk-signatures (Apple Logic, MOTU Digital
  Performer, Bitwig, Avid/Digidesign) and narrow comment / device / tracker
  writer tells, corpus-verified.
- MP3: ID3 picture-frame (APIC/PIC) image data is surfaced as a carveable
  `<id>:image` region with an xref, flagging a complete embedded file
  (JPEG/PNG/GIF) and warning on non-padding bytes past its terminator.
- Vital: unknown top-level JSON keys and trailing bytes after the preset are
  flagged as unvalidated side-channels instead of being silently accepted or
  rejected.

- Public library API for tools built on acidcat: `edit_metadata(path, changes)`
  (plus `EditError` / `EditResult`), `read_tags(path)`, `read_id3v2(path)`,
  `list_id3v2_frames(path)`, and the `tui_theme` brand-palette module -- so
  downstream tools use public names instead of reaching into `commands` / `core`
  internals. The metadata-edit dispatcher moved from `commands/write` to
  `core/edits.edit_metadata`.
- TUI (`acidcat tui`): byte-view modes (`b` cycles hex / entropy / hilbert /
  histogram of the file), region audition (`p` plays the selected chunk as raw
  PCM via ffplay, `.` stops), and per-field hex tinting in the chunk view.

### Changed

- `core.viz.byte_class(b)` now returns `(glyph, class_name)` instead of
  `(glyph, hex_color)`; the byte-class -> color map lives in
  `tui_theme.BYTE_CLASS` so the CLI `probe map` and the TUI byte-map views share
  one palette (they had drifted). Breaking for any direct `byte_class` caller.

### Fixed

- `.multisample` zone and manifest chunks pointed at the ZIP local-file header,
  so `carve` yielded the header instead of the sample; they now use the entry's
  real data offset, and a STORED sample carves to the literal WAV/FLAC.
- MP4: re-read the `moov` box when a faststart layout overruns the head-read
  window, so a large-file encoder tag no longer goes missing.

## [0.47.0] - 2026-07-12

### Added

- Grammar engine, Phase 1: the declarative engine now reproduces the full WAV
  walker vocabulary byte-for-byte -- the complete `fmt ` chunk (every format-tag
  variant: PCM, MS/IMA ADPCM, MPEGLAYER3, and WAVE_FORMAT_EXTENSIBLE, dispatched
  by a `Switch` construct) plus the `inst` and `acid` regions. Adds the guard
  vocabulary (`Cmp` / `Remaining`), `Valid` plausibility warnings, note-sources,
  `Hex` / `Float` types, decode/summary helpers, format-level rules, and the
  per-region partition with a walk-scope dataflow rule. Verified byte-for-byte
  against the hand-written walker across the full WAV corpus (6,998 checks).
  Still opt-in and test-only; the walkers remain the oracle and the default.
- `acidcat shape DIR` -- a fast one-line structural fingerprint per file
  (format, key summary, chunk-id set) for specimen-hunting across a large
  library: pipe to `sort | uniq -c` to surface the rare or malformed shapes.
  Flags: `--fast` (header-only, no field parse), `--anomalies` (append the
  forensic anomaly type), `--format FMT` (filter), plus
  `--coarse` / `--no-path` / `--warn-only`.
- `acidcat od FILE` -- an objdump-x-style colored hex view: header bytes plus
  per-field offset / hex / decoded-value lines, opaque payloads dimmed.
  `--color` auto/always/never, `--width N`.

### Changed

- Faster scanning: the WAV and AIFF walkers no longer read the audio payload
  they never parse -- `inspect_wav` read up to 64 KB of the `data` chunk that
  `_parse_data` ignores, and `inspect_aiff` read 64 KB of `SSND` for an 8-byte
  header. Output is byte-for-byte identical; this drops an unnecessary read
  plus a transient allocation per file, which matters most on cold-cache scans
  of large libraries.

## [0.46.0] - 2026-07-11

### Added

- Declarative grammar engine, v1 walking skeleton (`core/grammar`): format
  descriptors as pure data plus one interpreter that emits the hand-written
  walkers' exact chunk/field model. Ships the Int/Enum type layer, a lenient
  `iff` container strategy with walker-equivalent traversal semantics (yields
  EOF-overrunning chunks with their declared size plus a warning, degrades
  instead of raising), and a WAV descriptor covering the `fmt ` chunk --
  proven byte-exact against `walk/wav` field-for-field across the 2,327-WAV
  corpus. Experimental and opt-in: nothing imports it on the `import acidcat`
  path, `walk_file` dispatch is unchanged, and the walkers remain the oracle
  and the default.

## [0.45.0] - 2026-07-11

### Added

- Public library API. `import acidcat` now exposes a stable, documented engine
  surface -- `walk`, `probe` (byte dissection), `viz` (entropy/Hilbert), `analyze`
  / `repair` (constraints), `anomalies_scan`, `Report`/`Violation`, `Unsupported`
  -- so consumers (the acidcat-playground, any dissection tool) import from the
  package root instead of reaching into `acidcat.core.*` internals. Importing the
  package pulls only the zero-optional-dependency core; tagging (mutagen), the TUI
  (textual), and librosa load only when their commands run. This is the engine
  boundary that makes the acidcat-as-library architecture solid.

## [0.44.0] - 2026-07-11

### Added

- `acidcat probe entropy` and `acidcat probe map`: byte-visualization dissection
  views (`core/viz.py`, zero-dependency terminal primitives). `entropy` plots a
  Shannon-entropy curve across the file plus a byte histogram, flagging spans that
  read as encrypted or compressed (>= 7.2 bits/byte). `map` draws a binvis-style
  Hilbert byte-class map -- adjacent cells are adjacent bytes, so headers, PCM,
  and appended/cavity regions show up as distinct blocks -- in truecolor on a TTY
  or byte-class glyphs otherwise. Both upstreamed from the playground's `viz.py`,
  since seeing a file's shape is a dissection capability.

## [0.43.0] - 2026-07-11

### Added

- `acidcat probe`: low-level byte dissection, the RE-tool surface. Read a file as
  raw bytes the way a reverse engineer does -- `read` (typed read at an offset,
  pwndbg `x`), `scan` (find every offset holding a value, in both byte orders,
  Cheat-Engine style), `find` (byte-pattern search), `strings`, `hexdump`, and
  `diff` (changed byte ranges between two files). The scalpel's edge over
  `xxd`+`grep`: an address can be a raw offset (`0x2c`) OR a structural name
  resolved through the walker -- a chunk id (`data`) or a chunk field
  (`fmt.sample_rate`) -- so you dissect by structure, not by counting bytes.
  `core/probe.py` holds the primitives; the `write`/`repair` edit path stays as
  the experimentation bench it was meant to be, with `probe` the read-only
  dissection face.

## [0.42.0] - 2026-07-11

### Added

- `acidcat audit` now surfaces MP3 truncation: it deep-walks an MP3 (only) so the
  Xing/VBRI declared frame count is cross-checked against the frames actually
  present. A big divergence -- the tell of a truncated or clipped MP3 -- appears
  in the FORENSICS section. The walker already had the check; audit now triggers
  it, thoroughly, without deep-walking other formats.

## [0.41.0] - 2026-07-11

### Changed

- Re-enabled `acidcat write` for Bitwig and Native Instruments presets. The
  editors (Bitwig meta splice, NI nksf/ksd/hsin, each with its own size cascade)
  were built and round-trip-tested but gated behind a "not enabled" refusal
  pending verification; that refusal is removed. The write routes through the
  same `writer.commit` sink (a `_original` backup, atomic write, read-back
  verify), and the format is labelled "(experimental)" with a caution to confirm
  the preset reloads in its app. Editing proprietary preset metadata is now
  possible from the CLI, not just readable.

## [0.40.0] - 2026-07-11

### Added

- COUNT-kind repair, the fourth and final derived-field kind. `acidcat repair`
  and `validate` now clamp a RIFF table-count that claims more records than the
  payload can physically hold -- WAV `cue ` (`num_cue_points`) and `smpl`
  (`num_sample_loops`) -- which a reader would otherwise trust and walk off the
  end of the chunk. The witness is payload capacity; only the over-capacity
  direction is repaired (a count smaller than capacity is left alone, since a
  chunk may carry trailing padding). Length-preserving; never touches audio.
- The constraint framework now runs **every** applicable repairer and aggregates,
  so a file with more than one kind of violation (a stale size and an
  over-capacity count) is fully repaired in one pass.

## [0.39.0] - 2026-07-11

### Added

- LAME tag detail in provenance: a LAME-encoded MP3 now reports its encode
  settings, not just the version -- e.g. "LAME 3.100 (VBR (mtrh), lowpass 20500
  Hz)" from the Xing/LAME tag the walker already decodes. The bare version string
  is no longer listed separately.
- ID3v2 encoder frames (`TSSE`, `TENC`) are now provenance tells, so an MP3 whose
  encoder wrote its name there (e.g. ffmpeg's `Lavf`) is identified.

## [0.38.0] - 2026-07-11

### Added

- Integrity widened to MP4/M4A: a duration-consistency check. The sample table
  (`stts`) sums to a media duration that must match the media header (`mdhd`); a
  mismatch means the header and the samples disagree -- a truncation or a re-mux
  that updated one and not the other. Pure timescale math, no codec knowledge,
  single-track. Surfaces in the `audit` INTEGRITY section. Verified: a healthy
  m4a is consistent; halving the `mdhd` duration is flagged (declared vs
  sample-table seconds).

## [0.37.0] - 2026-07-11

### Added

- Provenance depth: DAW/tool structural fingerprints and more converters. `audit`
  now identifies a writing tool from the signature chunks it leaves even when no
  encoder string is present -- Avid Pro Tools (`regn`/`minf`/`elm1`), Steinberg
  (`SMED`), the AFsp library, a SMPTE-UMID broadcast tool -- reported at "likely"
  (a structural tell, not a stamp). The encoder-string table gained the common
  rip/convert tools (Exact Audio Copy, dBpoweramp, XLD, foobar2000, fre:ac, SoX,
  GoldWave, Ocenaudio, TwistedWave, Serato, Traktor). Broadcast-Wave `bext`
  originator and CodingHistory already flow into provenance, so field-recorder
  and mastering chains are covered.

## [0.36.0] - 2026-07-11

### Added

- Integrity (effective bit depth / fake hi-res) now covers AIFF as well as WAV.
  AIFF is big-endian signed PCM read from the SSND chunk (past its offset and
  blockSize fields) with the declared depth from COMM; the same lowest-set-bit
  witness detects a 24-bit AIFF that is really upsampled 16-bit. AIFC is left
  alone (it may be compressed).

## [0.35.0] - 2026-07-11

### Added

- HIDDEN section in `acidcat audit`: concealed or appended data, split out from
  the general forensic findings and made actionable. Trailing blobs past the
  container, polyglots (an appended ZIP/PDF/PNG/etc), non-zero cavity content,
  FLAC APPLICATION data, and MP4 mdat coverage gaps are each reported with an
  exact `acidcat carve` command to extract the region. `audit` now gives the full
  four-part verdict -- STRUCTURE, INTEGRITY, HIDDEN, PROVENANCE -- with the rest
  of the anomaly findings under FORENSICS. `--json` splits `hidden` from
  `forensics`.

## [0.34.0] - 2026-07-11

### Added

- Integrity checks in `acidcat audit` (`core/integrity.py`): does the header match
  the audio. The anchor check is effective bit depth (fake hi-res): a file can
  declare 24-bit while every sample's low byte is zero, meaning it was upsampled
  from 16-bit. The witness is the PCM itself -- the lowest set bit across all
  samples tells how many bits actually carry data. Handles WAVE_FORMAT_EXTENSIBLE
  (resolves the real format tag from the sub-format GUID), skips float and
  compressed codecs and pure silence, and caps the read. A new INTEGRITY section
  in the audit report and `--json`.

## [0.33.0] - 2026-07-11

### Added

- Provenance fingerprinting in `acidcat audit` (`core/provenance.py`): identify
  the tool chain that wrote a file, with honest confidence levels that mirror the
  repair-witness discipline. Explicit version strings (FLAC/Ogg vendor, MP3
  LAME/encoder, WAV ISFT/software, bext originator, MP4 encoder) are canonicalized
  to a tool + version at "high" confidence; structural fingerprints (e.g.
  MuseScore's SF3 writer, revealed by Ogg-Vorbis samples and omitted RIFF padding)
  are reported at "likely" and never asserted as fact. A file can carry both -- an
  SF3 shows the original SoundFont editor from its string and MuseScore from its
  structure. `audit --json` includes the full signal list.

## [0.32.0] - 2026-07-11

### Added

- FLAC structural validate/repair/audit -- the third container grammar under the
  constraint model (a chain of metadata blocks, after the IFF tree and the MP4
  box). Two witnessed derived fields: the last-metadata-block flag (witnessed by
  the audio frame sync -- a flag set too early or missing is corrected) and
  PADDING body zero-fill (witnessed by the spec, the FLAC analog of the RIFF pad
  byte). Length-preserving; the audio frames are never touched. Fields that would
  need the audio decoded to witness them (STREAMINFO MD5/total-samples, seektable
  offsets) are out of scope -- acidcat bundles no FLAC decoder. `acidcat validate`
  now covers `.flac`, and `audit` reports its structure and encoder provenance.

## [0.31.0] - 2026-07-11

### Added

- `acidcat audit`: a read-only forensic verdict on a file, composing three views
  the constraint model and walkers already produce -- STRUCTURE (the derived-field
  violations `repair` would fix), FORENSICS (the anomaly detector's findings:
  polyglots, cavities, trailing data, high-entropy regions), and PROVENANCE (the
  writer/tool tells the file carries: encoder, software, vendor). `--json` for a
  machine-readable report.
- TUI validate/repair surface (`v`): the constraint model's face inside the
  interactive tool. `v` opens a panel listing every derived-field violation with
  the witness that makes it fixable; `r` applies the witnessed repairs to the
  working copy (unsaved until `ctrl+s`, original untouched). This completes the
  "surface it" phase: the model is now visible and usable interactively, not just
  from the CLI.

## [0.30.0] - 2026-07-11

### Added

- `acidcat validate`: read-only structural checking with an exit code. Runs the
  same analysis `repair` uses but writes nothing, over files or a whole directory
  tree, so it fits a CI check or a sweep to find broken files before they bite.
  Exit 0 when every checked file is consistent, 1 when any has a violation.
- The constraint framework (`core/constraints.py`, `core/repairers.py`): a
  `Violation` (a derived field disagreeing with its function) is now a first-class
  object carrying its witness and one of four kinds (size, offset, count, zero).
  Every verb is a move over violations -- `analyze` (read-only: validate) vs
  `apply` (fix: repair) -- and the IFF size cascade and MP4 offset rebuild are
  both expressed through the shared protocol, so the command layer is
  format-agnostic and adding a repairer wires it into both verbs at once.

### Changed

- `acidcat repair` now dispatches through the constraint framework; behavior is
  unchanged, and it reports a non-witnessed violation as "left as-is" rather than
  silently touching it.

## [0.29.0] - 2026-07-11

### Added

- `acidcat repair` extended beyond IFF to its first non-RIFF format and its
  first offset-kind fix: it rebuilds a broken MP4/M4A `stco`/`co64` chunk-offset
  table (the classic result of a re-mux or metadata insertion that moved `mdat`
  without patching the table, which a player hears as silence or a crash). The
  correct offsets are derived from `mdat`'s real position plus the sample sizes
  (`stsz`) and sample-to-chunk map (`stsc`) -- an independent witness, so it is a
  rebuild and not a guess. Conservative by design: single media track only, fires
  only when the stored table actually points outside `mdat`, and refuses unless
  the rebuilt table provably fits. The patch is length-preserving and never
  touches a byte of `mdat`. This is the second field kind (OFFSET) under the
  constraint model, the step that takes `repair` off the IFF grammar.

## [0.28.0] - 2026-07-11

### Added

- `acidcat repair`: fix structural inconsistencies in RIFF/WAVE, RF64, and
  AIFF/AIFC containers without touching a byte of audio. Recomputes stale
  container sizes (the common "riff_size says X, file is Y" left by a crash or
  a tool that appended without adjusting) and normalizes a non-zero pad byte,
  driven by a new generic IFF structural model (`core/structure.py`). Data
  appended past the container is preserved; the audio payload is compared
  before and after as a hard guard. Sits on the same `writer.commit` backup +
  atomic + read-back-verify sink as `write`. `--dry-run`, `-o`, `--keep-pad`.
- `core/structure.py`: an IFF container model whose bedrock invariant is
  byte-exact round-trip (validated on 2,358 real corpus files, zero false
  positives). This is the first piece of the declarative-structure direction:
  write and repair become one operation, re-satisfying the size cascade after
  a mutation.

## [0.27.0] - 2026-07-11

### Added

- SoundFont 3 (.sf3) support: MuseScore's Ogg-Vorbis-compressed soundfont.
  `acidcat inspect` maps every sample as a carveable Ogg stream (byte range in
  smpl), and `acidcat convert font.sf3` extracts each sample as a playable
  `.ogg` (decoding Vorbis to PCM needs a codec acidcat does not bundle). Same
  sfbk RIFF as SF2, with shdr start/end repurposed as byte offsets and
  sample-type bit 0x10 marking compression; the chunk walker now tolerates the
  MuseScore writer's omitted RIFF pad bytes.
- Tracker-module support: `acidcat inspect` maps ProTracker MOD, FastTracker
  II XM, and Impulse Tracker IT down to the byte offset of every embedded
  sample, so each sample is a carveable region (`carve --offset`). Header
  fields, pattern order, and per-sample descriptors are all decoded.
- Pointer (xref) annotation extended to three more pointer-table structures,
  all followable in the TUI with `x` and bounds-checked for dangling targets:
  - IT on-disk offset tables (instrument/sample/pattern pointers) and each
    IMPS sample header's SamplePointer, a two-level pointer chain.
  - MP4/ISO-BMFF `stco` / `co64` chunk-offset boxes; entries pointing past
    end-of-file (a re-muxed or truncated `mdat` tell) are counted and warned.
  - WAV `cue ` markers resolved from sample-frame index to a byte offset in
    the data chunk (`data_off + frame*block_align`) for uncompressed PCM.

## [0.26.0] - 2026-07-11

### Added

- SoundFont 2 (.sf2) support: `acidcat inspect font.sf2` shows the font
  metadata and every named sample (rate, duration, loop) with its real byte
  offset, and `acidcat convert font.sf2` extracts all samples to a folder of
  WAVs. Open, uncompressed sampler format -- the first SoundFont support.
- TUI byte map (`m`): where the file's bytes actually go, top-level regions
  biggest first with a proportional bar, unaccounted bytes called out.
- TUI pointer navigation (`x`): follow a pointer field to its target and flag
  a dangling (out-of-bounds) one. FLAC SEEKTABLE points are wired up, with an
  out-of-bounds seek offset also warned at inspect.
- TUI pending-changes diff (`d`): review every changed byte region (offset,
  old->new) between the working copy and the original before saving.
- FLAC: a metadata-like block after the last-metadata-block flag is flagged as
  data hidden past the block table; WAV: an implausible sample rate or channel
  count (structurally valid, physically impossible) is flagged.

### Changed

- TUI edits scale to large files: undo/redo store a minimal byte-range delta
  instead of a whole-file snapshot, and the dirty check no longer does a
  whole-file compare on every edit. App-global shortcuts are disabled while a
  modal is open.

### Fixed

- `inspect` on a VBR MP3 no longer walks every frame when the Xing/VBRI header
  already carries the frame count; the walk (and its cross-check) is a
  `--frames`/deep diagnostic now.

## [0.25.0] - 2026-07-10

### Added

- `acidcat carve` -- extract a structurally-identified byte range to a file or
  stdout. `--offset X [--length N | --end Y]` for an explicit range (any
  format), `--trailing` for the blob past the declared container end (the
  appended data a polyglot finding flags; RF64-sentinel aware), `--chunk ID`
  for a RIFF/AIFF chunk payload. Read-only on the source. The general
  extraction primitive behind sample and blob carving.
- `acidcat convert FILE.ncw` -- decode Native Instruments' NCW (Kontakt's
  lossless codec: DPCM + bit-truncation + mid/side) to WAV, and
  `acidcat convert DIR` to batch-convert a whole library (recursive,
  `--skip-existing`). NCW is compression, not access control -- no key,
  nothing bypassed. Verified against the public reference decoder and real
  Kontakt samples, with the bit-unpacking proven invertible and ground-truth
  round-trip tests for every mode.
- TUI navigation: `g` goto-offset, `/` search (fzf-style over field
  names/values, or `0x..`/`"ascii"` raw-byte search), `n`/`N` to cycle,
  `f` jump-to-forensics-finding, `y` yank hex to clipboard, `ctrl+r` redo.
  The forensics panel is now numbered, has a severity legend, and scrolls
  (findings past the eighth were previously unreachable).

### Fixed

- Forensics: RF64/BW64 files silently skipped the trailing-data and
  appended-magic scans -- the `0xFFFFFFFF` sentinel size made the container
  end compute as ~4.29 GB. The true end is now resolved from the `ds64`
  chunk, so a PDF/PNG/ELF appended to an RF64 is detected.
- TUI: pressing edit on an MP3 bitrate/sample_rate field crashed the app
  (a missing import); a malformed file crashed the session on open (only
  one exception type was caught); the stale-source save prompt named the
  wrong key (`s` strips, the force-save is `ctrl+s`).

### Changed

- New forensic tells, each calibrated to zero false positives across the
  local corpus: non-zero odd-chunk pad bytes (a covert channel), duplicate
  structural chunks, APEv2 tags on non-MP3 files, and byte-entropy
  characterization of cavities ("entropy X.X/8, encrypted or compressed
  payload") so a hidden ciphertext blob reads differently from benign
  metadata.
- `info` and `scan` now decode through the inspect walkers like `index`
  already did, completing the one-decoder-per-format unification; the dead
  legacy wrapper functions and imports were removed.

## [0.24.0] - 2026-07-10

### Changed

- Library indexing now decodes each file once. The scan-row extraction for
  WAV, AIFF, MIDI, and Serum is driven by the inspect walkers (via a shared
  `ctx` dict) instead of a second parser re-reading the same bytes, ending
  the double-maintenance that let the two paths drift (the `smpl` signedness
  bug in 0.22.0 had to be fixed in both). Verified row-identical to the
  previous extractor across the local corpus (2,328 WAV, 270 MIDI, 85 Serum,
  4 AIFF). Tagged audio (mp3/flac/ogg/m4a) intentionally stays on mutagen,
  which owns the on-disk tag spec.
- MIDI key signatures resolve through one shared `key_signature_name`
  helper, so the inspector and the library index can no longer disagree on a
  key: the structural view now shows the real key name ("D", "Bm") where it
  previously showed the raw signature ("+2 sharps").

### Fixed

- A MIDI file with no tempo event no longer stores a duration derived from
  the assumed 120 bpm default in the library index (it would be a wrong
  number to filter on); the inspector still shows the estimate, clearly
  labeled.

## [0.23.0] - 2026-07-10

### Added

- MP4 `stsd` descent: sample entries and their codec-config boxes join the
  box tree. The esds descriptor chain decodes down to the AAC
  AudioSpecificConfig (object type, frequency index including the 24-bit
  escape, channel configuration, SBR/PS extension rate), so the codec line
  names the exact profile ("AAC LC", "SBR (HE-AAC)") instead of "AAC".
  ALAC magic cookies and dOps decode fully; QuickTime `wave` wrappers are
  flattened; freeform `----` atoms (Serato, MusicBrainz, iTunNORM) surface
  as namespace:name tags (#56).
- WAV `fmt ` extension decode per format tag: MS ADPCM samples-per-block
  and predictor coefficients (the standard 7-pair set recognized), IMA
  ADPCM samples-per-block, MPEGLAYER3WAVEFORMAT fields; cue points show
  play order and compressed-data chunk/block starts (#56).
- MIDI wall-clock duration on the MThd chunk, tempo-independent for SMPTE
  division and honestly annotated for PPQ (approximation on tempo changes,
  the SMF-default-120 case called out) (#56).
- FLAC SEEKTABLE points listed (sample @ +offset, frame samples), with
  placeholders counted; AIFF COMT timestamps rendered from the 1904 Mac
  epoch and FVER decoded; MP3 VBRI encoder delay, quality, and seek-TOC
  geometry (#56).
- Free-format MP3 (bitrate index 0): the constant frame length is measured
  from sync spacing, the derived bitrate reported, and a lone free-format
  sync without a matching twin is treated as a false sync. New fixture
  specimen at ~91.9 kbps, a rate no table entry can express (#58).

### Changed

- The field value/bytes codec engine moved from the TUI into
  `core/fieldcodec.py` with no behavior change; it no longer requires the
  `[tui]` extra, and the codec test suite runs on a bare install (#57).

### Fixed

- Capped file reads are clamped to the file size: `read(N)` pre-allocates
  the full N-byte buffer, so the 256 MB MIDI read cap cost ~50 ms per file
  regardless of size. Library scans are ~19x faster (measured 17.7 s to
  0.91 s over a 2,615-file corpus) (#59).

## [0.22.0] - 2026-07-10

### Fixed

- A crafted `.nksf` MessagePack header claiming ~4 billion array/map elements
  could hang `inspect` and unattended `index` scans and exhaust memory; forged
  counts are now rejected and degrade to the normal warning path (#53).
- ID3v2 COMM/USLT (and v2.2 COM/ULT) frames decode to their text instead of a
  byte count; v2.3/v2.4 per-frame format flags are honored, so group ids,
  data-length indicators, and per-frame unsynchronisation no longer corrupt
  the payload decode, and compressed/encrypted frames are labeled rather than
  rendered as garbage; numeric TCON genre references resolve against the
  ID3v1 table (#54).
- MP4 `gnre` genre atoms (how older iTunes stored genre) resolve to the genre
  name instead of raw bytes; QuickTime version-2 audio sample entries report
  their real channel count and sample rate instead of v0-offset constants (#54).
- Ogg Opus durations subtract the pre-skip priming samples and report the
  48 kHz decode rate (the encoder input rate is shown separately); duration
  is scoped to the first logical bitstream, and chained/muxed files warn (#54).
- WAV `smpl` SMPTE fields and `inst` base note are read unsigned per spec (#54).
- Writes are read back from disk and verified before "saved" is reported; a
  commit failure (locked file, full disk) prints a per-file error instead of
  a traceback; a pre-existing `_original` file is reported as "existing
  backup kept" rather than passing silently as a fresh backup (#53).
- The TUI refuses to save over a source file that changed on disk since it
  was opened (press save again to force), so external edits are never
  silently clobbered and the first-save backup always captures the bytes
  that were actually being edited (#53).
- Tag edits and strips on MP3/FLAC/OGG/Opus/M4A (including cover-art
  changes) now verify the audio payload survived the rewrite, matching the
  guarantee WAV/AIFF edits already had (#55).
- A file truncated inside the MIDI MThd header degrades to a warning instead
  of a parse error (#53).

## [0.21.0] - 2026-07-10

### Added

- `acidcat tui` (new `[tui]` extra): an interactive terminal inspector and
  byte-level metadata editor built on textual, imported lazily so the core
  stays dependency-light. Chunk/field/row tree with a hex pane and forensics
  panel, a file browser, a metadata form, and a layered field editor:
  variable-length text fields route through the write engine (so lengths can
  change); numeric fields value-edit via a verified encoding (struct formats,
  ID3 synchsafe, AIFF 80-bit float, u24be, bit-packed fields, enum bit-fields
  editable by name, and context-dependent enums such as MP3 bitrate and
  sample rate, whose value tables depend on the version/layer bits); anything
  else hex-edits in place. Every walker-declared encoding is trusted only
  after it re-encodes to the field's actual on-disk bytes, so a wrong
  annotation can never write blind. All edits apply to a temp working copy;
  nothing touches the original until ctrl+s, which makes a pristine
  `_original` backup. Undo, unsaved-changes prompts, a help overlay, and
  per-field editability hints. The cursor and expansion state survive the
  tree rebuild after each edit.
- `write --strip` (and the TUI `s` key): remove identifying metadata (WAV
  LIST/bext/iXML/cart/ID3/XMP, AIFF NAME/AUTH/ANNO/copyright/ID3/APPL, all
  tags on MP3/FLAC/OGG/M4A, Vital author/comments) while preserving the audio
  byte-for-byte, verified after the rewrite.
- Walker coverage pass: formatted and bit-packed fields across WAV, MP3,
  AIFF, FLAC, MIDI, RX2, and FXP carry verified encoding annotations;
  composite fields (MP3 gapless and replay_gain, AIFF AESD channel status,
  WAV smpl loops) split into editable subfields; FLAC STREAMINFO is fully
  value-editable via a read-modify-write that preserves neighbouring
  bit-fields.

### Changed

- MP3 inspect output decodes more of the frame header in place: `version`
  and `layer` are their own fields, `crc_protected` reports
  `protected`/`unprotected` instead of a boolean, and
  `bitrate`/`sample_rate`/`channel_mode`/`emphasis` carry the header word's
  real offset and length instead of null. New fields: LAME
  `replay_gain_type`/`replay_gain_sign`/`replay_gain_mag`,
  `encoder_delay`/`encoder_padding`, AIFF `aes_*` status subfields, and WAV
  `loop[N]_type`. Consumers parsing inspect JSON for MP3 will see the new
  shape.

### Fixed

- inspect JSON (plain and `--full`) no longer leaks the editor-only
  `enc`/`raw` field keys; the field-level `raw` also collided with the
  chunk-level `raw` hex bytes that `--full` emits.

## [0.20.0] - 2026-07-08

### Changed

- Per-library schema bumped to v3. `samples` gains an explicit `id INTEGER
  PRIMARY KEY` (a VACUUM-stable rowid alias) and the `samples_fts` mirror is
  re-keyed to it. The per-path FTS refresh now deletes by rowid (one index
  lookup) instead of `DELETE ... WHERE path = ?`, which scanned the whole FTS
  index for a matching column, making a `--force` full rebuild O(n^2). Existing
  v1/v2 DBs migrate in place on first open, inside the single atomic
  transaction from 0.19.1; an interruption rolls back to the prior version.
  Benchmarked on a 32k-row library: full FTS rebuild dropped from minutes to
  under a second; migration is a one-time ~1s pass.
- `find_similar` similarity is now meaningful, not a near-1.0 cluster. It scores
  a fixed timbral/rhythmic vector (core/features.py `FEATURE_KEYS`) and
  **z-standardizes each dimension across the candidate population** before the
  cosine, so the small-magnitude timbral dims are no longer buried by the
  10^3-10^6 spectral ones. The vector excludes non-sonic scale fields
  (sample_rate, audio_length_samples, duration, beat_count).
- Feature vectors are stored as a packed float32 BLOB (`features.feature_vec`,
  schema v3) so `find_similar` unpacks them directly instead of JSON-parsing
  every candidate. Scoring is numpy-vectorized when the analysis extra is
  present and falls back to an identical pure-Python path otherwise, so scoring
  a shared index needs no numpy. Existing feature rows are backfilled from their
  stored JSON during the v3 migration (no librosa re-extraction).

## [0.19.1] - 2026-07-07

### Added

- Interactive anatomy datasheets for the newer formats: Bitwig wavetable (`.wt`),
  Bitwig multisample, VST FXP, ReCycle RX2, and RMID
  (`docs/formats/*-anatomy.html`).

### Changed

- Anatomy datasheets: the colour legend moved from prose ("green is a value,
  clay is a flag, ...") into the panel beside the lede as a visual key, a
  dark/light box pair per field kind (the selected and resting appearance)
  naming the colour and its meaning, showing only the kinds each page uses. The
  five new pages carry distinct per-format accents.

## [0.19.0] - 2026-07-07

### Added

- `query --compatible-with FILE`: find samples that mix with a reference
  (harmonic key via the Camelot wheel + compatible tempo including
  half/double-time) from the CLI, matching the MCP `find_compatible` tool.
  Reads the reference's key/BPM/kind from the index or by parsing the file.
- `inspect` walks Bitwig wavetable `.wt` files (the `vawt` container Bitwig
  writes from Polymer and other wavetable devices): frame count, samples per
  single-cycle wave, and the 16-bit sample block. Reverse-engineered and
  documented in docs/formats/bitwig-wt.md.
- Format reference docs (docs/formats/) for the previously-undocumented native
  walkers: RF64/BW64, RMID, VST FXP, and ReCycle RX2.
- `inspect` walks Bitwig `.multisample` files (a ZIP with a `multisample.xml`
  zone map plus member samples): per-zone file, root note, key/velocity range,
  and loop. Content-sniffed by peeking the zip for `multisample.xml`. Reads
  entries by seeking past the local header, since Bitwig writes a mismatched
  CRC. stdlib only (zipfile + xml.etree), no new dependency. See
  docs/formats/bitwig-multisample.md.

### Changed

- MCP server: a process-lifetime read-connection cache (keyed by db_path,
  opened `check_same_thread=False`, every use serialized under a lock)
  replaces re-opening every library DB on each tool call. A warm fan-out
  query dropped from ~20ms to ~2ms across 18 libraries. Scoped queries now
  open only the in-scope libraries (was: open all, discard the rest). WAL
  means cached readers see committed writes; the cache is evicted on
  register/forget/reindex and revalidated on borrow. Thread-safe if tool
  dispatch ever moves off the event-loop thread.
- The filter SQL (bpm/duration/key/format/device/category/creator/product/
  tags/text) is now built once in `core/query_sql.py`, shared by the CLI
  `query` and the MCP `search_samples` tool instead of two drifting copies.
- The compatible-sample engine (`find_compatible` + `infer_kind`) moved into
  `core/search.py`, shared by the MCP tool and the new CLI command. The MCP
  `find_compatible` gained half/double-time matching (`half_double`) and a
  per-result compatibility note; keyless references now match only keyless
  samples on both surfaces. Key matching is spelling-robust (normalized via
  camelot), so 'A minor' and 'Am' match.
- Index/DB tuning: the case-insensitive filters (`key`/`format`/`device`/
  `category`/`creator`/`product`) now hit `LOWER()`-expression indexes instead
  of a full scan (a per-filter lookup on a 32k-row library dropped from ~5.5ms
  to ~0.02ms); read connections are tuned (`synchronous=NORMAL` under WAL,
  larger page cache + mmap + in-memory temp store); `PRAGMA optimize` runs after
  a walk so index choices are stats-driven. Additive and idempotent, no
  schema-version bump; existing libraries pick the indexes up on the next index.
- MCP: tool-execution failures now return a `CallToolResult` with `isError: true`
  (and a handler that returns an `{"error": ...}` dict is flagged the same way),
  so clients and the model see errors as errors, not as a successful payload that
  happens to contain an error string.
- MCP: successful tool calls now return `structuredContent` (the machine-readable
  result object) alongside the JSON text block, and every tool advertises a
  human-readable `title`. Previously-bare input fields gained descriptions.

### Fixed

- Index schema migration is now atomic and re-entry-safe: the whole step runs
  in one transaction that rolls back on error, and each `ADD COLUMN` is guarded
  against a pre-existing column, so a migration interrupted midway can no longer
  wedge a DB with `duplicate column name` on the next open.

## [0.18.0] - 2026-07-06

### Added

- `inspect` walks RMID (RIFF-wrapped MIDI): reports the RIFF wrapper and hands
  the inner Standard MIDI File to the MIDI walker (offsets shifted into place),
  so the MThd/MTrk detail shows through.
- `inspect` decodes the WAV `cart` chunk (AES46 radio automation: title, artist,
  cut id, category, start/end, producer app, level reference, post-timers, url)
  and the `iXML` chunk (field-recorder metadata: project, scene, take, tape,
  note, track count), previously shown as unparsed.

## [0.17.0] - 2026-07-04

### Added

- `inspect` walks VST2 `.fxp` presets: the `CcnK` container, its preset kind
  (`FxCk`/`FPCh`), the plugin id (a FourCC, e.g. `XfsX` = Serum), version fields,
  the preset name, and the opaque plugin chunk as a region.
- `inspect` walks Propellerhead ReCycle `.rx2` loops: the `CAT`/`REX2` IFF
  chunk tree, the creator string, and the slice count (recursing into the
  nested slice-list group).

### Changed

- Internal: the library-indexing engine and the discovery helpers moved from
  `commands/index.py` into a new `core/indexing.py`; `mcp_server.py` no longer
  imports any command internals (core never imports commands). Pure relocation,
  no behavior change.

## [0.16.0] - 2026-07-04

### Added

- `inspect --anomalies` flags an Ogg file carrying more than one logical
  bitstream (multiple BOS serials), several codecs multiplexed into one file,
  where a single-codec player surfaces only one and the others ride along hidden.
- `inspect` decodes the manufacturer id of MIDI SysEx events, and `--anomalies`
  warns when a SysEx uses the non-commercial id 0x7D (no synth acts on it) or
  carries an oversized payload, a MIDI payload-cavity tell.
- `inspect --anomalies` flags non-zero content in a RIFF JUNK/PAD chunk (spec'd
  as ignorable padding, and the RF64/BW64 ds64 placeholder), a WAV cavity.
- `inspect --anomalies` flags an MP4/M4A `mdat` coverage gap: bytes inside `mdat`
  that no `stsz` sample references (a payload grown onto the box's tail while the
  sample tables still validate), a container cavity most tools miss.
- `inspect --anomalies` flags non-zero bytes in an ID3v2 tag's padding region
  (after the last frame, within the declared tag size), a cavity, not trailing data.
- `inspect --anomalies` flags dual-endianness 16-bit PCM: audio engineered so
  both the little- and big-endian readings are structured (a WAV/AIFF twin that
  plays a different sound each way). Real audio is structured only one way.

### Fixed

- Ogg files now report `duration`, computed from the last page's granule
  position (Opus granules are counted at 48 kHz). Was previously absent.
- Every ID3 `T***` text frame now decodes to its value; frames outside a
  hardcoded set (e.g. `TPE2` album artist, `TCOM` composer) previously showed
  as a raw byte count. All `T***` frames share the same text structure per spec.
- MP3 duration is now the gapless/playable length: the LAME encoder delay and
  padding are subtracted (was ~48 ms long): the standard
  encoder-delay-adjusted sample count, matching ffprobe.
- MP4/M4A `trkn` and `disk` atoms decode to `index/total` (or `index`) instead
  of a raw byte count.

### Changed

- Docs: describe acidcat on its own terms (dropped the tool comparisons from
  the README tagline, package description, and command help); refreshed a tight,
  current SECURITY.md.

## [0.15.0] - 2026-07-03

### Added

- `acidcat explore FILE [-o out.html]` builds the standalone interactive HTML
  byte-explorer as a first-class command. The explorer (previously the
  unpackaged repo-root `build_explorer.py`) now ships inside the package; the
  root script stays as a back-compat shim for the `inspect --full | ...` pipe.

### Fixed

- MessagePack codec in `core/ni.py` (reads/writes `.nksf`): the decoder now
  handles the full int / uint / float / bin family and map32/array32, a real
  `.nksf` with any integer field above 127 previously failed to read. The
  encoder emits correct signed and 64-bit ints, floats, and str32 (it had
  wrapped negatives to unsigned and truncated large values). Adds test_ni.py
  covering the codec, FastLZ, and the hsin walker.

- The "unsupported file" error and `inspect` help now list Ogg and Native
  Instruments (they were supported but omitted from the message).

### Changed

- Internal: the thirteen format walkers moved out of `commands/inspect.py`
  (2,878 lines) into `core/walk/*` behind a registry, with a canonical format
  sniffer in `core/sniff.py`; `commands/inspect.py` (~400 lines) now holds only
  rendering, selection, and the CLI. Adding a format is one magic + one walker +
  one registry entry. No behavior change: byte-for-byte identical `inspect`
  output (verified across formats and output modes), and the suite stayed green
  at every step.

## [0.14.1] - 2026-07-03

### Added

- `inspect --anomalies` detects an appended ZIP on ANY format via a universal
  end-of-central-directory scan near EOF, not just containers with a total-size
  header. Catches mp3/flac/ogg polyglots the size-based trailing check missed.

### Fixed

- `inspect --anomalies` no longer raises a false "possible LSB-stego" alert on
  ordinary recordings. A uniformly high low-bit-entropy floor is consistent with
  an embedded payload but equally with a mic/preamp noise floor, dither, or a
  high-bit-depth capture (real TASCAM field recordings tripped it). It is now a
  NOTICE describing the entropy, not an alert claiming stego; entropy alone
  cannot separate the cases (a sample-pair/chi-square test is the future path).

## [0.14.0] - 2026-07-03

### Added

- `inspect --anomalies`: a forensic scan that flags trailing data past the
  declared container end, appended-format magic (polyglot detection: ZIP/PDF/
  PNG/... after the audio), structural size mismatches (surfaced from the
  walker), and control bytes smuggled into text fields. Findings carry a
  severity, byte offset, and rule; also emitted in `-f json`. Also flags duplicate ID3 frames, non-zero content in spec-ignorable padding/free regions, and FLAC APPLICATION blocks.
- `cover` command: extract, embed, or remove embedded cover art across MP3,
  FLAC, MP4/M4A, and Ogg (`acidcat cover FILE -o art.jpg`, `--set art.png`,
  `--remove`); embed/remove are atomic with a `_original` backup.
- Custom ID3 frames in `write`: `--set txxx:NAME=value` (and `wxxx:NAME=url`)
  set user-defined frames; on FLAC/Ogg the name becomes a Vorbis comment, on
  M4A a freeform atom. `inspect` decodes TXXX/WXXX as `description = value`.
- LSB-steganography detection: `--anomalies` computes the per-window entropy of
  the low bit-plane of PCM WAV samples and flags a uniform-high floor (the tell
  of an encrypted hidden payload; natural audio dips low in quiet passages).
  `inspect --full` emits the entropy map and `build_explorer.py` renders it as a
  color heat-map in the byte explorer.

### Fixed

- MP4/M4A: a large `mdat` (or any box) whose contents extend past the inspector's
  read window was wrongly flagged as overrunning its parent. Box sizes are now
  reconciled against the real file size, so a valid large box reads as "content
  beyond read window", not an error. (Found by `--anomalies` on a real ALAC file.)

## [0.13.0] - 2026-07-03

### Added

- MCP: a **streamable-HTTP transport** (`acidcat-mcp --transport http`, mounted at
  /mcp, stateless), the modern replacement for SSE; stdio stays the default. New
  `mcp-http` extra (starlette + uvicorn).
- A distributable **Claude skill** under `skills/acidcat/` (copy to
  `~/.claude/skills`) covering inspect/write/convert/index/query, build_explorer,
  and the MCP server.
- Ogg: `inspect` now decodes the identification header, reporting `sample_rate`
  and `channels` (previously only the comment header was read).

### Fixed

- `info` on a Bitwig/NI/Vital preset silently parsed it as a headerless WAV; it
  now detects presets and points to `acidcat inspect`.
- build_explorer: the hover highlight holds briefly so dragging across the gaps
  between byte cells reads as continuous; the dark/light toggle applies on load
  without pinning the OS preference in localStorage.
- Docs: README overview + Supported Formats table now list Bitwig/NI/Vital/NCW/
  MP4; CHEATSHEET documents `--verbose` and the `mcp-http` extra. Removed an
  unreachable code branch in `write`.

## [0.12.0] - 2026-07-03

### Added

- Native Instruments preset support (`inspect`): the NISound `hsin` container
  (Massive `.nmsv`, Absynth `.nabs`, FM8, Reaktor, modern Kontakt `.nki`), the
  older `.ksd` (KORE/Absynth, zlib+XML), and `.nksf` (NKS, RIFF+MessagePack).
  Reads product, name, author, vendor, category, tags; `--verbose` FastLZ-
  decompresses the hsin subtree. Pure Python, from byte-level facts only.
- Ogg (`inspect`): page structure + the Vorbis/Opus comment header (vendor,
  tags), bounds-checked.
- Bitwig deep deconstruction (`inspect --verbose`): the device/module tree, the
  named parameter table with values, the Grid wiring paths, the reference graph,
  and the embedded-asset zip unzipped with each file identified. `.bwclip` note
  clips report bpm + beat length and read every note (pitch / position /
  duration / velocity), reverse-engineered via a known-plaintext attack.
- Vital deep deconstruction: oscillators + wavetables, LFO inventory, effects
  chain, and the modulation matrix (source -> destination with amounts).
- AIFF: decode the embedded ID3v2 chunk (as bandcamp and some tools write it).
- `convert` command: export a DAW clip's notes to a Standard MIDI File
  (`acidcat convert clip.bwclip -o out.mid`).
- `write` command: edit metadata in place (exiftool-style) after a `_original`
  backup, or a `-o` copy; `--dry-run` and batch. Covers WAV (INFO tags, acid
  bpm/key, bext, smpl root), AIFF (NAME/AUTH/ANNO), MP3/FLAC/OGG/M4A (via
  mutagen), and Vital presets. Atomic writes; refuses RF64/malformed; verifies
  audio is byte-identical after a WAV rewrite. (Bitwig/NI preset writing is
  implemented but held as experimental pending in-app reload verification.)
- Index / query / MCP: synth/DAW preset metadata (device, product, creator,
  category, tags) is indexed and searchable (`query --device/--category/
  --creator/--product`, full-text, and the MCP `search_samples` tool). Schema v2
  with a safe v1 -> v2 migration.
- `inspect --pretty` (human-friendly metadata view) and `--verbose` (deep
  deconstruction). CHEATSHEET.md.

### Changed

- MIDI note names now use the DAW octave convention (middle C = C3), matching
  Bitwig / Ableton / FL / Cubase / Logic (previously scientific, C4).

### Fixed

- inspect decoded WAV/AIFF text metadata (INFO tags, NAME/AUTH/ANNO, comments,
  MIDI text events) as ASCII with errors='replace', mangling every non-Latin tag
  (Korean, CJK, mixed-script) into U+FFFD. Now decodes UTF-8 with a latin-1
  fallback, so non-ASCII metadata displays correctly.
- Hardening from two adversarial pre-release reviews of the new code: bounded the
  Bitwig note/parameter/path/wiring scanners and the NI .ksd / FastLZ-subtree
  scanners (a crafted preset could force quadratic or multi-second scans); capped
  the embedded-zip asset reader to a per-entry prefix (zip-bomb memory guard);
  guarded the note reader against NaN/Inf fields and an out-of-range pitch footer;
  added a recursion-depth limit to the hsin walker; gave `convert` proper error
  handling. Write path: RIFF/AIFF no longer fold trailing bytes into the container
  size, and the tagged-file editor fsyncs its temp file before re-reading it.

## [0.11.0] - 2026-07-02

### Added

- New format walkers for `inspect`, all pure-Python and bounds/DoS-hardened:
  - MP4/M4A (ISO-BMFF): walks the box tree (bounds- and depth-checked) and
    decodes ftyp brands, movie duration, the audio codec (AAC / Apple Lossless /
    Opus / ...) with channels and rate from stsd, and the iTunes metadata under
    udta > meta > ilst (title, artist, album, bpm, cover-art, ...). moov is
    found even when it sits at the end of a non-faststart file.
  - Bitwig `.bwpreset` / `.bwclip`: the BtWg tagged meta block (device, creator,
    category, tags, description, version) plus a note for any embedded-asset zip.
  - Vital `.vital`: the bare-JSON preset metadata (preset_name, author, comments,
    style, synth_version, macros).
  - NI Compressed Wave `.ncw` (Kontakt samples): header audio parameters
    (channels, bits, sample rate, sample count, duration).
- Bitwig WAV bounces: the `BWBM` beat-map chunk (beats, duration, derived bpm)
  and the `IBPM` tempo tag are decoded.
- `inspect --pretty`: a human-friendly view of the decoded tags and metadata
  (no byte offsets), for presets and tagged files.

## [0.10.1] - 2026-07-02

### Fixed

- The CLI forces UTF-8 output. acidcat printed decoded tags with the platform
  default stdout encoding, so on Windows or any non-UTF-8 locale (or a
  redirected pipe) a file with non-Latin metadata (a Korean artist tag, for
  example) raised an uncaught UnicodeEncodeError. stdout and stderr are now
  reconfigured to UTF-8 with errors=replace at CLI entry.

## [0.10.0] - 2026-07-02

### Added

- `inspect` accepts multiple files. With more than one, each is printed under a
  readelf-style `File:` banner and JSON output becomes NDJSON (one record per
  line). A missing or undecodable file is reported to stderr and skipped, and
  the exit code reflects any failure. A broken downstream pipe exits quietly.
- `inspect --only` / `--exclude` select or drop chunks by id (comma-separated,
  case-insensitive). Composing `--only NAME --hex` gives a focused hexdump.
- `inspect --full` emits a self-contained structural dump (implies JSON): each
  chunk with its raw region bytes and every field's absolute byte offset.
- `build_explorer.py`, a standalone script that renders a `--full` dump to a
  self-contained interactive HTML byte explorer (a hex grid with the decoded
  fields tinted over the bytes).
- Native decode of many previously-opaque structures: WAV `fmt ` extensible
  (sub-format GUID, `channel_mask` speaker names, `cbSize`) and `bext` v1/v2
  (UMID, EBU R128 loudness, coding history); the RF64 `ds64` size-override
  table; FLAC CUESHEET; AIFF COMT/AESD/APPL; MP3 ID3v2.2 frames, VBRI headers,
  LAME replay-gain and bitrate, and full ID3v1.1 with the standard genre table;
  MIDI SMPTE-offset meta events.

### Fixed

- `inspect --hex` read the wrong bytes for FLAC, MP3, and Serum, which do not
  share the RIFF 8-byte chunk-header layout. Each chunk now carries a payload
  base, so `--hex` and the `--full` byte ranges are correct across every format.
- ID3v2 unsynchronisation is de-escaped for v2.2/v2.3 before frame sizes are
  read (v2.4 per-frame unsync is left intact); the extended header is skipped
  rather than misread as the first frame.
- The Xing/Info side-info offset accounts for the two CRC bytes present on a
  CRC-protected MPEG frame.

### Changed

- Unrecognized arguments print the chosen subcommand's usage rather than the
  top-level usage.

## [0.9.7] - 2026-07-02

### Fixed

- `inspect` no longer false-warns `frame 'APIC' size N overruns tag` on MP3s
  whose ID3v2 tag carries embedded cover art. The frame-overrun check compared
  each frame against the 64 KB read buffer rather than the tag's declared size,
  so any tag with art larger than 64 KB tripped the warning and stopped
  enumerating frames early. The tag is now read up to a 16 MB cap and overrun
  is tested against the declared tag size; a frame that genuinely exceeds the
  tag still warns.
- `inspect --color` renders de-emphasized text (offsets, notes, table headers)
  as bright-black instead of the faint attribute. Terminals implement faint by
  blending the foreground toward the background, which turned muddy on any
  non-black terminal background; bright-black is a palette slot the theme
  defines, so it stays legible everywhere.

## [0.9.6] - 2026-07-02

### Fixed

- `inspect` no longer crashes on three malformed inputs that reached a read
  past a buffer: a truncated MP3 Xing header (uncaught `struct.error`), a
  deeply nested Serum preset (uncaught `RecursionError`), and an `MThd` shorter
  than 6 bytes under `--hex`. Each now degrades to a warning.
- Key detection corrected. A minor MIDI key signature now names the relative
  minor (an A-minor signature reports `Am`, not `Cm`), and filename key parsing
  accepts flats (`Eb minor` becomes `D#m`) and the capital-M major marker
  (`F#M` becomes `F#`). This flows into `info`, the index, and Camelot matching.
- RF64 duration: the `fact` chunk's `0xFFFFFFFF` sentinel is resolved through
  the `ds64` 64-bit sample count instead of being taken literally (which
  reported durations of tens of thousands of seconds).
- MP3 frames carrying a LAME `Info` tag are labeled CBR, not VBR; only a `Xing`
  tag denotes VBR.
- `acid` chunks padded beyond 24 bytes are decoded instead of silently dropping
  BPM and beats.
- `info` renders a SMPTE MIDI division as frames-per-second and ticks-per-frame
  rather than a meaningless "ticks/beat".
- AIFF sample rates whose 80-bit extended value is non-finite are treated as
  unset instead of degrading the COMM chunk to a parse error.

### Changed

- Format sniffing in `index` recognizes all MPEG audio layers and versions (it
  previously matched Layer III only), reusing the frame-header validator that
  also rejects ADTS AAC.
- Hardening: `inspect` lints an RF64 `ds64` data size larger than the file,
  validates FLAC PICTURE string lengths before slicing, and caps the MIDI
  whole-file read at 256 MB.

## [0.9.5] - 2026-07-01

### Fixed

- `inspect` no longer dispatches an ID3-wrapped non-MP3 container as MP3. A file
  that opens with an ID3v2 tag is treated as MP3 only when the tag does not wrap
  a RIFF/AIFF/FLAC/MIDI container; otherwise it is cleanly rejected instead of
  emitting bogus "no MPEG frame" warnings.
- `inspect` flags an AIFF COMM `num_sample_frames` that implies more audio than
  the file holds (its duration is then untrustworthy), gated to uncompressed so
  AIFC packet counts are not false-flagged.
- `inspect` flags an SSND `offset` that exceeds the chunk payload, which
  previously degraded silently to a reported 0 bytes.

### Changed

- SECURITY.md documents that acidcat performs no eval/exec/deserialization/
  subprocess on parsed content, closing the metadata-reader code-injection class
  (e.g. CVE-2021-22204 in another tool).
- Format-anatomy pages normalized to a single background palette.

## [0.9.4] - 2026-06-29

### Fixed

- `inspect` derives WAV duration from the `fact` chunk's sample count for
  non-PCM audio instead of `bytes / block_align`. ADPCM packs many samples
  per block, so a data chunk previously reported ~0.000 s; it now reports the
  true duration. The PCM and overrun paths are unchanged.
- `inspect` labels AIFC compressed duration as approximate. `num_sample_frames`
  counts packets, not sample frames, for compressed codecs (e.g. ima4), so a
  `frames / rate` figure is only a lower bound; it now shows `~N s (approx)`
  with a warning. Uncompressed AIFC (NONE/sowt/twos/float) stays exact.
- `inspect` sanity-checks the MP3 Xing/VBRI `frame_count` against the frames
  actually walked and warns on a wild divergence; a bogus VBR count otherwise
  yields a wrong duration silently.

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
