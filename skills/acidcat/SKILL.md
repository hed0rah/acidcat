---
name: acidcat
description: >
  Inspect, edit, and search low-level metadata and byte structure of audio files
  (WAV, AIFF, MP3, FLAC, OGG, M4A, MIDI) and synth/DAW presets (Bitwig,
  Native Instruments Massive/Absynth/Kontakt/NKS/KORE, Vital, Serum, VST FXP, ReCycle RX2, NCW; and containers MP4/M4A and RMID). Use when the
  user wants byte-level format structure, to read or write tags / loop points /
  BPM / key / root note, to build an interactive HTML byte-explorer of a file,
  to export a DAW note clip to MIDI, or to index and search a sample library.
---

# acidcat

acidcat is a pure-Python tool (one dependency, mutagen; native inspect walkers need nothing): readelf/exiftool for audio
and synth/DAW presets. It reads from byte-level facts only and treats every file
as hostile input (bounded parsers designed to degrade to a clean warning rather than crash).

Install: `pip install acidcat` (core). Extras: `[mcp]` (stdio MCP server),
`[mcp-http]` (streamable-HTTP MCP server), `[analysis]` (librosa: `detect`,
`features`, `similar`, and the MCP tools that need feature vectors), `[tui]`
(the interactive inspector), `[crypto]` (encrypted disc images), `[all]`.

## Which command

- **Structure / deep decode**: `acidcat inspect FILE`. This is the one to reach
  for on presets and any "what's actually in this file" question.
- **Quick metadata (audio/tags)**: `acidcat inspect --summary FILE` (or just
  `acidcat FILE`). WAV/AIFF/MP3/FLAC/OGG/M4A/MIDI/Serum. The summary does NOT
  parse Bitwig/NI/Vital presets; plain `inspect` does.
- **Edit metadata**: `acidcat edit FILE --set field=value` (verified, with a
  `_original` backup).
- **Clip to MIDI**: `acidcat convert clip.bwclip -o out.mid`.
- **Search a library**: `acidcat lib index` then `acidcat lib query`.
- **HTML byte-explorer**: `acidcat explore FILE -o out.html`.
- **Drive it by hand**: `acidcat tui FILE` (needs `[tui]`).
- **Is this file sound?**: `acidcat audit FILE` (exit 1 on findings, so it works
  as a CI gate), `acidcat check` for structural checks, `acidcat check --fix`
  to fix what is witnessed.
- **Where is the audio in this blob?**: `acidcat locate`, then `acidcat carve`
  or `acidcat extract` to pull it out.

## inspect

```
acidcat inspect FILE                 # structural dump (chunks/boxes/frames + lint warnings)
acidcat inspect --pretty FILE        # human-friendly metadata view
acidcat inspect --verbose FILE       # deep deconstruction: Bitwig device tree +
                                     # parameters + note lanes, Vital modulation
                                     # matrix, NI hsin FastLZ subtree
acidcat inspect --frames FILE        # per-frame/per-event dump (MP3 frames, MIDI events)
acidcat inspect --only fmt,data FILE # select regions; --exclude to drop them
acidcat inspect --anomalies FILE     # forensic scan: trailing data, polyglots, cavities, stego notice
acidcat inspect --full FILE          # self-contained JSON (feeds acidcat explore)
acidcat inspect FILE1 FILE2 ...      # multiple files; JSON output becomes NDJSON
```

Formats: WAV, RF64, AIFF/AIFC, MIDI, RMID, MP3, FLAC, OGG, MP4/M4A, Serum, VST FXP,
ReCycle RX2, Bitwig
(.bwpreset/.bwclip), Vital, NCW, Native Instruments (hsin: .nmsv/.nabs/.nki;
.ksd; .nksf). Non-Latin metadata (Korean, CJK, mixed-script) decodes correctly.

## write (exiftool-style, safe)

Edits in place after writing a `NAME_original` backup; `-o OUT` writes a copy
instead; `--dry-run` shows the diff without writing. Atomic (temp + fsync +
replace). Refuses RF64/malformed. Verifies audio bytes are unchanged after a WAV
rewrite.

```
acidcat edit song.wav --set title="My Loop" --set bpm=140 --set key=Am
acidcat edit take.aiff --set artist="..." -o take_tagged.aiff
acidcat edit patch.vital --set author="..." --set comments="..."
```

Editable fields by format:
- WAV: INFO tags (title/artist/album/genre/comment/date), acid (bpm/tempo/key),
  bext (bext_description/originator/...), smpl (root/root_note/unity_note).
- AIFF: title/artist/comment (NAME/AUTH/ANNO).
- MP3/FLAC/OGG/M4A: title/artist/album/genre/comment/date/key/bpm (via mutagen).
- Vital: preset_name/author/comments/macro names.
- Bitwig / Native Instruments preset writing is implemented but currently
  DISABLED (experimental, pending in-app reload verification). `write` will say
  so; reading via `inspect` is fully supported.

## convert (DAW clip to MIDI)

```
acidcat convert clip.bwclip -o out.mid        # Bitwig note clip -> Standard MIDI File
```
Reads pitch/position/duration/velocity from the clip's note lanes. Note names use
the DAW octave convention (middle C = C3 = MIDI 60).

## lib index + lib query (sample library search)

```
acidcat lib index /path/to/library      # build/update a per-library SQLite index
acidcat lib query --bpm 120-130 --key Am    # filter across registered libraries
acidcat lib query --device Massive --category bass    # search indexed preset metadata
acidcat lib query --text reese          # full-text
```
Indexed dimensions include bpm, key, tags, and (for presets) device, product,
creator, category, preset name.

## acidcat explore (interactive HTML)

A pure JSON-to-HTML transform of an `inspect --full` dump: a datasheet with hex
byte grids, each decoded field tinted over its bytes, hover-to-link, and a
dark/light theme toggle. No dependencies, no access to the original file needed.

```
acidcat explore song.mp3 -o song.html   # legacy pipe: inspect --full | python build_explorer.py
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

A two-pane inspector: the parsed tree on the left, bytes on the right, with a
forensics panel above the tree. `?` lists every key. The ones worth knowing:
`tab` cycles panes, `z` zooms one, `b` cycles the byte pane through hex,
entropy, hilbert map and byte histogram, `f` jumps to the next forensic finding,
`e` edits a field, `ctrl+s` saves (leaving a `_original` backup).

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

- Every `inspect`/`info` call is bounds-checked; malformed or hostile files yield
  warnings, not crashes (the design goal, verified by fuzzing).
- `write` never touches audio sample data; it only rewrites metadata regions, and
  always leaves a `_original` backup unless you use `-o`.
- The threat model is pure-Python: denial-of-service and wrong-output, not memory
  corruption. Do not present `inspect` output as a security guarantee about the
  file's safety in other software.
