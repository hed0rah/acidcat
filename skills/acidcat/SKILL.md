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

acidcat is a pure-Python (zero-dependency core) tool: readelf/exiftool for audio
and synth/DAW presets. It reads from byte-level facts only and treats every file
as hostile input (bounded parsers, never crashes, degrades to a clean warning).

Install: `pip install acidcat` (core). Extras: `[mcp]` (stdio MCP server),
`[mcp-http]` (streamable-HTTP MCP server), `[ml]`/`[viz]` (librosa analysis),
`[all]`.

## Which command

- **Structure / deep decode**: `acidcat inspect FILE`. This is the one to reach
  for on presets and any "what's actually in this file" question.
- **Quick metadata (audio/tags)**: `acidcat info FILE`. WAV/AIFF/MP3/FLAC/OGG/
  M4A/MIDI/Serum. Note: `info` does NOT parse Bitwig/NI/Vital presets, it will
  tell you to use `inspect`.
- **Edit metadata**: `acidcat write FILE --set field=value`.
- **Clip to MIDI**: `acidcat convert clip.bwclip -o out.mid`.
- **Search a library**: `acidcat index` then `acidcat query`.
- **HTML byte-explorer**: `acidcat inspect --full FILE | python build_explorer.py -o out.html`.

## inspect

```
acidcat inspect FILE                 # structural dump (chunks/boxes/frames + lint warnings)
acidcat inspect --pretty FILE        # human-friendly metadata view
acidcat inspect --verbose FILE       # deep deconstruction: Bitwig device tree +
                                     # parameters + note lanes, Vital modulation
                                     # matrix, NI hsin FastLZ subtree
acidcat inspect --frames FILE        # per-frame/per-event dump (MP3 frames, MIDI events)
acidcat inspect --only fmt,data FILE # select regions; --exclude to drop them
acidcat inspect --full FILE          # self-contained JSON (feeds build_explorer.py)
acidcat inspect FILE1 FILE2 ...      # multiple files; JSON output becomes NDJSON
```

Formats: WAV, RF64, AIFF/AIFC, MIDI, MP3, FLAC, OGG, MP4/M4A, Serum, Bitwig
(.bwpreset/.bwclip), Vital, NCW, Native Instruments (hsin: .nmsv/.nabs/.nki;
.ksd; .nksf). Non-Latin metadata (Korean, CJK, mixed-script) decodes correctly.

## write (exiftool-style, safe)

Edits in place after writing a `NAME_original` backup; `-o OUT` writes a copy
instead; `--dry-run` shows the diff without writing. Atomic (temp + fsync +
replace). Refuses RF64/malformed. Verifies audio bytes are unchanged after a WAV
rewrite.

```
acidcat write song.wav --set title="My Loop" --set bpm=140 --set key=Am
acidcat write take.aiff --set artist="..." -o take_tagged.aiff
acidcat write patch.vital --set author="..." --set comments="..."
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

## index + query (sample library search)

```
acidcat index /path/to/library          # build/update a per-library SQLite index
acidcat query --bpm 120-130 --key Am    # filter across registered libraries
acidcat query --device Massive --category bass    # search indexed preset metadata
acidcat query "reese"                    # full-text
```
Indexed dimensions include bpm, key, tags, and (for presets) device, product,
creator, category, preset name.

## build_explorer.py (interactive HTML)

A pure JSON-to-HTML transform of an `inspect --full` dump: a datasheet with hex
byte grids, each decoded field tinted over its bytes, hover-to-link, and a
dark/light theme toggle. No dependencies, no access to the original file needed.

```
acidcat inspect --full song.mp3 | python build_explorer.py -o song.html
```

## MCP server

Exposes the sample index over MCP. Two transports:

```
acidcat-mcp                              # stdio (default; for local MCP clients)
acidcat-mcp --transport http --port 8765 # streamable HTTP at http://host:8765/mcp
```

Tools: `search_samples`, `get_sample`, `locate_sample`, `list_libraries`,
`list_tags`, `list_keys`, `list_formats`, `index_stats`, `find_compatible`,
`find_similar`, `analyze_sample`, `detect_bpm_key`, `reindex`, `reindex_features`,
`register_library`, `forget_library`, `discover_libraries`, `tag_sample`,
`set_sample_description`.

## Gotchas

- Every `inspect`/`info` call is bounds-checked; malformed or hostile files yield
  warnings, not crashes (the design goal, verified by fuzzing).
- `write` never touches audio sample data; it only rewrites metadata regions, and
  always leaves a `_original` backup unless you use `-o`.
- The threat model is pure-Python: denial-of-service and wrong-output, not memory
  corruption. Do not present `inspect` output as a security guarantee about the
  file's safety in other software.
