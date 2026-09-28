<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo-horizontal-dark.svg">
    <img src="docs/brand/logo-horizontal-light.svg" alt="acidcat" width="400">
  </picture>
</p>

# acidcat

A pure-Python inspector, editor and forensic tool for audio files, sample banks
and synth/DAW presets. It walks a file byte by byte into a tree of nodes and
fields, each with a stable address and the exact bytes it came from. You can
read any of it, change a field and have the fields that depend on it follow,
check and repair container structure without touching the audio, and find and
cut audio out of blobs and disk images.

Closer to readelf, 010 Editor or radare2's format layer than to exiftool. The
structural walkers need nothing beyond the standard library. The one required
dependency, mutagen, reads tags. librosa-based tempo, key and feature
analysis is an optional extra.

It also keeps per-library SQLite indexes of your samples and presets, and
ships an MCP server (`acidcat-mcp`) so a model can search them.

## Install

Python 3.11+.

    pip install acidcat              # core + mutagen, one dependency
    pip install acidcat[analysis]    # + librosa tempo/key and feature extraction
    pip install acidcat[tui]         # + the interactive terminal inspector
    pip install acidcat[mcp]         # + MCP server (acidcat-mcp, stdio)
    pip install acidcat[mcp-http]    # + MCP streamable-HTTP transport
    pip install acidcat[crypto]      # + AES for encrypted Wii disc extraction
    pip install acidcat[all]         # everything

From a checkout, swap `acidcat` for `-e .`:

    git clone https://github.com/hed0rah/acidcat.git
    cd acidcat
    pip install -e ".[all]"

Coming from 1.8? Every 1.8 command and flag still runs, prints one line on
stderr naming its 2.0 spelling, and then runs that. `CHANGELOG.md` has a
"Migrating from 1.x" section, and
[docs/contract/cli-2.0.md](docs/contract/cli-2.0.md) maps every old verb
and flag.

## Quick start

    acidcat loop.wav                          # summary: format, duration, tempo, key
    acidcat inspect loop.wav                  # every node and field, with offsets
    acidcat od loop.wav RIFF/fmt_             # the bytes of one node, annotated
    acidcat carve loop.wav RIFF/fmt_#sample_rate      # one field's value: 44100
    acidcat carve loop.wav RIFF/fmt_#sample_rate -o rate.bin   # its 4 bytes
    acidcat edit loop.wav --set RIFF/fmt_#sample_rate=48000 --dry-run
    acidcat check loop.wav                    # are the sizes and counts right?
    acidcat audit suspect.wav                 # forensic verdict
    acidcat ~/Samples/Loops                   # one row per file over a tree

    cat loop.wav | acidcat -                  # stdin works wherever a file does

## Addresses

Everything acidcat shows has an address, and every verb that takes a location
takes the same one (an ADDR, [docs/contract/node-v1.md](docs/contract/node-v1.md)
section 13):

| ADDR | names |
|---|---|
| `RIFF/fmt_` | a node, by its id: the path of chunk names from the root, a space written `_` |
| `RIFF/fmt_#sample_rate` | a field of that node, by its key |
| `fmt_`, `fmt`, `data` | a node by its last step or its name, when only one node has it |
| `RIFF/LIST[1]` | the second of two siblings with one name (repeats are indexed from 0) |
| `RIFF/*`, `**/data` | every node a glob matches (`inspect --only`, `carve GLOB#KEY`) |
| `@0x100+64`, `@0x100..0x200` | a byte range: offset and length, or start and end |
| `1:lh5/header#frames` | a field inside a decoded layer (a packed YM's unpacked tune is layer 1) |

A node means its payload: `od f.wav RIFF/data` dumps the audio bytes, not the
chunk header (`carve --raw` includes the header). In zsh, quote an ADDR that
holds `[`, `*`, `?` or `#`, or the shell treats it as a pattern:
`acidcat od f.wav 'RIFF/LIST[1]'`.

`inspect` prints the ids, so the way to find an address is to look:

    $ acidcat inspect loop.wav --only RIFF/fmt_ --hex
    loop.wav: RIFF/WAVE, 172 bytes, showing 1 of 2 chunks

      idx   id        offset      size        summary
      [ 0]  RIFF/fmt_ 0x0000000c  16          PCM 16-bit 1ch 44100 Hz

    RIFF/fmt_ @ 0x0000000c (16 bytes)
      +0x0000  01 00                      format_tag             0x0001          PCM
      +0x0002  01 00                      channels               1
      +0x0004  44 ac 00 00                sample_rate            44100           Hz
      ...

## Commands

Seventeen verbs. `acidcat VERB --help` has the rest.

| Verb | Does |
|---|---|
| `inspect FILE...` | The structural dump, for the 88 formats `acidcat formats` lists: audio, sampler banks, trackers, synth presets, console streams, disc images. Nodes, fields, offsets and findings. `--summary` one record per file (format, duration, tempo, key); `--tags` the decoded tags without offsets; `--chunks` the node table alone; `--hex` bytes beside each field; `--only/--exclude ADDR-GLOB`; `-F/--frames` every MPEG frame or MIDI event; `--deep` the walkers' extra decoding (Bitwig device tree, Vital modulation matrix, NI compressed subtree); `--anomalies` the forensic scan; `--json` the contract v1 Document |
| `od FILE [ADDR...]` | Annotated, coloured hex: the whole file by its structure, or the nodes, fields and ranges you name. `--at` for a search anchor (`find:STR`, `end-N`), `--region N` for a `locate` region, `--marks` to tint an unwalked dump |
| `carve FILE [ADDR]` | Write out what an ADDR names: a node's payload (`--raw` with its header), a field (its value on the terminal, its bytes with `-o` or `--encoding raw`), a byte range, or a decoded layer (`--layer N`). `--type`/`--struct` decode typed values at any offset; `--trailing` is everything past the container; `--batch -` cuts every region `locate` found; `--as-wav` wraps raw PCM in a WAV header |
| `probe read\|table\|scan\|find\|strings\|diff\|entropy\|map\|lsb` | Byte dissection: a typed read at an ADDR, an offset table walked into regions, value scan, pattern find, strings, binary diff, Shannon entropy, a Hilbert byte map, sample-LSB entropy |
| `classify FILE\|DIR` | Triage: a single file acidcat walks, a container of files, a chunked but unknown format, damaged remains, or not audio. Each verdict names the command to run next |
| `locate BLOB` | Find audio in a blob or disk image: embedded containers, headerless PCM, headerless MP3. `--analyze` infers PCM geometry, `--transforms` finds audio under XOR, rotate or nibble-swap. Never writes |
| `audit FILE` | A forensic verdict in five parts: STRUCTURE, HIDDEN (appended or concealed data, with a carve command), FORENSICS, INTEGRITY (fake hi-res, duration mismatch), PROVENANCE (the writing tool). `--signal` adds decoded-audio checks (a WAV that is really a decoded MP3, stereo that is really dual mono) |
| `check FILE\|DIR` | Recompute every derived field (sizes, offsets, counts, pad bytes, rates) and report the ones that disagree with the data. `--fix` rewrites the ones it can witness, never touching audio, with a `_original` backup; `--deep` also verifies the checksums a format carries (FLAC frame CRCs, MP3 frame validity) |
| `edit FILE... --set NAME=VALUE` | Change a tag (`title`, `bpm`, `key`, `root`), a typed field by ADDR (`RIFF/fmt_#sample_rate=48000`, bytes as `hex:0100`), or the cover (`cover=@art.jpg`). The fields an edit ties to follow it (a WAV's `avg_bytes_per_sec` after its `sample_rate`; `--no-cascade` refuses instead). The result is verified by re-reading it before anything is written. `--unset`, `--get cover -o art.jpg`, `--strip`, `--dry-run`, `-o COPY` |
| `stats DIR... --by meta\|shape\|chunks` | Over a tree: one row per file of format, tempo, key and duration (`meta`, the default); one structural fingerprint per file (`shape`, built for `sort \| uniq -c`); or a chunk-id histogram with the rare ones flagged (`chunks`). Stops at 10,000 files unless `--max-files` says otherwise |
| `analyze FILE\|DIR --bpm-key\|--features` | Estimate tempo and key from the audio, or extract 50+ features for ML. Needs `[analysis]` |
| `lib index\|list\|stats\|forget\|query\|similar` | The sample library index (below) |
| `convert FILE` | Bitwig clip to MIDI; NCW, 8SVX and AU to WAV; SF2/SF3 to a folder of samples; `--to-pcm` decodes ADPCM or a mistagged WAV to plain 16-bit PCM |
| `extract BANK` | Every embedded sample of a bank or module as its own WAV (MOD/XM/IT/S3M, Gravis `.pat`, 8SVX, NCW, SF2/SF3, Bitwig `.multisample`, Kurzweil, E-mu, MPC), and the soundtracks off console discs and ROMs (PlayStation/CD-XA, CD-DA `.cue`, GameCube, Wii with `[crypto]`, N64 VADPCM, SNES BRR) |
| `formats [FMT]` | The capability matrix: which formats acidcat can inspect, extract, convert, repair and edit. The quickest answer to "can it read my files?" |
| `explore FILE -o out.html` | A standalone HTML byte explorer: hex grid, each field tinted over its bytes, an LSB heat map |
| `tui [FILE]` | The interactive inspector (needs `[tui]`, below) |

`acidcat FILE` is `acidcat inspect --summary FILE`, and `acidcat DIR` is
`acidcat stats DIR --by meta`.

## Format anatomy

Interactive datasheets for the formats acidcat dissects, each drawn byte by byte:
**hover** a field to light its exact bytes and read the decode, **click** a field
for its table. The RIFF/WAVE family, MP3, FLAC, Ogg, MP4, MIDI, the trackers, the
sampler and synth-preset formats, and more, with the history and edge-case notes
behind each.

**Browse them at [hed0rah.github.io/audio_files_anatomy](https://hed0rah.github.io/audio_files_anatomy/)**.
A good start is the [WAV / RIFF page](https://hed0rah.github.io/audio_files_anatomy/wav-anatomy.html),
which walks the container, the `fmt`/`data`/`smpl`/`acid` chunks byte by byte, and
the whole RIFF to BWF to RF64 to Wave64 family.

## Standard flags

The same on every verb that has the behaviour:

| Flag | Meaning |
|---|---|
| `--output-format table\|json\|csv\|tsv` | how the result is rendered; `table` by default everywhere |
| `--json`, `--csv` | shorthands for the above |
| `-o/--output PATH` | write the report here; on `carve`, `convert`, `extract` and `edit`, what they write |
| `-q/--quiet` | nothing on stderr but errors; never changes stdout |
| `-v/--verbose` | diagnostic lines on stderr; never changes stdout |
| `--color auto\|always\|never` | `auto` colours only on a terminal and honours `NO_COLOR` |
| `--deep` | the extra decoding work: frames, checksums, compressed subtrees |
| `--force-format FMT` | parse as FMT whatever the magic says (`acidcat formats` lists the ids) |
| `--only-format FMT` | keep only targets of that format |
| `--max-files N`, `--top N` | stop after N files; keep the first N results |
| `--byte-order be\|le\|both` | the byte order of a typed read or of raw PCM |

## JSON

`--json` writes records to stdout and everything else to stderr, so a bounded
run stays parseable. One rule holds on every verb
([cli-2.0.md](docs/contract/cli-2.0.md) section 4.1):

- keys are snake_case and name what the value is;
- a file is `path`, as you gave it (`<stdin>` for `-`);
- a format is `format`, the registry id (`wav`), with `label` beside it
  (`RIFF/WAVE`), both null when nothing recognises the file;
- `inspect --json` writes one contract v1 Document per file, one per line;
  a verb with rows writes one array; a verb with one report writes one object.

A JSON object may gain keys in any release, so ignore the ones you do not
know. Removing or renaming a key, or wrapping an array in an object, is a
breaking change.

**The Document** (`inspect --json`, `acidcat.open()`) is specified in
[docs/contract/node-v1.md](docs/contract/node-v1.md), with a JSON Schema
beside it. Each node has an `id` (its ADDR), a `name`, an `extent` and a
`payload` (`{layer, off, len}`) and its `fields`. Each field has a `key`, the
machine `value`, the `display` string, and `at`, the bytes it was read from.
A `value` is never formatted text: `"44,100 Hz"` is 44100, `"0.008 s"` is
0.008, `"1:00"` in a length is 60 (numbers in seconds, Hz and bits per
second).
Findings (`kind`, `code`, `severity`, `node`) are one list, the walker's
and the forensic scan's together. `limits` records what the walk ran under
and which caps it hit. `layers` lists the decoded images. `typing` says
which fields were read from the file and which were inferred.

    acidcat inspect --json loop.wav | jq -r '.nodes[] | .id'
    acidcat inspect --json *.wav | jq -c '{path: .file.path, findings: [.findings[].code]}'

## Exit codes

Every verb answers with the same three codes, following `grep` and `diff`:

| Code | Meaning | Examples |
|---|---|---|
| `0` | it worked | the file is clean, the node is there, regions were found |
| `1` | it ran, and the answer is no | `check` found a violation, `audit` found a defect, `locate` found nothing, `stats` found nothing its mode reads, an ADDR names no node |
| `2` | it could not run | a bad flag or value, unreadable input, a file no walker reads (or that `edit`, `extract` or `convert` has nothing for), a missing extra |

`coverage`, `environment` and `info` findings never make an exit 1; only a
`defect` does. `check` exits 2 on a format it does not model, so a gate cannot
pass a file it never examined:

    acidcat check track.wav && ship track.wav     # ships only a checked, clean file

A bounded run is not a failed one. When a cap stops a verb short, it says so
on stderr, naming the flag that lifts it, and exits on what it found.

## Python

    import acidcat

    doc = acidcat.open("loop.wav")                 # a path, bytes or a Source
    doc.format                                     # Format('wav', 'RIFF/WAVE')
    doc.field("RIFF/fmt_#sample_rate").value       # 44100
    [n.id for n in doc.walk()]                     # ['RIFF', 'RIFF/fmt_', 'RIFF/data']
    doc.read("RIFF/fmt_#sample_rate")              # b'D\xac\x00\x00'
    [f.code for f in doc.findings]

    patch = doc.edit({"RIFF/fmt_#sample_rate": 48000}).repair()
    patch.repairs      # [Repair(addr='RIFF/fmt_#avg_bytes_per_sec', old=88200, new=96000, ...)]
    patch.verify()     # re-reads the result; PatchError if it does not hold
    patch.commit("loop48.wav")                     # or commit() in place, with a backup

`doc.to_json()` is the Document dict. `acidcat.open()` raises `Unsupported`
for a file no walker claims. An ADDR that names nothing raises `AddrError`,
and one that names several lists them in its `candidates`. `acidcat.walk()` and
`acidcat.walk_file()`, the 1.x tuple API, still work with a
`DeprecationWarning` and go in 3.0.

## Examples

### Look inside

    # the chunk table, then the bytes of one chunk
    acidcat inspect --chunks breakbeat.wav
    acidcat od breakbeat.wav RIFF/smpl

    # the tags of a preset, without offsets
    acidcat inspect --tags MyPatch.bwpreset

    # per-frame MP3 bitrate switching, per-event MIDI
    acidcat inspect --frames song.mp3

    # one field of every node a glob matches: each sample's C5 speed
    acidcat carve song.it 'IMPM/smp*#c5_speed'

    # fingerprint a tree, then rank the rarest structural shapes
    acidcat stats ~/Samples --by shape --no-path --output-format tsv | sort | uniq -c | sort -n

    # which chunks does a library use, and which are rare
    acidcat stats ~/Samples/Loops --by chunks

### Edit

    acidcat edit loop.wav --set title="Deep Kick" --set genre=Techno
    acidcat edit loop.wav --set bpm=128 --set key=Am
    acidcat edit oneshot.wav --set root=C3
    acidcat edit *.wav --set genre=Foley --dry-run
    acidcat edit loop.wav --set RIFF/fmt_#sample_rate=48000 -o loop48.wav
    acidcat edit track.mp3 --set cover=@art.jpg
    acidcat edit track.mp3 --get cover -o art.jpg
    acidcat edit field.wav --strip -o clean.wav

In place by default, after a `<name>_original` backup; `-o` writes a copy.
The write is atomic, and the result is re-read and verified first.

### Check and repair

A container is a set of derived fields (sizes, offsets, counts, pad bytes)
whose correct value is a function of the data. `check` reports the ones that
do not match; `--fix` rewrites the ones an independent witness backs, and
leaves the audio alone.

    acidcat check ~/Samples                  # sweep a tree; exit 1 if any file is broken
    acidcat check --fix broken.wav --dry-run
    acidcat check --fix broken.wav           # keeps broken_original.wav
    acidcat audit suspect.wav --json

### Recovery

Find audio in a raw blob, cut it out, make it play. The verbs chain like
coreutils: `classify` (what is this), `locate` (where is the audio), `carve`
(cut it out, `--as-wav` to give raw PCM a header), `extract` for known banks.
The whole workflow is in [docs/recovery.md](docs/recovery.md).

    acidcat classify mystery.bin
    acidcat locate disk.img --mode aggressive --analyze

    # every region into a directory, headerless PCM given a WAV header
    acidcat locate disk.img --analyze --json \
      | acidcat carve disk.img --batch - --wrap -o recovered/

    # one range by hand, as 16-bit big-endian PCM
    acidcat carve disk.img @0x5d1000+2048 --as-wav --rate 44100 --byte-order be -o region.wav

    # every sample out of a bank or module
    acidcat extract kit.sf2 -o kit_samples/

    # audio hidden under a reversible transform (XOR, rotate, nibble-swap)
    acidcat locate challenge.bin --transforms

### Analysis

    acidcat analyze ~/Samples/OneShots --bpm-key     # needs [analysis]
    acidcat analyze ~/Samples/Loops --features --csv -o features.csv

## Libraries

Each directory you index becomes a *library* with its own SQLite file, and a
small registry at `~/.acidcat/registry.db` lets a query fan out across all of
them. By default a library's DB lives at `~/.acidcat/libraries/<label>_<hash>.db`;
`--in-tree` keeps it at `<library>/.acidcat/index.db` instead.

    acidcat lib index ~/Samples/Loops --label loops
    acidcat lib index ~/Samples/Loops --features      # vectors for lib similar
    acidcat lib index --discover ~/Samples --dry-run   # one library per pack
    acidcat lib list
    acidcat lib stats loops
    acidcat lib forget loops                           # --delete-db removes the file too

    acidcat lib query --bpm 120:130 --key Am
    acidcat lib query --tag drums --duration :1
    acidcat lib query --text "dusty lofi" --top 20
    acidcat lib query --device Polysynth --category Reverb
    acidcat lib query --compatible-with kick.wav       # Camelot key + tempo, half/double time
    acidcat lib query --bpm 128 --paths-only | xargs -I {} cp {} out/
    acidcat lib similar kick.wav --top 10              # nearest by audio features

Nested libraries are refused: with `~/Samples` registered, `~/Samples/Loops`
cannot be until the parent is forgotten. `--discover` registers each
subdirectory holding at least `--min-samples` audio files (default 20,
within `--max-depth`, default 3), and refuses your home directory as a root.
`lib similar` scores z-standardized cosine over the feature vectors, filtered
to the reference's kind (loop or one-shot) unless `--no-kind-filter`; the MCP
`find_similar` tool calls the same code.

## The TUI

`acidcat tui FILE` (or `acidcat tui` for a file browser) opens the Document
in two panes: the node tree on the left, the bytes on the right, a data
inspector reading the selected bytes as integers and floats of each width
and byte order, and a status line naming the layer, the selected node's id
and what that node can do. `?` lists every key; the ones to know:

| Key | Does |
|---|---|
| `g`, `/` | go to an offset; search names, values or bytes |
| `e` | edit the selected field (the fields it ties to follow) |
| `x`, `enter` | follow a pointer field; open a decoded layer |
| `u`, `U` | back and forward through what you followed or opened |
| `f` | next finding |
| `p`, `.` | play the selected audio node through ffplay, when its caps say it plays; stop |
| `X` | write out the selected node, or the marked `locate` regions |
| `b`, `m` | cycle the byte pane (hex, entropy, Hilbert map, histogram); the byte map |
| `v` | check the file (and offer the fix) |
| `ctrl+s`, `ctrl+z` | save with a backup; undo |
| `z`, `tab` | zoom a pane; move between panes |

## Environment

| Variable | Effect |
|---|---|
| `ACIDCAT_HOME` | relocate all catalogue state (registry and per-library DBs); default `~/.acidcat/` |
| `ACIDCAT_REGISTRY` | relocate only `registry.db`; wins over `ACIDCAT_HOME` for that file |
| `NO_COLOR` | honoured by every `--color auto` |

## Dependency groups

| Group | Adds | Enables |
|---|---|---|
| (none) | mutagen | every verb but those below, for every format |
| `[analysis]` | librosa, numpy, scipy, soundfile | `analyze`, `lib similar`, `lib index --features/--analyze`, `audit --signal` |
| `[tui]` | textual | `acidcat tui` |
| `[mcp]` | the MCP SDK | `acidcat-mcp` over stdio |
| `[mcp-http]` | starlette, uvicorn | `acidcat-mcp --transport http` |
| `[crypto]` | cryptography | audio from encrypted Wii disc images |
| `[all]` | all of the above | |

acidcat-lab, the adversarial half (it builds polyglots, cavities and
LSB-stego specimens rather than reading them), is a separate distribution in
`lab/`.

## MCP server

`acidcat-mcp` exposes the registered libraries as tools, so a model can ask
what libraries you have, search them by metadata, find compatible keys, or
(with `[analysis]`) find similar samples.

    pip install acidcat[mcp]            # discovery, search, writes
    pip install acidcat[analysis,mcp]   # + find_similar, analyze_sample, detect_bpm_key

Claude Desktop / Claude Code config:

    {
      "mcpServers": {
        "acidcat": {
          "command": "acidcat-mcp"
        }
      }
    }

Pass `--registry PATH` or set `ACIDCAT_REGISTRY` for a registry outside the
default location. `acidcat-mcp --transport http --port 8765` serves
streamable HTTP; it has no authentication, so keep it on localhost.

Every tool description starts with its cost, `Fast.`, `SLOW.` or
`VERY SLOW.`, so the model can choose:

- **Fast (SQLite only)**: `search_samples`, `get_sample`, `locate_sample`,
  `list_libraries`, `list_tags`, `list_keys`, `list_formats`,
  `index_stats`, `find_compatible`
- **Slow analysis** (needs `[analysis]`): `find_similar`, `analyze_sample`,
  `detect_bpm_key`
- **Index management**: `reindex`, `reindex_features`, `discover_libraries`
- **Write** (marked destructive): `register_library`, `forget_library`,
  `tag_sample`, `set_sample_description`

The same tiers are on the wire as MCP annotations (`readOnlyHint`,
`destructiveHint`, `idempotentHint`). Most clients do not show annotations to
the model, which is why the prefix is in the prose too.

One thing to know before you point an agent at it: **registering a library
does not populate it.** `register_library` and `discover_libraries` create the
row and stop; `reindex` walks the files. Between the two, a library reports
`sample_count: null` and answers nothing.

### The skill

`skills/acidcat/` is a Claude skill that teaches a model the above: which
verb or tool to reach for, what each cost tier means, the register-then-reindex
sequence, and which results are lower bounds rather than totals. Install it
alongside the server, from a checkout:

    cp -r skills/acidcat ~/.claude/skills/

It is not in the pip package: a skill is instructions for a model, and
site-packages is not where a skill loader looks. Installed from PyPI, take it
from the repository:

    mkdir -p ~/.claude/skills/acidcat
    curl -o ~/.claude/skills/acidcat/SKILL.md \
      https://raw.githubusercontent.com/hed0rah/acidcat/main/skills/acidcat/SKILL.md

Without it a model has only the tool descriptions, which cover each call on
its own but not the order they go in. An agent given the server and no skill
registered four libraries, reported success, and left four empty shells.

## License

MIT
