<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo-horizontal-dark.svg">
    <img src="docs/brand/logo-horizontal-light.svg" alt="acidcat" width="400">
  </picture>
</p>

# acidcat

A pure-Python inspector, editor, and forensic tool for audio files and synth/DAW
presets: read the metadata, decode the format structure byte by byte, flag
anomalies, repair broken containers, and identify how a file was made.

Reads BPM, key, duration, tags, and format info from WAV, AIFF, MP3, FLAC,
OGG, Opus, M4A, MIDI, and Serum presets. Also structurally decodes Bitwig
(.bwpreset/.bwclip), Native Instruments (Massive/Absynth/Kontakt/NKS/KORE),
Vital, NCW, SoundFont (SF2/SF3), tracker modules (MOD/XM/IT), MP4, VST FXP,
ReCycle RX2, and RMID containers via `inspect`. Beyond reading: `repair` and
`validate` fix and check container structure (stale sizes, offset tables,
counts, pad bytes) without touching a byte of audio, and `audit` gives a
forensic verdict (structure, integrity, hidden data, and the writing tool).
One pure-Python dependency (mutagen); the native `inspect` walkers need nothing.
Optional librosa analysis for BPM/key detection and ML feature extraction.

Also ships per-library SQLite indexes (`acidcat index`) tracked in a
small global registry, plus an MCP server (`acidcat-mcp`) so an LLM can
query your whole collection across libraries by bpm, key, tags, or
full-text.

## Install

Python 3.10+.

    pip install acidcat              # core + mutagen, one dependency
    pip install acidcat[analysis]    # + librosa BPM/key detection + features
    pip install acidcat[tui]         # + the interactive terminal inspector
    pip install acidcat[mcp]         # + MCP server (acidcat-mcp, stdio)
    pip install acidcat[mcp-http]    # + MCP streamable-HTTP transport
    pip install acidcat[crypto]      # + AES for encrypted Wii disc extraction
    pip install acidcat[all]         # everything

From a checkout, swap `acidcat` for `-e .`:

    git clone https://github.com/hed0rah/acidcat.git
    cd acidcat
    pip install -e ".[all]"

## Quick Start

    # single file -- instant metadata
    acidcat kick_808.wav
    acidcat loop.mp3
    acidcat pad.flac

    # pipe from stdin
    cat file.wav | acidcat
    curl https://example.com/loop.mp3 | acidcat -

    # JSON output for piping
    acidcat kick_808.wav --json | jq .BPM

    # deep analysis with librosa
    acidcat kick_808.wav --deep

    # scan a mixed-format directory
    acidcat scan ~/Samples/Breaks -n 200

## Supported Formats

| Format | Extension | What acidcat reads |
|--------|-----------|-------------------|
| WAV    | `.wav`    | BPM, key, loop points, beats, ACID/SMPL, LIST/INFO, bext, cart, iXML |
| AIFF   | `.aif`    | Duration, format, name, author, copyright, markers |
| MP3    | `.mp3`    | BPM, key, title, artist, album, genre, comment (ID3v2) |
| FLAC   | `.flac`   | BPM, key, title, artist, album, genre (Vorbis Comment) |
| OGG    | `.ogg`    | BPM, key, title, artist, album, genre (Vorbis Comment) |
| Opus   | `.opus`   | BPM, key, title, artist (Vorbis Comment) |
| M4A    | `.m4a`    | BPM, key, title, artist, album, genre (iTunes atoms) |
| MIDI   | `.mid`    | BPM, key sig, time sig, tracks, note count/range |
| RMID   | `.rmid`   | RIFF-wrapped MIDI: RIFF wrapper + the inner SMF (inspect) |
| MIDI 2.0 | `.midi2` | MIDI Clip File: SMF2CLIP magic + UMP stream -- resolution, tempo, time sig, tick-stamped events (inspect) |
| N64 bank | `.ctl` | libultra ALBankFile: bank/instrument/wavetable tree + VADPCM codebooks (inspect); `core/codecs/vadpcm.py` decodes N64 vector ADPCM |
| Serum  | `.SerumPreset` | Preset name, author, tags, description |
| VST FXP | `.fxp` | Preset kind, plugin id, version, preset name (inspect) |
| ReCycle | `.rx2` | CAT/REX2 chunks, creator, slice count (inspect) |
| Ableton | `.asd` | Live's analysis sidecar: warp markers and the **tempo derived from them**, onsets with energies, warp-engine parameters, the overview pyramid, and a frame grid that recovers the sample rate, frame count and duration of audio that has been **deleted** (inspect) |
| Ableton | `.als`, `.alc`, `.adg`, `.adv`, `.agr` | Live Set / Clip / rack / device preset / groove: gzipped XML -- exact Live build, track and clip counts (inspect) |
| Max for Live | `.amxd` | `ampf` chunk chain around the Max patcher (inspect) |
| Bitwig WT | `.wt` | Wavetable header: frame count, samples/frame, 16-bit sample block (inspect) |
| Bitwig | `.bwpreset`, `.bwclip` | Device tree, parameters, clip notes (inspect + index) |
| Bitwig multisample | `.multisample` | Zone map: per-sample root note, key/velocity range, loop (inspect) |
| Native Instruments | `.nmsv`, `.nabs`, `.ksd`, `.nksf`, `.nki` | Preset metadata, NKS tags, FastLZ subtree (inspect + index) |
| Vital  | `.vital`  | Patch name, author, tags, modulation matrix (inspect + index) |
| NCW    | `.ncw`    | NI Compressed Wave header, channel/block info (inspect + convert to WAV) |
| SoundFont | `.sf2`, `.sf3` | sfbk metadata + every named sample with its byte offset; SF3 = Ogg-Vorbis samples (inspect + convert to WAV/Ogg) |
| Tracker | `.mod`, `.xm`, `.it` | ProTracker / FastTracker II / Impulse Tracker: header, pattern order, every embedded sample at its byte offset; IT offset tables as pointers (inspect) |
| Console streams | `.adx`, `.brstm`, `.hps`, `.vag` | CRI ADX, Nintendo BRSTM, HAL PCM Stream and Sony VAG: codec, channels, rate, frame count and derived duration in one shared vocabulary, plus each format's own block layout (inspect + extract) |
| MDX    | `.mdx`    | Sharp X68000 MXDRV tune: Shift-JIS title, PDX sample-bank reference, per-channel MML offsets and OPM voice definitions (inspect) |
| SID    | `.sid`    | PSID/RSID: big-endian header, C64 memory image, subtunes, chip model, clock, up to three SID addresses, HVSC songlength key (inspect); plays by running the tune's 6510 player |
| MP4    | `.mp4`, `.m4a` | Box tree, codec info, iTunes tags, `stco`/`co64` offset tables (inspect + repair) |

## Format anatomy

Interactive datasheets for the formats acidcat dissects, each drawn byte by byte:
**hover** a field to light its exact bytes and read the decode, **click** a field
for its table. The RIFF/WAVE family, MP3, FLAC, Ogg, MP4, MIDI, the trackers, the
sampler and synth-preset formats, and more, with the history and edge-case notes
behind each.

**Browse them at [hed0rah.github.io/audio_files_anatomy](https://hed0rah.github.io/audio_files_anatomy/)**
-- a good start is the [WAV / RIFF page](https://hed0rah.github.io/audio_files_anatomy/wav-anatomy.html),
which walks the container, the `fmt`/`data`/`smpl`/`acid` chunks byte by byte, and
the whole RIFF-to-BWF-to-RF64-to-Wave64 family.

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
| `acidcat shape DIR` | One-line structural fingerprint per file for specimen-hunting -- pipe to `sort \| uniq -c` to surface rare shapes; `--fast` (header-only), `--anomalies`, `--format FMT`, `--coarse` |
| `acidcat detect FILE\|DIR` | Estimate BPM/key using librosa |
| `acidcat features DIR` | Extract 50+ audio features for ML |
| `acidcat similar FILE` | Find samples that sound like FILE, over the index (`index --features` first); `-n N`, `--kind`, `--no-kind-filter`, `--paths-only`, `--registry PATH` to query a registry other than the global one |
| `acidcat dump FILE CHUNK [...]` | Hex-dump specific RIFF chunks |
| `acidcat od FILE` | Colored objdump-x-style hex view: header bytes plus per-field offset / hex / decoded value, opaque payloads dimmed; `--color`, `--width` |
| `acidcat inspect FILE... [--hex] [--frames] [--only/--exclude IDS] [--full] [--anomalies] [--pretty] [--color]` | Byte-level structural dump with lint warnings, for the 79 formats `acidcat formats` lists -- audio files, instrument banks, synth presets, tracker modules, console streams and disc images. Takes multiple files (each under a `File:` banner; JSON becomes NDJSON). `--frames` per-frame/event dump, `--only`/`--exclude` select chunks, `--hex` raw bytes, `--full` a self-contained JSON dump feeding `acidcat explore`, `--anomalies` a forensic scan (trailing data, polyglots, cavities, size mismatches, LSB-stego notice), `--pretty` a human-friendly metadata view, `--verbose` a deep deconstruction (Bitwig device tree/parameters/notes, Vital modulation matrix, ...), `--color` to syntax-highlight. `--offset/--length/--end/--at EXPR` restrict the dump to a byte range. `--sandbox` parses untrusted input in an isolated worker with memory and time caps (`--sandbox-profile/--sandbox-mem/--sandbox-timeout`); experimental and Linux-only, so it is a hardening option rather than a guarantee |
| `acidcat index DIR` | Upsert DIR into the global SQLite index |
| `acidcat query [flags]` | Filter the global index by bpm/key/tag/text |
| `acidcat query --compatible-with FILE` | Find samples that mix with FILE: harmonic key (Camelot) + compatible tempo (incl. half/double-time) |
| `acidcat convert FILE [-o OUT]` | Export/transcode: `.bwclip` -> MIDI, NCW -> WAV (single file or a directory), SF2/SF3 -> a folder of samples, 8SVX -> WAV; `--to-pcm` decodes an ADPCM or mistagged WAV to plain playable 16-bit PCM (`--codec ima` to force it) |
| `acidcat classify FILE\|DIR [--shallow]` | Triage before anything expensive: is this one format you understand, a container holding files, a chunked-but-unknown format, damaged remains, or not audio at all. Each verdict names the verb to run next |
| `acidcat locate BLOB [--mode strict\|normal\|aggressive] [--analyze] [--transforms] [--min-confidence C] [-v]` | Find the audio regions in a raw blob or disk image (containers, signatureless raw PCM, headerless MP3 streams) and report them; never writes. `--analyze` infers PCM geometry, `--transforms` finds audio hidden under XOR/rotate/nibble-swap, `-v` shows the evidence. Pipe `--json` into `carve --batch` |
| `acidcat wrap [RAW] [--rate N] [--bits N] [--channels N] [--endian le\|be]` | Give headerless PCM a WAV header so it plays. The end of the recovery chain: `carve … \| acidcat wrap --rate 44100 --bits 16 --endian be > out.wav` |
| `acidcat census DIR [--json]` | Chunk-ID histogram across a whole corpus, plus flags for the open questions (rare chunks, odd format tags) -- the specimen-hunting view |
| `acidcat formats` | The capability matrix: which formats acidcat can inspect / extract / convert / repair. The fastest answer to "can it read my files?" |
| `acidcat extract BANK [-o DIR]` | Pull every embedded sample out of a known bank/module as its own WAV: MOD/XM/IT/S3M, Gravis `.pat`, 8SVX, NCW, SF2/SF3, Bitwig `.multisample`, Kurzweil `.krz`, E-mu `.e4b`/`.e5b`, MPC `.snd`. Also rips soundtracks off console disc images: PlayStation/CD-XA (`.bin`/`.img`), any `.cue` (CD-DA), GameCube `.iso` (HPS/ADX/DTK), Wii `.iso` (BRSTM, needs `[crypto]`), N64 `.z64/.n64/.v64` ROMs (container-agnostic VADPCM recovery), and SNES `.sfc/.smc` ROMs (container-agnostic BRR recovery). `--json` for a manifest |
| `acidcat write FILE --set field=value` | Edit metadata in place, with a `_original` backup, `-o` copy, and `--dry-run`; custom frames via `txxx:NAME=value`; `--strip` removes identifying metadata (tags/bext/iXML/ID3) while keeping the audio and functional chunks, and ignores `--set`; Bitwig/NI preset editing (experimental) |
| `acidcat probe read\|scan\|find\|strings\|hexdump\|diff\|entropy\|map\|lsb [OPTIONS] FILE...` | Low-level byte dissection (RE-tool surface): typed read at an offset (`read fmt.sample_rate -t u32`), value scan, byte-pattern find, strings, hexdump, diff, plus `entropy` (Shannon curve + histogram), `map` (binvis Hilbert byte-map), and `lsb` (sample-LSB entropy, to spot LSB steganography in PCM WAVs). Addresses can be raw offsets or structural names (`chunk` / `chunk.field`) resolved through the walker |
| `acidcat carve FILE (--chunk ID \| --trailing \| --offset N [--length N] \| --at EXPR \| --batch SRC)` | Extract a structurally-identified byte region (a chunk payload, an appended blob, or an explicit/anchored range) to a file or stdout; `--batch` consumes `locate` records and cuts every region into a directory. Two flags turn it into a decoder rather than a cutter: `--struct '@OFF name:type name:type ...'` decodes a labeled record at any `--at` address, and `--field NAME` prints a single walker-decoded field by the name `inspect` shows |
| `acidcat repair FILE [--dry-run] [-o OUT]` | Fix stale container sizes, offset tables, table counts, and pad bytes without touching a byte of audio (WAV, RF64, AIFF, MP4, FLAC); keeps a `_original` backup. `--keep-pad` leaves a non-zero pad byte as it is instead of normalizing it to `0x00`, for when that byte is evidence (`inspect --anomalies` reports it as a cavity) rather than a defect |
| `acidcat validate FILE\|DIR [-q]` | Read-only structural check with an exit code (0 = all consistent, 1 = any violation, 2 = nothing structurally modeled to check); walks a directory tree. `--deep` additionally verifies the checksums a format carries about itself (FLAC frame CRC-8/CRC-16, MP3 frame validity), neither of which needs a decoder, so a failure there is proof rather than inference; it costs a full read |
| `acidcat audit FILE [--json] [--signal]` | Forensic verdict in five parts: STRUCTURE (repairable inconsistencies), HIDDEN (concealed/appended data + a carve command), FORENSICS (anomalies), INTEGRITY (fake hi-res, duration mismatch), PROVENANCE (the writing tool). `--signal` adds decoded-audio checks: is this WAV really a decoded MP3, is this stereo really dual-mono |
| `acidcat tui [FILE]` | Interactive terminal inspector: goto/search, follow pointers (`x`), byte map (`m`), edit fields, and validate (`v`, which offers repair). Graph views with `b`, scoped to the selected chunk with `r` and rescaled with `S`; a scoped graph follows the selection as you move. Focus a graph and the arrows drive it: up/down rescale, left/right walk the selection. `z` gives a pane the whole screen, `?` lists every key. Omit FILE to open the built-in file browser |
| `acidcat cover FILE [-o art.jpg] [--set img] [--remove]` | Extract, embed, or remove embedded cover art (MP3/FLAC/MP4/Ogg) |
| `acidcat explore FILE [-o out.html]` | Build a standalone interactive HTML byte-explorer (hex grid + tinted fields + LSB heat-map) |

## Common flags

Three words are reserved and mean the same thing everywhere:

| word | meaning |
|---|---|
| `--format` | the **file's** format (e.g. `inspect --format wav` to force a walker) |
| `--output-format` | how the result is **rendered** (`table` / `json` / `csv`) |
| `--encoding` | how carved **bytes** are serialized (`carve --encoding hex`) |

    --output-format FMT   table / json / csv, whichever the command supports
    --json                shorthand for --output-format json
    --csv                 shorthand for --output-format csv
    -o, --output FILE     write to a file instead of stdout
    -q, --quiet           suppress progress
    -v, --verbose         extra detail

`-f` still works as a deprecated alias for `--output-format` and warns. It was
the old spelling of `--format`, which now belongs to the file-format axis.

Rendering support varies: most commands do `table`/`json`/`csv`, `inspect` does
`table`/`json`, `dump` does `hex`/`json`. `scan` and `features` default to
`csv`, and write it to a file rather than stdout unless you ask otherwise.

Not global, though they look it: `--has` is on `scan` and `survey`; `--deep` is
on `info` and `index`; `-n/--num` defaults to 500 on `scan`/`detect`/`features`
but 5 on `similar`.

## Environment

| variable | effect |
|---|---|
| `ACIDCAT_HOME` | relocate **all** catalogue state (registry + per-library index DBs). Default `~/.acidcat/`. Set this for a scratch catalogue -- it is one variable, not a list. |
| `ACIDCAT_REGISTRY` | relocate only `registry.db`. More specific, so it wins over `ACIDCAT_HOME` for that one file; the per-library DBs still follow `ACIDCAT_HOME`. |
| `NO_COLOR` | honoured by every `--color auto` path. |

## Exit codes

Every verb answers with the same three codes, following `grep` and `diff`, so a
script can branch without knowing which verb it called:

| code | meaning | examples |
|---|---|---|
| `0` | it worked | the file is clean, the chunk is here, regions were found |
| `1` | ran fine, the answer is no | `locate` found nothing, `audit` has findings, `validate` saw a violation, `carve --chunk` is not in this file |
| `2` | could not run | bad flag or value, missing or unreadable input, or nothing in the input was checkable |

The distinction that matters in practice is `1` vs `2`. `validate` returns `2`
on a format it does not model, rather than `0`, so a gate cannot pass a file it
never examined:

    acidcat validate track.wav && ship track.wav      # only ships a checked, clean file
    acidcat locate disk.img --json | acidcat carve disk.img --batch - -o out/ \
      || echo "nothing recovered"                    # a real answer either way

A bounded run is not a failed one. When a read window, a list cap or a filter
stops a verb short, it says so **on stderr** and exits by what it actually
found. `scan DIR -n 5` that hits the cap still exits `0`; the sentence naming
the cap is on stderr so it cannot corrupt the records on stdout. Silence is the
claim of completeness: a caveat appears only when a bound was actually reached,
never as a standing note that one exists.

## Machine-readable output

`--output-format json|csv|tsv` (or `--json` / `--csv`) writes records to stdout
and everything else to stderr, so a truncated run stays parseable and a summary
sentence can never turn a document into `Extra data`.

Two compatibility rules, so scripts written against 1.0 keep working:

- **A JSON object may gain new keys in any release.** Consumers must ignore keys
  they do not recognise. Removing or renaming a key is a breaking change.
- **A top-level JSON array stays an array.** Verbs that emit a list of records
  will not be wrapped in an envelope, because `jq '.[]'` and `[0]` are the
  reason the list shape was chosen. Run-level facts that have no record to live
  on go to stderr instead.

## Dependency Groups

| Group | What it adds | Commands enabled |
|-------|-------------|-----------------|
| (none) | mutagen (base) | info, scan, chunks, survey, dump, inspect, explore, index, query, write, convert, cover for WAV/AIFF/MIDI/Serum/MP3/FLAC/OGG/Opus/M4A + all inspect-only formats |
| `[analysis]` | librosa, numpy, scipy, soundfile | detect, features, similar, `audit --signal`, info --deep |
| `[tui]` | textual | `acidcat tui` |
| `[mcp]` | mcp SDK | `acidcat-mcp` stdio server |
| `[mcp-http]` | starlette + uvicorn | `acidcat-mcp --transport http` (streamable-HTTP transport) |
| `[crypto]` | cryptography | extract audio from encrypted Wii disc images |
| `[all]` | everything (`[analysis]`, `[mcp-http]`, `[tui]`, `[crypto]`) | all commands, all formats |

## Examples

### Metadata Exploration

    # what chunks exist in your sample library?
    acidcat survey ~/Samples/Loops -n 5000

    # walk all chunks in a specific file
    acidcat chunks ~/Samples/Loops/breakbeat.wav

    # hex-dump the ACID and SMPL chunks
    acidcat dump ~/Samples/Loops/breakbeat.wav acid smpl

    # fingerprint a whole tree, then rank the rarest structural shapes
    acidcat shape ~/Samples --no-path | sort | uniq -c | sort -n

    # colored objdump-x-style hex view of a file's headers and fields
    acidcat od ~/Samples/Loops/breakbeat.wav

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

### Recovery / rescue

Find audio in a raw blob, cut it out, make it playable. The verbs chain like
coreutils: `classify` (what is this) -> `locate` (find) -> `carve` (cut) ->
`wrap` (add a header) or `convert` (transcode), with `extract` for known banks.
Full workflow in [docs/recovery.md](docs/recovery.md).

    # what am I even holding, and what should I run next
    acidcat classify mystery.bin

    # find the audio regions in a disk image or card dump
    acidcat locate disk.img --mode aggressive --analyze

    # embedded FILES: locate the regions, carve every one into a directory
    acidcat locate disk.img --json | acidcat carve disk.img --batch - -o recovered/

    # headerless PCM: carve it, then give it a header so it plays
    acidcat locate disk.img --analyze --json \
      | acidcat carve disk.img --batch - --wrap --rate 44100 -o recovered/

    # one region by hand
    acidcat carve disk.img --offset 0x5d1000 --length 2048 \
      | acidcat wrap --rate 44100 --bits 16 --endian be > region.wav

    # pull every sample out of a sampler bank / tracker module
    acidcat extract kit.sf2 -o kit_samples/

    # decode an ADPCM or mistagged WAV to plain playable PCM
    acidcat convert weird.wav --to-pcm -o plain.wav

    # CTF: find audio hidden under a reversible transform (XOR / rotate / nibble-swap)
    acidcat locate challenge.bin --transforms

### Similarity Search

Index a library with feature vectors, then find sounds like a reference:

    acidcat index ~/Samples/Loops --features       # store the vectors
    acidcat similar ~/Samples/kick.wav -n 10        # nearest neighbours

`similar` scores z-standardized cosine over the feature vectors across every
registered library, filtered to the target's kind (loop / one-shot) by default
(`--no-kind-filter` to disable). The same ranking is available to an LLM through
the MCP `find_similar` tool -- both call one core implementation, so the CLI and
MCP never drift. If the reference is not indexed, its vector is extracted live
(needs `[analysis]`).

## Libraries (per-directory indexes)

`acidcat scan` writes a one-off CSV. `acidcat index` is the persistent
path: each directory you index becomes a *library* with its own SQLite
file, and a small global registry at `~/.acidcat/registry.db` lets reads
fan out across every library you have registered.

By default the per-library DB lives centrally at
`~/.acidcat/libraries/<label>_<hash>.db`. Pass `--in-tree` if you'd
rather have the DB travel with the data at
`<library>/.acidcat/index.db`.

    # register and index a library (label defaults to basename of DIR)
    acidcat index ~/Samples/Loops --label loops
    acidcat index ~/Samples/OneShots --label oneshots

    # show every registered library
    acidcat index --list

    # per-library stats
    acidcat index --stats loops

    # extract librosa features during indexing (slower, enables similarity)
    acidcat index ~/Samples/Loops --label loops --features

    # rebuild a library's DB from scratch
    acidcat index ~/Samples/Loops --label loops --rebuild

    # forget a library (registry only) vs remove it (deletes the DB file)
    acidcat index --forget loops
    acidcat index --remove loops

    # list registered libraries whose DB file is missing on disk
    acidcat index --orphans

    # import a legacy <name>_tags.json into a library
    acidcat index ~/Samples --label samples --import-tags old_tags.json

Nested libraries are rejected at registration time: if you've registered
`~/Samples`, you can't also register `~/Samples/Loops` until you forget
the parent.

### Discovery

For users with many scattered packs, `--discover` walks a tree and
registers every qualifying subdirectory as its own library in one pass.

    # preview what would get registered (no writes)
    acidcat index --discover ~/Samples --dry-run

    # actually register them
    acidcat index --discover ~/Samples

    # tighter threshold and namespacing for a subset of your collection
    acidcat index --discover /mnt/external/old_drives \
                  --min-samples 50 --label-prefix "ext_"

A directory qualifies if its subtree (within `--max-depth`, default 3)
contains at least `--min-samples` audio files (default 20). Non-
qualifying parents are recursed into so packs nested inside catch-all
folders still surface. Already-registered roots are skipped. The home
directory is refused as a discover root to prevent runaway registration.

### Querying

By default `acidcat query` fans out across every registered library and
merges the results.

    acidcat query --bpm 120:130 --key Am
    acidcat query --tag drums --tag punchy --duration :1
    acidcat query --text "dusty lofi" --limit 20
    acidcat query --format mp3 --root loops
    acidcat query --root loops,oneshots --bpm 128
    acidcat query --bpm 128 --paths-only | xargs -I {} cp {} out/

`--root` accepts a label, an absolute path, or a comma-separated list.
Override the registry on any command with `--registry PATH` or the
`ACIDCAT_REGISTRY` environment variable.

## MCP Server

`acidcat-mcp` is a stdio MCP server that exposes the registered libraries
as structured tools. An LLM can ask "what libraries do I have?",
search across them by metadata, find compatible keys via Camelot, or
(with `[analysis]` installed) find similar samples by librosa feature
cosine.

    pip install -e .[mcp]            # minimum for discovery + writes
    pip install -e .[analysis,mcp]   # unlock find_similar / analyze_*

Claude Desktop / Claude Code config:

    {
      "mcpServers": {
        "acidcat": {
          "command": "acidcat-mcp"
        }
      }
    }

Optional: pass `--registry PATH` on the server process or set
`ACIDCAT_REGISTRY` if your registry lives outside the default location.

Tool tiers (each tool description starts with `Fast.`, `SLOW.`, or
`VERY SLOW.` so the model self-selects):

- **Fast (SQLite only)**: `search_samples`, `get_sample`, `locate_sample`,
  `list_libraries`, `list_tags`, `list_keys`, `list_formats`,
  `index_stats`, `find_compatible`
- **Slow analysis** (needs `[analysis]`): `find_similar`, `analyze_sample`,
  `detect_bpm_key`
- **Index management**: `reindex`, `reindex_features`,
  `discover_libraries`
- **Write** (marked destructive): `register_library`, `forget_library`,
  `tag_sample`, `set_sample_description`

The same tiers are on the wire as MCP annotations (`readOnlyHint`,
`destructiveHint`, `idempotentHint`) for clients that branch on them
programmatically. Most clients do not show annotations to the model, which is
why the prefix is in the prose too.

One thing worth knowing before you point an agent at it: **registering a library
does not populate it.** `register_library` and `discover_libraries` create the
row and stop; `reindex` is what walks the files. A library between those two
steps reports `sample_count: null` and answers nothing.

### The skill

`skills/acidcat/` is a Claude skill that teaches a model the above: which tool to
reach for, what each cost tier means, the register-then-reindex sequence, and
which results are lower bounds rather than totals. Install it alongside the
server, from a checkout of this repository:

    cp -r skills/acidcat ~/.claude/skills/

It is not part of the pip package -- a skill is instructions for a model, not
importable code, and shipping it inside site-packages would put it somewhere no
skill loader looks. Installing from PyPI, take it from the repository:

    mkdir -p ~/.claude/skills/acidcat
    curl -o ~/.claude/skills/acidcat/SKILL.md \
      https://raw.githubusercontent.com/hed0rah/acidcat/main/skills/acidcat/SKILL.md

Without it a model has only the tool descriptions, which cover each call in
isolation but not the order they go in. The gap is not hypothetical: an agent
given the server and no skill registered four libraries, reported success, and
left four empty shells.

## License

MIT
