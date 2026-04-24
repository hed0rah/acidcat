# acidcat

Audio metadata explorer and analysis tool -- like exiftool, but for audio.

Reads BPM, key, duration, tags, and format info from WAV, AIFF, MP3, FLAC,
OGG, Opus, M4A, MIDI, and Serum presets. Zero dependencies for core metadata.
Optional librosa analysis for BPM/key detection and ML feature extraction.

Also ships a persistent SQLite index (`acidcat index`) and an MCP server
(`acidcat-mcp`) so an LLM can query your whole sample library by bpm, key,
tags, or full-text.

## Install

    git clone https://github.com/hed0rah/acidcat.git
    cd acidcat
    pip install -e .                # core: zero deps
    pip install -e .[tags]          # + MP3/FLAC/OGG/M4A (mutagen)
    pip install -e .[analysis]      # + librosa BPM/key detection
    pip install -e .[ml]            # + sklearn similarity/clustering
    pip install -e .[mcp]           # + MCP server (acidcat-mcp)
    pip install -e .[all]           # everything

## Quick Start

    # single file -- instant metadata
    acidcat kick_808.wav
    acidcat loop.mp3
    acidcat pad.flac

    # pipe from stdin
    cat file.wav | acidcat
    curl https://example.com/loop.mp3 | acidcat -

    # JSON output for piping
    acidcat kick_808.wav -f json | jq .BPM

    # deep analysis with librosa
    acidcat kick_808.wav --deep

    # scan a mixed-format directory
    acidcat scan ~/Samples/Breaks -n 200

## Supported Formats

| Format | Extension | What acidcat reads |
|--------|-----------|-------------------|
| WAV    | `.wav`    | BPM, key, loop points, beats, ACID/SMPL chunks, LIST/INFO |
| AIFF   | `.aif`    | Duration, format, name, author, copyright, markers |
| MP3    | `.mp3`    | BPM, key, title, artist, album, genre, comment (ID3v2) |
| FLAC   | `.flac`   | BPM, key, title, artist, album, genre (Vorbis Comment) |
| OGG    | `.ogg`    | BPM, key, title, artist, album, genre (Vorbis Comment) |
| Opus   | `.opus`   | BPM, key, title, artist (Vorbis Comment) |
| M4A    | `.m4a`    | BPM, key, title, artist, album, genre (iTunes atoms) |
| MIDI   | `.mid`    | BPM, key sig, time sig, tracks, note count/range |
| Serum  | `.SerumPreset` | Preset name, author, tags, description |

## Commands

| Command | Description |
|---------|-------------|
| `acidcat FILE` | Show metadata for a single file (auto-detected) |
| `acidcat DIR` | Batch-scan a directory (auto-detected) |
| `acidcat -` | Read from stdin |
| `acidcat info FILE` | Explicit single-file metadata dump |
| `acidcat scan DIR` | Batch-scan with CSV output |
| `acidcat chunks FILE` | Walk RIFF chunks -- offsets, sizes, parsed fields |
| `acidcat survey DIR` | Count chunk types across a directory tree |
| `acidcat detect FILE\|DIR` | Estimate BPM/key using librosa |
| `acidcat features DIR` | Extract 50+ audio features for ML |
| `acidcat similar CSV find TARGET` | Find similar samples by features |
| `acidcat similar CSV cluster` | Cluster samples by audio characteristics |
| `acidcat search CSV query TEXT` | Text-based sample search (legacy CSV) |
| `acidcat dump FILE CHUNK [...]` | Hex-dump specific RIFF chunks |
| `acidcat index DIR` | Upsert DIR into the global SQLite index |
| `acidcat query [flags]` | Filter the global index by bpm/key/tag/text |

## Global Flags

    -f, --format {table,json,csv}   Output format (default: table)
    -o, --output FILE               Write output to file
    -q, --quiet                     Suppress progress output
    -v, --verbose                   Extra detail
    -n, --num N                     Max files to scan (default: 500)
    --has CHUNKS                    Filter by chunk IDs (comma-separated)
    --deep                          Include librosa analysis

## Dependency Groups

| Group | What it adds | Commands enabled |
|-------|-------------|-----------------|
| (none) | zero deps | info, scan, chunks, survey, dump (WAV, AIFF, MIDI, Serum) |
| `[tags]` | mutagen | info, scan for MP3, FLAC, OGG, Opus, M4A |
| `[analysis]` | librosa, numpy, scipy | detect, info --deep |
| `[ml]` | + pandas, scikit-learn | features, similar, search |
| `[mcp]` | mcp SDK | `acidcat-mcp` stdio server |
| `[all]` | everything | all commands, all formats |

## Examples

### Metadata Exploration

    # what chunks exist in your sample library?
    acidcat survey ~/Samples/Loops -n 5000

    # walk all chunks in a specific file
    acidcat chunks ~/Samples/Loops/breakbeat.wav

    # hex-dump the ACID and SMPL chunks
    acidcat dump ~/Samples/Loops/breakbeat.wav acid smpl

    # scan only files with ACID metadata
    acidcat scan ~/Samples/Loops --has acid -n 200

    # scan a directory with mixed formats (WAV, MP3, FLAC, etc.)
    acidcat scan ~/Samples -n 500

### BPM / Key Detection

    # estimate BPM/key with librosa (for files without metadata)
    acidcat detect ~/Samples/OneShots

    # scan with librosa fallback for missing metadata
    acidcat scan ~/Samples/Loops --fallback -n 100

### ML Feature Extraction

    # extract 50+ audio features to CSV
    acidcat features ~/Samples/Loops -n 500

    # generate normalized (StandardScaler) ML-ready dataset
    acidcat features ~/Samples/Loops --ml-ready -n 500

### Similarity & Clustering

    # find 5 samples similar to index 0
    acidcat similar features.csv find 0 -n 5

    # k-means clustering
    acidcat similar features.csv cluster -k 10 -o clustered.csv

## Global Index

`acidcat scan` writes a one-off CSV. `acidcat index` maintains a single
persistent SQLite database at `~/.acidcat/index.db` that aggregates every
directory you point it at. Re-running skips unchanged files and prunes
missing ones. Query it without rescanning.

    # add a library (repeat for as many folders as you want)
    acidcat index ~/Samples/Loops
    acidcat index ~/Samples/OneShots

    # show what has been indexed
    acidcat index --list-roots
    acidcat index --stats

    # optional: include librosa features (slower, enables similarity)
    acidcat index ~/Samples/Loops --features

    # optional: import legacy <name>_tags.json into the index
    acidcat index ~/Samples --import-tags old_tags.json

    # drop everything under a root
    acidcat index --remove-root ~/Samples/Loops

### Querying

    acidcat query --bpm 120:130 --key Am
    acidcat query --tag drums --tag punchy --duration :1
    acidcat query --text "dusty lofi" --limit 20
    acidcat query --format mp3 --root ~/Samples/Loops
    acidcat query --bpm 128 --paths-only | xargs -I {} cp {} out/

Override the DB on any command with `--db PATH` or the `ACIDCAT_DB`
environment variable.

## MCP Server

`acidcat-mcp` is a stdio MCP server that exposes the global index as
structured tools. An LLM can search your library by metadata, find
compatible keys via Camelot, or (with `[analysis]` installed) find
similar samples by librosa feature cosine.

    pip install -e .[tags,mcp]        # minimum for discovery + writes
    pip install -e .[tags,analysis,mcp]  # unlock find_similar/analyze_*

Claude Desktop / Claude Code config:

    {
      "mcpServers": {
        "acidcat": {
          "command": "acidcat-mcp"
        }
      }
    }

Optional: pass `--db` or set `ACIDCAT_DB` on the server process if your
index lives outside the default path.

Tool tiers (each tool description starts with `Fast.`, `SLOW.`, or
`VERY SLOW.` so the model self-selects):

- **Fast (SQLite only)**: `search_samples`, `get_sample`, `locate_sample`,
  `list_roots`, `list_tags`, `list_keys`, `list_formats`, `index_stats`,
  `find_compatible`
- **Slow analysis** (needs `[analysis]`): `find_similar`, `analyze_sample`,
  `detect_bpm_key`
- **Index management**: `reindex`, `reindex_features`
- **Write**: `tag_sample`, `describe_sample` (marked destructive)

## License

MIT
