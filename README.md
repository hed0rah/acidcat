# acidcat

Audio metadata explorer and analysis tool -- like exiftool, but for audio.

Reads BPM, key, duration, tags, and format info from WAV, AIFF, MP3, FLAC,
OGG, Opus, M4A, MIDI, and Serum presets. Zero dependencies for core metadata.
Optional librosa analysis for BPM/key detection and ML feature extraction.

## Install

    git clone https://github.com/hed0rah/acidcat.git
    cd acidcat
    pip install -e .                # core: zero deps
    pip install -e .[tags]          # + MP3/FLAC/OGG/M4A (mutagen)
    pip install -e .[analysis]      # + librosa BPM/key detection
    pip install -e .[ml]            # + sklearn similarity/clustering
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
| `acidcat search CSV query TEXT` | Text-based sample search |
| `acidcat dump FILE CHUNK [...]` | Hex-dump specific RIFF chunks |

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

## License

MIT
