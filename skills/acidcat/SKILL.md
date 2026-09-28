---
name: acidcat
description: >
  Inspect, edit, check and search the byte structure and metadata of audio
  files (WAV, AIFF, MP3, FLAC, OGG, M4A, MIDI and 80-odd more), sample banks,
  tracker modules and synth/DAW presets (Bitwig, Native Instruments, Vital,
  Serum, VST FXP, ReCycle RX2). Use when the user wants a file's structure
  byte by byte, to read or change a field, tag, loop point, BPM, key or root
  note, to check or repair a container, a forensic verdict, to find and cut
  audio out of a blob or disk image, to build an HTML byte explorer, to
  export a DAW clip to MIDI, or to index and search a sample library.
---

# acidcat

acidcat is a pure-Python tool (one dependency, mutagen; the structural walkers
need nothing): readelf for audio files and presets. It walks a file into a
tree of nodes and fields, each with an address and the exact bytes it was
read from, and treats every file as hostile input (bounded parsers that
degrade to a finding rather than crash).

Install: `pip install acidcat` (core). Extras: `[mcp]` (stdio MCP server),
`[mcp-http]` (streamable-HTTP MCP server), `[analysis]` (librosa: `analyze`,
`lib similar`, and the MCP tools that need feature vectors), `[tui]` (the
interactive inspector), `[crypto]` (encrypted disc images), `[all]`.

acidcat 2.0 has seventeen verbs. A 1.8 spelling (`info`, `chunks`, `write`,
`validate`, `repair`, `scan`, `index`, `query`...) still runs, but prints a
line on stderr naming the new one; write the 2.0 form.

## Which command

- **What is in this file**: `acidcat inspect FILE`. Every node and field with
  its offset, plus findings. The one to reach for on presets and any "what is
  actually in this file" question.
- **Quick metadata**: `acidcat inspect --summary FILE` (or just
  `acidcat FILE`): format, duration, rate, tempo, key. `--tags` for decoded
  tags without offsets.
- **One value for a script**: `acidcat carve FILE RIFF/fmt_#sample_rate`
  (its bytes with `-o PATH`).
- **The bytes of one thing**: `acidcat od FILE RIFF/smpl`.
- **Change a tag or a field**: `acidcat edit FILE --set NAME=VALUE`
  (verified, with a `_original` backup).
- **Is this file sound?**: `acidcat check FILE` (derived fields: sizes,
  counts, rates), `acidcat check --fix` to rewrite what is witnessed,
  `acidcat audit FILE` for the forensic verdict.
- **What is this blob?**: `acidcat classify`, then `acidcat locate` to find
  the audio and `acidcat carve` or `acidcat extract` to pull it out.
- **Over a tree**: `acidcat stats DIR --by meta|shape|chunks`.
- **Tempo, key or features from the audio**: `acidcat analyze` (needs
  `[analysis]`).
- **Clip to MIDI**: `acidcat convert clip.bwclip -o out.mid`.
- **Search a library**: `acidcat lib index`, then `acidcat lib query`.
- **HTML byte explorer**: `acidcat explore FILE -o out.html`.
- **Drive it by hand**: `acidcat tui FILE` (needs `[tui]`).

## Addresses (ADDR)

Every node and field has an address, and every verb that takes a location
takes the same grammar:

```
RIFF/fmt_                 a node by id: chunk names from the root, a space as _
RIFF/fmt_#sample_rate     a field of it, by key
fmt_   fmt                a node by last step or name, only when unique
RIFF/LIST[1]              the second of two same-named siblings (0-based)
RIFF/*   **/data          globs
@0x100+64   @0x100..0x200 bytes
1:lh5/header#frames       a field inside decoded layer 1
```

Do not guess ids: run `acidcat inspect --chunks FILE` and read them. A node
means its payload. Quote an ADDR holding `[`, `*`, `?` or `#` for the shell.
An ADDR that names nothing exits 1; an ambiguous one lists the candidates.

## inspect

```
acidcat inspect FILE                 # nodes, fields, offsets, findings
acidcat inspect --summary FILE       # format, duration, rate, tempo, key
acidcat inspect --tags FILE          # decoded tags, no offsets
acidcat inspect --chunks FILE        # the node table only
acidcat inspect --only RIFF/fmt_ --hex FILE   # one node, with its bytes
acidcat inspect --deep FILE          # Bitwig device tree + parameters + notes,
                                     # Vital modulation matrix, NI compressed subtree
acidcat inspect --frames FILE        # every MP3 frame, every MIDI event
acidcat inspect --anomalies FILE     # forensic scan: trailing data, polyglots, cavities
acidcat inspect --json FILE          # the contract v1 Document
acidcat inspect FILE1 FILE2 ...      # several files; JSON becomes one Document per line
```

`acidcat formats` lists the 88 formats with a walker; do not rely on a list
from memory. Non-Latin metadata (Korean, CJK, mixed-script) decodes
correctly.

### The JSON

`inspect --json` is the contract v1 Document (`docs/contract/node-v1.md`):
`file.path`, `format.id` and `format.label`, `nodes[]` (each with `id`,
`name`, `payload`, `fields[]` of `key`, `value`, `display`, `type`, `at`),
`findings[]` (`kind`, `code`, `severity`, `node`), `limits`, `layers`,
`typing`. A field's `value` is a number where the text states one, in
seconds, Hz or bits per second (`"44,100 Hz"` is 44100); read `display` for
the text. Every other verb's JSON names a file `path` and a format by
registry id `format` plus `label`, with snake_case keys. Findings of kind
`coverage`, `environment` and `info` are not problems with the file; only
`defect` is.

In Python, `acidcat.open(path)` returns the same Document as views:
`doc.field("RIFF/fmt_#sample_rate").value`, `doc.walk()`, `doc.findings`,
`doc.edit({...})`.

## edit (safe, verified)

Edits in place after writing a `NAME_original` backup; `-o OUT` writes a copy
instead; `--dry-run` shows the change without writing. The result is re-read
and verified before anything is written, and the write is atomic.

```
acidcat edit song.wav --set title="My Loop" --set bpm=140 --set key=Am
acidcat edit take.aiff --set artist="..." -o take_tagged.aiff
acidcat edit patch.vital --set author="..." --set comments="..."
acidcat edit loop.wav --set RIFF/fmt_#sample_rate=48000 --dry-run
acidcat edit track.mp3 --set cover=@art.jpg
acidcat edit track.mp3 --get cover -o art.jpg
acidcat edit field.wav --strip -o clean.wav
```

NAME is one of:
- a tag. WAV: INFO tags (title/artist/album/genre/comment/date), acid
  (bpm/tempo/key), bext (bext_description/originator/...), smpl
  (root/root_note/unity_note). AIFF: title/artist/comment. MP3/FLAC/OGG/M4A:
  title/artist/album/genre/comment/date/key/bpm (via mutagen). Vital:
  preset_name/author/comments/macro names.
- an ADDR naming a typed field (`RIFF/fmt_#sample_rate=48000`; bytes as
  `hex:0100`, the field's own length). The fields tied to it follow: a WAV's
  `block_align` and `avg_bytes_per_sec` after its `channels` or
  `sample_rate`, each named in the output. `--no-cascade` refuses instead. A
  field whose type the walk only inferred needs `--force`.
- `cover`.

The acid chunk stores a root note, not a mode: `key=Am` stores A and says the
minor was dropped. Bitwig and Native Instruments preset writing is
implemented but DISABLED (experimental, pending in-app reload verification);
`edit` says so.

## check and audit

```
acidcat check DIR                    # exit 1 if any file has a violation
acidcat check --fix broken.wav       # rewrite the witnessed fields, audio untouched
acidcat check --fix broken.wav --dry-run
acidcat audit suspect.wav            # STRUCTURE / HIDDEN / FORENSICS / INTEGRITY / PROVENANCE
```

`check` exits 2 on a format it does not model and names the ones it does:
say the file was not checked, never that it passed.

## Exit codes

0 ok; 1 the answer is no (a violation, a defect finding, nothing found, an
ADDR that names nothing); 2 could not run (bad arguments, unreadable input,
a file no walker reads or that `edit`, `extract` or `convert` has nothing
for, a missing extra). A cap that stopped a verb short is
a line on stderr, not a failure; report it as a bound.

## convert (DAW clip to MIDI)

```
acidcat convert clip.bwclip -o out.mid        # Bitwig note clip -> Standard MIDI File
```
Reads pitch/position/duration/velocity from the clip's note lanes. Note names use
the DAW octave convention (middle C = C3 = MIDI 60).

## lib (sample library search)

```
acidcat lib index /path/to/library           # build/update a per-library SQLite index
acidcat lib query --bpm 120:130 --key Am     # filter across registered libraries
acidcat lib query --device Massive --category bass    # indexed preset metadata
acidcat lib query --text reese               # full-text
acidcat lib similar kick.wav --top 10        # nearest by audio features ([analysis])
```
Indexed dimensions include bpm, key, tags, and (for presets) device, product,
creator, category, preset name.

## acidcat explore (interactive HTML)

A standalone datasheet of one file, built from its positioned walk in
process: hex byte grids,
each decoded field tinted over its bytes, hover-to-link, and a dark/light
theme toggle. No dependencies; the HTML does not need the original file.

```
acidcat explore song.mp3 -o song.html
```

## MCP server

Exposes the sample index over MCP. Two transports:

```
acidcat-mcp                              # stdio (default; for local MCP clients)
acidcat-mcp --transport http --port 8765 # streamable HTTP at http://host:8765/mcp
```

The HTTP transport has **no authentication**. Bind it to localhost. `--host`
beyond 127.0.0.1 exposes every tool, including the destructive ones, to anyone
who can reach the port.

### Read the cost prefix before calling

Every tool description opens with `Fast.`, `SLOW.`, `VERY SLOW.`, or
`Destructive.` The same information is on the wire as MCP annotations
(`readOnlyHint` / `destructiveHint` / `idempotentHint`), but most clients do not
surface annotations to the model, so the prefix is what you will actually see.
Treat it as the budget.

- **Fast** -- call freely. `search_samples`, `get_sample`, `locate_sample`,
  `list_libraries`, `list_tags`, `list_keys`, `list_formats`, `index_stats`,
  `find_compatible`
- **SLOW** -- one call is fine, a loop is not. `find_similar` (fast once
  features are cached), `analyze_sample` (~1-10s, first call 30-60s while
  librosa imports), `detect_bpm_key` (~0.5-2s), `reindex`, `discover_libraries`
- **VERY SLOW** -- `reindex_features`. Use `limit` and expect minutes.
- **Destructive** -- writes to the registry, the index, or a file's annotations:
  `register_library`, `discover_libraries`, `forget_library`, `tag_sample`,
  `set_sample_description`. Confirm before calling.

### Answer from metadata first

`search_samples` is the primary tool and covers most questions: bpm range, key,
duration, format, tags, and a full-text field spanning title/artist/album/genre/
comment/description/tags/preset/device/creator/path. Preset metadata is indexed
as a first-class dimension, so `device`, `product`, `creator` and `category`
filter Serum/Vital/Massive/Absynth/FM8/Kontakt patches the same way bpm filters
loops.

Reach past it only when metadata genuinely cannot answer:

- `find_compatible` -- key/BPM compatibility, still metadata, still Fast
- `find_similar` -- timbral nearest-neighbour over librosa vectors, needs
  `[analysis]` and an indexed feature pass. Results carry `percentile_rank` and
  `similarity_above_mean` because same-pack variations cluster around 0.99
  cosine and the raw score cannot separate them; read the rank, not the score.
- `analyze_sample` / `detect_bpm_key` -- read the audio itself. Last resort.

### Registering a library: register does not populate

The most common mistake, and the one the tool names do not warn you about.
`register_library` and `discover_libraries` create the row and point it at a
path. **They do not walk any files.** A library in that state reports
`sample_count: null` and `available: false` in `list_libraries`, and answers no
queries. `reindex` is the step that fills it.

The whole sequence:

1. `list_libraries` -- check it is not already registered
2. `discover_libraries(root, dry_run=true)` -- preview the candidates
3. show the user the candidates, get confirmation
4. `discover_libraries(root, dry_run=false)` -- or `register_library` per folder
   when you want to control the labels
5. **`reindex` each new library** -- otherwise steps 1-4 bought nothing
6. `reindex_features` only if the user wants `find_similar` (VERY SLOW, opt-in)

Verify with `list_libraries` at the end: a populated library has a real
`sample_count`.

### max_depth undercounts, and says when it did

`discover_libraries` defaults to `max_depth=3`, and `audio_count` counts only
within that depth. A pack nesting one level deeper reports fewer files than it
holds -- 520 against a true 657, in one measured case.

A candidate whose count was cut this way carries `audio_count_is_a_floor: true`,
and the result carries a `note`. When you see either, the counts are lower
bounds: re-run with a larger `max_depth` before reporting a number to the user
or deciding a folder failed `min_samples`. Absence of the flag means the count
is complete.

`min_samples` (default 20) is the other silent filter: a folder holding fewer
audio files than that is not offered as a candidate at all.

### Reporting results

Say what was not looked at. If a scan was `dry_run`, say nothing was written.
If a count is a floor, say so rather than quoting it as a total. If libraries
are registered but not reindexed, say they are empty -- do not present a
successful registration as a finished import.

## tui (interactive inspector)

```
acidcat tui FILE        # or bare `acidcat tui` for a file browser
```

A two-pane inspector over the Document: the node tree on the left with the
findings above it and a data inspector below, the bytes on the right, and a
status line naming the layer, the selected node's id and what that node can
do. `?` lists every key. The ones worth knowing: `tab` cycles panes, `z`
zooms one, `b` cycles the byte pane through hex, entropy, hilbert map and
byte histogram, `f` jumps to the next finding, `e` edits a field (the fields
tied to it follow), `x` follows a pointer, `enter` opens a decoded layer and
`u` comes back, `p` plays a node whose caps say it plays, `X` writes the
selected node out, `ctrl+s` saves (leaving a `_original` backup).

On a graph, `r` scopes it to the selected chunk instead of the whole file and it
then follows the selection as you move; `S` changes the vertical scale. With the
graph focused the arrows drive it: up/down rescale, left/right walk the
selection. Entropy defaults to an absolute 0-8 axis; `auto` is what makes sense
of audio, which sits near 7.9 and pins the absolute chart to its ceiling.

Read the caption. It states what the picture covers, whether values were
sampled, and which axis is in use -- a rescaled chart looks identical to an
absolute one. On a small region it also states the ceiling entropy cannot pass,
because entropy over n bytes cannot exceed log2(n).

## Gotchas

- Every walk is bounds-checked; malformed or hostile files yield findings,
  not crashes (the design goal, verified by fuzzing). A cap the walk hit is a
  `coverage` finding naming the limit: say what was not read.
- `edit` and `check --fix` never touch audio sample data, and leave a
  `_original` backup unless you use `-o` or `--overwrite`.
- Capabilities in the Document are marked `inferred` when acidcat worked them
  out rather than a walker declaring them; say so if you rely on one.
- The threat model is pure-Python: denial-of-service and wrong-output, not memory
  corruption. Do not present `inspect` output as a security guarantee about the
  file's safety in other software.
