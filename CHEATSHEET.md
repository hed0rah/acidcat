# acidcat cheatsheet

readelf for audio files, sample banks and synth presets. Every node and field
has an address (ADDR), and every verb takes the same one.

## verbs

| verb | does |
|---|---|
| `acidcat FILE` | the summary (`inspect --summary`): format, duration, tempo, key |
| `acidcat DIR` | one row per file over a tree (`stats --by meta`) |
| `acidcat inspect FILE...` | every node and field, with offsets and findings |
| `acidcat od FILE [ADDR...]` | annotated, coloured hex of the file or of what ADDR names |
| `acidcat carve FILE [ADDR]` | write out a node's payload, a field's value, a range or a layer |
| `acidcat probe read\|table\|scan\|find\|strings\|diff\|entropy\|map\|lsb` | byte dissection |
| `acidcat classify FILE\|DIR` | triage: what is this, and what to run next |
| `acidcat locate BLOB` | find audio in a blob or disk image (never writes) |
| `acidcat audit FILE` | forensic verdict: structure, hidden data, integrity, provenance |
| `acidcat check FILE\|DIR` | derived fields (sizes, counts, rates) against the data; `--fix` rewrites them |
| `acidcat edit FILE... --set NAME=VALUE` | tags, typed fields by ADDR, the cover; verified, with a backup |
| `acidcat stats DIR --by meta\|shape\|chunks` | per-file rows, fingerprints, or a chunk histogram over a tree |
| `acidcat analyze FILE\|DIR --bpm-key\|--features` | tempo/key or ML features from the audio (`[analysis]`) |
| `acidcat lib index\|list\|stats\|forget\|query\|similar` | the sample library index |
| `acidcat convert FILE` | bwclip to MIDI; NCW/8SVX/AU to WAV; SF2/SF3 to samples; `--to-pcm` |
| `acidcat extract BANK` | every embedded sample as a WAV; console disc and ROM soundtracks |
| `acidcat formats [FMT]` | what this build can inspect / extract / convert / repair / edit |
| `acidcat explore FILE -o out.html` | standalone HTML byte explorer |
| `acidcat tui [FILE]` | interactive inspector (`[tui]`) |

Stdin: `acidcat -`, `cat f.wav | acidcat -`, or `-` wherever a verb takes
a file. Every 1.8 verb still runs as an alias and names its 2.0 spelling on
stderr (`docs/contract/cli-2.0.md`).

## ADDR

```
RIFF/fmt_                 a node, by id (chunk names from the root, space as _)
RIFF/fmt_#sample_rate     a field of it, by key
fmt_   fmt   data         a node by last step or name, when only one node has it
RIFF/LIST[1]              the second of two same-named siblings (0-based)
RIFF/*   **/data          globs: every node that matches
IMPM/smp*#c5_speed        a field in every node a glob matches (carve)
@0x100+64                 64 bytes from 0x100
@0x100..0x200             0x100 up to 0x200
1:lh5/header#frames       a field inside decoded layer 1
```

A node means its payload. Quote brackets, globs and `#` in zsh:
`acidcat od f.wav 'RIFF/LIST[1]'`. `--at` takes search anchors instead
(`find:STR`, `find:0xHEX`, `end-N`, `chunk:ID+N`), which are not addresses.

## inspect

```
acidcat inspect FILE... [--summary|--tags|--chunks] [--hex] [-F] [--deep]
                        [--only ADDR-GLOB] [--exclude ADDR-GLOB] [--anomalies]
                        [--json] [--color auto|always|never]
                        [--force-format FMT] [--try-all] [--resync] [--region N]
```

| flag | effect |
|---|---|
| (default) | node table, decoded fields, findings |
| `--summary` | one record per file: format, duration, rate, tempo, key |
| `--tags` | decoded tags and metadata, no offsets (presets, tagged files) |
| `--chunks` | the node table only |
| `--hex` | raw bytes beside each field |
| `-F`, `--frames` | every MPEG frame, every MIDI event |
| `--deep` | the extra decoding: Bitwig device tree, Vital modulation matrix, NI compressed subtree |
| `--only RIFF/fmt_,bext` | only these nodes (ids, globs, names); no match exits 1 |
| `--exclude RIFF/data` | hide these nodes |
| `--anomalies` | forensic scan: trailing data, polyglots, cavities, size mismatches |
| `--json` | the contract v1 Document, one per file per line |
| `--force-format FMT` | parse as FMT whatever the magic says |
| `--try-all` | on a file no walker claims, run them all and report what each made of it |
| `--resync` | rebuild a damaged container by scanning for `[id][size]` records |
| `--region N` | walk the Nth region `locate` found inside a blob |
| `-q` | nothing on stderr but errors |

## recipes

```
# look
acidcat inspect --chunks loop.wav
acidcat inspect --only RIFF/fmt_ --hex loop.wav
acidcat od loop.wav RIFF/smpl
acidcat inspect --tags MyPatch.bwpreset
acidcat inspect --frames song.mp3

# one value, for a script
acidcat carve loop.wav RIFF/fmt_#sample_rate          # 44100
acidcat carve loop.wav RIFF/fmt_#sample_rate -o r.bin # its bytes: 44 ac 00 00
acidcat probe read RIFF/fmt_#sample_rate loop.wav     # typed, with its offset

# the Document, into jq
acidcat inspect --json loop.wav | jq -r '.nodes[].id'
acidcat inspect --json *.wav | jq -c '{path: .file.path, codes: [.findings[].code]}'

# over a tree
acidcat stats ~/Samples --by shape --no-path --output-format tsv | sort | uniq -c | sort -n
acidcat stats ~/Samples --by chunks
acidcat classify ~/Downloads --problems-only

# byte explorer
acidcat explore song.mp3 -o song.html

# a Bitwig clip as MIDI
acidcat convert MyClip.bwclip -o MyClip.mid
```

## edit

    acidcat edit FILE... --set NAME=VALUE [--set ...] [--unset NAME] [-o OUT] [--dry-run]

NAME is a tag (`title`, `artist`, `genre`, `bpm`, `key`, `root`, `bext_description`,
...), an ADDR naming a typed field, or `cover`. In place after a
`<name>_original` backup; `-o` writes a copy; `--dry-run` shows the change and
writes nothing. The result is re-read and verified before it is written, and
the write is atomic.

    # tags
    acidcat edit loop.wav --set title="Deep Kick" --set artist="me" --set genre=Techno
    acidcat edit loop.wav --set bpm=128 --set key=Am
    acidcat edit oneshot.wav --set root=C3
    acidcat edit field.wav --set originator="me" --set bext_description="night frogs"
    acidcat edit *.wav --set genre=Foley --dry-run
    acidcat edit Bass.vital --set name="Reese Bass" --set author=me

    # a typed field; the fields tied to it follow (--no-cascade refuses instead)
    acidcat edit loop.wav --set RIFF/fmt_#sample_rate=48000 -o loop48.wav
    #   RIFF/fmt_#sample_rate: 44100 -> 48000
    #   RIFF/fmt_#avg_bytes_per_sec: 88200 -> 96000 (follows sample_rate * block_align)

    # bytes, as hex of the field's own length
    acidcat edit loop.wav --set RIFF/fmt_#channels=hex:0200 --dry-run

    # cover art, and stripping identifying metadata
    acidcat edit track.mp3 --set cover=@art.jpg
    acidcat edit track.mp3 --get cover -o art.jpg
    acidcat edit track.mp3 --unset cover
    acidcat edit field.wav --strip -o clean.wav

## check / audit

A container is a set of derived fields (sizes, offsets, counts, pad bytes)
whose correct value is a function of the data. `check` reports the ones that
disagree; `--fix` rewrites the ones an independent witness backs. Audio is
never touched.

```
acidcat check DIR                     # sweep a tree; exit 1 if any file is broken
acidcat check --problems-only DIR     # only the broken ones
acidcat check --deep song.flac        # + the checksums the format carries
acidcat check --fix broken.wav        # keeps broken_original.wav
acidcat check --fix broken.wav --dry-run
acidcat audit suspect.wav             # STRUCTURE / HIDDEN / FORENSICS / INTEGRITY / PROVENANCE
acidcat audit suspect.wav --signal    # + decoded-audio checks ([analysis])
```

Exit codes on every verb: 0 ok, 1 the answer is no (a violation, a defect,
nothing found), 2 could not run (bad arguments, unreadable input, a format
the verb does not model).

## recovery

`locate` finds audio in a raw blob; `carve` cuts it out; `extract` unpacks a
known bank; `convert --to-pcm` makes odd codecs playable. The whole workflow
is in [docs/recovery.md](docs/recovery.md).

```
acidcat locate disk.img --mode aggressive --analyze
dd if=/dev/sdcard | acidcat locate -

# every region into a directory; headerless PCM gets a WAV header
acidcat locate disk.img --analyze --json | acidcat carve disk.img --batch - --wrap -o recovered/

# one range by hand, as raw PCM
acidcat carve disk.img @0x5d1000+2048 --as-wav --rate 44100 --byte-order be -o region.wav
cat raw.pcm | acidcat carve - --as-wav --rate 22050 --bits 8 -o raw.wav

# what an unwalked file holds past its container
acidcat carve suspect.wav --trailing -o tail.bin

acidcat extract kit.sf2 -o kit_samples/
acidcat convert weird.wav --to-pcm -o plain.wav
acidcat convert mistagged.wav --to-pcm --codec ima
acidcat locate challenge.bin --transforms            # XOR / rotate / nibble-swap
```

## reverse-engineering an unknown container

The loop that takes a file from "opaque" to a spec, then acts on it:

```
acidcat classify mystery.ch1                          # is there anything here
acidcat od mystery.ch1 @0+256                         # the header
acidcat probe entropy mystery.ch1                     # compressed, or plain?
acidcat probe read 0x07 --type u32 --byte-order le mystery.ch1           # a count?
acidcat probe read 0x0b --type u32 --byte-order le --count 8 mystery.ch1 # a table?

# which header bytes are constant across a family
for f in *.ch1; do acidcat carve "$f" @0+16 --encoding hex; done

# walk the offset table into regions and cut each one
acidcat probe --json table 0x0b mystery.ch1 \
    --count-at 0x07 --type u32 --byte-order le --base after-table \
  | acidcat carve mystery.ch1 --batch - -o frames/
```

`table` walks an offset table into regions: `--count-at` reads the entry
count out of the file, `--base after-table` handles entries relative to the
byte just past the table (`--base EXPR` for a fixed origin, or omit it when
the entries are absolute). A count read from the file is bounded by what the
file can hold, and the report says when it was clamped.

## library

```
acidcat lib index ~/samples --label samples
acidcat lib index ~/samples --features           # vectors for lib similar ([analysis])
acidcat lib index --discover ~/Samples --dry-run
acidcat lib list
acidcat lib query --bpm 120:130 --key Am
acidcat lib query --device Polysynth --category Reverb
acidcat lib query --product Vital --creator someone
acidcat lib query --compatible-with kick.wav --same-key
acidcat lib similar kick.wav --top 10
```

## python

```
import acidcat
doc = acidcat.open("loop.wav")
doc.field("RIFF/fmt_#sample_rate").value            # 44100
patch = doc.edit({"RIFF/fmt_#sample_rate": 48000}).repair()
patch.verify().commit("loop48.wav")
```

## install / upgrade

```
pipx install acidcat          # first time
pipx upgrade acidcat          # the newest (reinstall does NOT upgrade)
pip install -U acidcat        # with pip
pip install -e .              # editable, from a checkout
pip install -e .[mcp]         # + MCP stdio server (acidcat-mcp)
pip install -e .[mcp-http]    # + MCP streamable HTTP (acidcat-mcp --transport http)
pip install -e .[all]         # everything
```
