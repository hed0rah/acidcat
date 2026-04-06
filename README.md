# acidcat

Audio metadata explorer and analysis tool -- like exiftool, but for audio.

Parses WAV, AIFF, MIDI, and Serum preset files. Extracts BPM, key,
loop points, and format metadata. Detects BPM/key via librosa when
metadata is missing. Extracts 50+ audio features for ML.

## Install

    git clone https://github.com/hed0rah/acidcat.git
    cd acidcat
    pip install -e .

## Quick Start

    # Single file -- instant metadata (the default)
    acidcat kick_808.wav

    # Deep analysis with librosa
    acidcat kick_808.wav --deep

    # JSON output for piping
    acidcat kick_808.wav -f json | jq .BPM

    # Scan a directory
    acidcat scan ~/Samples/Breaks -n 200

## Commands

| Command | Description |
|---------|-------------|
| `acidcat FILE` | Show metadata for a single file (WAV, AIFF, MIDI, Serum) |
| `acidcat DIR` | Batch-scan a directory (auto-detected) |
| `acidcat info FILE` | Explicit single-file metadata dump |
| `acidcat scan DIR` | Batch-scan with CSV output |
| `acidcat chunks FILE` | Walk RIFF chunks -- offsets, sizes, parsed fields |
| `acidcat survey DIR` | Count chunk types across a directory tree |
| `acidcat detect FILE\|DIR` | Estimate BPM/key using librosa |
| `acidcat features DIR` | Extract 50+ audio features for ML |
| `acidcat similar CSV find TARGET` | Find similar samples by features |
| `acidcat similar CSV cluster` | Cluster samples by audio characteristics |
| `acidcat search CSV query TEXT` | Text-based sample search |
| `acidcat search CSV interactive` | Interactive tagging session |
| `acidcat dump FILE CHUNK [...]` | Hex-dump specific RIFF chunks |

## Global Flags

    -f, --format {table,json,csv}   Output format (default: table)
    -o, --output FILE               Write output to file
    -q, --quiet                     Suppress progress output
    -v, --verbose                   Extra detail
    -n, --num N                     Max files to scan (default: 500)
    --has CHUNKS                    Filter by chunk IDs (comma-separated)

## Examples

### Metadata Exploration

    # What chunks exist in your sample library?
    acidcat survey "D:\Samples\Loops" -n 5000

    # Walk all chunks in a specific file
    acidcat chunks "D:\Samples\Loops\breakbeat.wav"

    # Hex-dump the ACID and SMPL chunks
    acidcat dump "D:\Samples\Loops\breakbeat.wav" acid smpl

    # Scan only files with ACID metadata
    acidcat scan "D:\Samples\Loops" --has acid -n 200

### BPM / Key Detection

    # Estimate BPM/key with librosa (for files without metadata)
    acidcat detect "D:\Samples\OneShots"

    # Scan with librosa fallback for missing metadata
    acidcat scan "D:\Samples\Loops" --fallback -n 100

### ML Feature Extraction

    # Extract 50+ audio features to CSV
    acidcat features "D:\Samples\Loops" -n 500

    # Generate normalized (StandardScaler) ML-ready dataset
    acidcat features "D:\Samples\Loops" --ml-ready -n 500

### Similarity & Clustering

    # Find 5 samples similar to index 0
    acidcat similar features.csv find 0 -n 5

    # K-means clustering
    acidcat similar features.csv cluster -k 10 -o clustered.csv

### Text Search & Tagging

    # Interactive tagging session
    acidcat search metadata.csv interactive

    # Search by text
    acidcat search metadata.csv query "punchy kick"

    # Search by tags
    acidcat search metadata.csv tags "drums,electronic"

## Audio Features (50+)

**Spectral**: centroid, rolloff, bandwidth, contrast, zero crossing rate
**Timbral**: MFCC (13 coefficients), chroma, mel-frequency, tonnetz
**Rhythmic**: tempo, beat count, RMS energy
**Metadata**: ACID BPM/key/beats, SMPL root/loops, format info

## RIFF Chunks Parsed

| Chunk | What's inside |
|-------|---------------|
| `acid` | BPM, root note, beats, meter |
| `smpl` | Root key, loop start/end points |
| `inst` | Base note, detune, gain, key/velocity range |
| `fmt ` | Format tag, channels, sample rate, bits |
| `fact` | Sample length (non-PCM) |
| `cue ` | Cue marker positions |
| `LIST` | INFO tags (title, artist, comment, software) |
| `bext` | Broadcast extension (description, originator, date) |

## License

MIT
