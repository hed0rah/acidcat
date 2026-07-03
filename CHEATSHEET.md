# acidcat cheatsheet

A low-level audio and preset metadata tool. readelf/exiftool for audio.

## commands

| command | does |
|---|---|
| `acidcat FILE` | quick info for one file (bare path auto-routes to `info`) |
| `acidcat DIR` | scan a directory (auto-routes to `scan`) |
| `acidcat info FILE` | format, duration, key, bpm, tags (uses mutagen where it helps) |
| `acidcat inspect FILE...` | readelf-style structural dump (see flags below) |
| `acidcat chunks FILE` | RIFF chunk table (offsets, sizes, parsed fields) |
| `acidcat dump FILE CHUNK` | hex-dump a named chunk |
| `acidcat survey DIR` | count chunk types across a tree |
| `acidcat detect FILE\|DIR` | estimate bpm/key with librosa |
| `acidcat features DIR` | extract 50+ audio features (ML) |
| `acidcat scan DIR` | batch scan to CSV |
| `acidcat similar CSV find\|cluster` | similarity search / clustering |
| `acidcat index DIR` | upsert into the global SQLite index |
| `acidcat query [flags]` | filter the index by bpm/key/tag/text |
| `acidcat convert clip.bwclip -o out.mid` | export a DAW clip's notes to Standard MIDI |
| `acidcat --version` | version |

Read from stdin: `acidcat -` or `cat f.wav | acidcat`.

## inspect flags

```
acidcat inspect FILE... [-f table|json] [--pretty] [--hex] [--frames]
                        [--only IDS] [--exclude IDS] [--full] [--color auto|always|never]
```

| flag | effect |
|---|---|
| (default) | readelf-style table: chunk map, decoded fields, lint warnings |
| `--pretty` | human-friendly metadata view, no byte offsets (best for presets/tags) |
| `--hex` | raw bytes beside each decoded field |
| `-F`, `--frames` | per-element deep dump (every MPEG frame / MIDI event) |
| `--only fmt,bext` | show only these chunks (case-insensitive); compose with `--hex` |
| `--exclude data` | hide these chunks |
| `--full` | self-contained JSON dump (raw region bytes + absolute field offsets) |
| `-f json` | JSON output; multiple files become NDJSON (one record per line) |
| `--color` | auto (TTY) / always / never; honors NO_COLOR |
| multiple files | each under a `File:` banner |

## formats `inspect` decodes natively

audio: WAV/RIFF, RF64, AIFF/AIFC, FLAC, MP3 (ID3v2/v1, Xing/VBRI/LAME), MIDI,
MP4/M4A (box tree, codec, iTunes tags).
presets: Serum 1 + 2 (`.serum`/`.SerumPreset`), Bitwig (`.bwpreset`/`.bwclip`),
Vital (`.vital`), Native Instruments (`.nmsv`/`.nabs`/`.ksd`/`.nksf`/`.nki`),
NI Compressed Wave (`.ncw`).

## recipes

```
# just the tags/metadata, prettily
acidcat inspect --pretty track.m4a
acidcat inspect --pretty MyPatch.bwpreset

# hexdump one chunk
acidcat inspect --only fmt --hex loop.wav

# machine-readable, many files, into jq
acidcat inspect -f json *.wav | jq -c '.chunks[].id'

# build a standalone interactive byte explorer for any file
acidcat inspect --full song.mp3 | python build_explorer.py -o song.html

# per-frame MP3 bitrate switching / per-event MIDI
acidcat inspect --frames song.mp3
acidcat inspect --frames beat.mid

# index a library, then query it
acidcat index ~/samples
acidcat query --bpm 120:130 --key Am

# pull the notes out of a Bitwig clip as MIDI
acidcat convert MyClip.bwclip -o MyClip.mid
acidcat query --device Polysynth --category Reverb   # search preset metadata
acidcat query --product Vital --creator someone
```

## install / upgrade

```
pipx install acidcat          # first time
pipx upgrade acidcat          # get the newest (reinstall does NOT upgrade)
pip install -U acidcat        # with pip
pip install -e .              # editable, from a checkout (runs live source)
```
