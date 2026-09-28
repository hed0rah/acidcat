# acidcat 2.0: the CLI, old to new

Every command and flag acidcat 1.8 accepts, what it becomes in 2.0, and
whether the old spelling keeps working. Built in 2.0.0a1 (section 5 says where
the build refines it). `tests/test_cli_mapping.py` runs the old spellings
through their aliases and holds each to its new form's exact output, and fails
when a 2.0 flag is not named here.

## 1. Rules

- **Seventeen verbs replace twenty-nine.** architecture-2.0.md section 8 says
  fifteen, but its own table lists seventeen (`convert` and `extract` share a
  row, and `formats`, `explore` and `tui` are counted separately); this page
  follows the table.
- **Aliases through 2.x.** An old verb or flag marked *alias* keeps working for
  every 2.x release: it prints one line to stderr naming the new spelling, then
  runs exactly the new form. Aliases are removed in 3.0. A flag already
  deprecated in 1.x (`-f`, `--no-color`, `carve --format`, `formats
  --format-out`) is removed in 2.0; that is marked *removed*.
- **Standard flags**, the same on every verb that has the behaviour:

  | Flag | Meaning |
  |---|---|
  | `--json` | shorthand for `--output-format json` |
  | `--csv` | shorthand for `--output-format csv`, where the verb has rows |
  | `--output-format table\|json\|csv\|tsv` | default `table` on every verb (1.8 defaults `scan` and `features` to csv and `shape` to tsv) |
  | `--color auto\|always\|never` | every verb whose output has colour: `inspect`, `od`, `classify`, `probe map` |
  | `-o/--output PATH` | write here instead of stdout: on every verb with a report (`od`, `probe`, `classify`, `locate`, `audit`, `formats` and `lib list`/`stats` included); on `carve`, `convert`, `extract` and `edit` it names what they write |
  | `-q/--quiet` | drop progress, notes and summary lines on stderr (errors stay); never changes stdout. On every verb that prints any |
  | `-v/--verbose` | add diagnostic lines on stderr; never changes stdout |
  | `--max-files N` | stop after N files (replaces `-n/--num` where it counted files, and `census --limit`) |
  | `--top N` | keep the first N results (replaces `similar -n`, `query --limit`) |
  | `--byte-order be\|le\|both` | replaces `carve --endian`, `probe --be/--le`, `wrap --endian` |
  | `--only-format FMT` | filter targets by format (replaces `shape --format`, `query --format`) |
  | `--force-format FMT` | parse as FMT whatever the magic says (replaces `inspect --format`) |
  | `--deep` | `Limits(decode=True)`: do the extra decoding work (frames, checksums, compressed subtrees) |

- **`--deep` means one thing.** In 1.8 it meant "decode more" on `validate`
  and `index`, and "run librosa" on `info`. The librosa meaning moves to
  `analyze`.
- **`-v` never changes stdout.** In 1.8 `inspect -v` was a synonym for
  `--frames` and `info -v` showed more fields; both move to real flags.
- **Addresses.** Every place a location is taken accepts an ADDR (node-v1.md
  section 13): `RIFF/fmt_`, `RIFF/fmt_#sample_rate`, `@0x100+64`,
  `@0x100..0x200`, `1:lh5/header#frames`. `--at` keeps its search anchors
  (`end[-N]`, `find:STR`, `find:0xHEX`), which are not addresses.
  In zsh (and bash with `extglob`), quote an ADDR that holds `[`, `*`, `?`
  or `#`, or the shell takes it as a pattern: `acidcat od f.wav 'RIFF/LIST[1]'`.
- **Exit codes**: 0 ok, 1 the answer is no (a defect finding, a failed check,
  nothing matched), 2 could not run (bad arguments, unreadable input, a missing
  extra). `coverage`, `environment` and `info` findings never exit 1.
  A file no walker reads is could-not-run for the verbs that need a walker
  (`inspect`, `audit`, `check`: 2), as is a file a verb has no editor,
  extractor or converter for (`edit`, `extract`, `convert`: 2), and so is a bad argument value
  (`formats nope`, `inspect --force-format nope`: 2). Two verbs answer it
  instead: `classify`'s job is to say what a file is, and "opaque, no walker"
  is its answer (1, so `classify f && inspect f` stops there); `inspect
  --try-all` runs every walker and reports what each made of it (1: still
  unidentified, but the report is the answer).
- `acidcat --version` is unchanged.

## 2. Verbs

| 1.8 | 2.0 | |
|---|---|---|
| `inspect` | `inspect` | |
| `info` | `inspect --summary` | alias |
| `chunks` | `inspect --chunks` | alias |
| `od` | `od FILE [ADDR]` | |
| `dump` | `od FILE ADDR` (view), `carve FILE ADDR -o` (write) | alias |
| `probe hexdump` | `od FILE ADDR` | alias |
| `carve` | `carve FILE ADDR` | |
| `wrap` | `carve FILE [ADDR] --as-wav` | alias |
| `probe` | `probe` | |
| `classify` | `classify` | |
| `locate` | `locate` | |
| `audit` | `audit` | |
| `validate` | `check` | alias |
| `repair` | `check --fix` | alias |
| `write` | `edit` | alias |
| `cover` | `edit --set cover=@IMG`, `edit --unset cover`, `carve FILE cover` | alias |
| `scan` | `stats DIR --by meta` | alias |
| `shape` | `stats DIR --by shape` | alias |
| `survey` | `stats DIR --by chunks` | alias |
| `census` | `stats DIR --by chunks` (census's parallel engine serves every `--by`) | alias |
| `detect` | `analyze --bpm-key` | alias |
| `features` | `analyze --features` | alias |
| `index` | `lib index`, `lib list`, `lib stats`, `lib forget` | alias |
| `query` | `lib query` | alias |
| `similar` | `lib similar` | alias |
| `convert` | `convert` | |
| `extract` | `extract` | |
| `formats` | `formats` | |
| `explore` | `explore` | |
| `tui` | `tui` | |

## 3. Flags, verb by verb

Every flag of every 1.8 verb. "same" means the flag is unchanged.

### `inspect`

| 1.8 | 2.0 | |
|---|---|---|
| `--hex` | same | |
| `--output-format`, `--json` | same | |
| `-f` | `--output-format` | removed |
| `-q`, `--quiet` | `--chunks`: the chunk table only. `-q` keeps the standard meaning, nothing on stderr but errors, so a 1.8 `inspect -q` prints the field detail too | changed |
| `--pretty` | `--summary` (the `info` view and the decoded-tag view become one) | alias |
| `-F`, `--frames` | same; rows capped by `frame_rows` | |
| `-v`, `--verbose` | `--deep` for the deep pass; `-v` becomes stderr diagnostics | alias for `--deep` through 2.x |
| `--only` | `--only ADDR-GLOB` (`RIFF/*`, `**/data`); a bare id still matches | |
| `--exclude` | `--exclude ADDR-GLOB` | |
| `--full` | `--json` (the v1 Document carries every position the explorer needs) | alias |
| `--anomalies` | same; its results are findings in the Document | |
| `--color` | same | |
| `--sandbox`, `--sandbox-profile`, `--sandbox-mem`, `--sandbox-timeout` | same | |
| `--format` | `--force-format` | alias |
| `--force` | `--try-all` (`--force` means "overwrite" on every other verb) | alias |
| `--resync` | same | |
| `--offset`, `--length`, `--end` | `--at @OFF+LEN` / `--at @OFF..END` | alias |
| `--at` | `--at ADDR` or a search anchor | |
| `--region` | same | |

### `info`

`inspect --summary FILE...`.

| 1.8 | 2.0 | |
|---|---|---|
| `--output-format`, `--json`, `--csv` | same | |
| `-f` | `--output-format` | removed |
| `--deep` | `analyze --bpm-key --features` (librosa); the `info --deep` alias prints both | alias |
| `-q`, `--quiet` | same | |
| `-v`, `--verbose` | `inspect` without `--summary` (every field) | alias |
| `-o`, `--output` | same | |

### `chunks`

`inspect --chunks FILE...`.

| 1.8 | 2.0 | |
|---|---|---|
| `--output-format`, `--json`, `--csv` | same | |
| `-f` | `--output-format` | removed |
| `-o`, `--output` | same | |
| `-q`, `--quiet` | same | |
| `-v`, `--verbose` | same (stderr diagnostics) | |

### `od`

| 1.8 | 2.0 | |
|---|---|---|
| `--color` | same | |
| `--width` | same | |
| `--offset`, `--length`, `--end` | positional ADDR: `@OFF+LEN`, `@OFF..END` | alias |
| `--at` | positional ADDR, or `--at` for a search anchor | |
| `--region` | same | |
| `--marks` | same | |

### `dump`

`dump FILE ID...` becomes `od FILE ADDR` per chunk (`od f.wav RIFF/smpl`).

| 1.8 | 2.0 | |
|---|---|---|
| `-b`, `--bytes` | `od FILE ADDR+N` | alias |
| `--output-format`, `--json` | `od --json` | alias |
| `-f` | `--output-format` | removed |
| `--write` | `carve FILE ADDR -o DIR/` per chunk | alias |
| `-q`, `--quiet` | same | |
| `-v`, `--verbose` | same | |

### `carve`

`carve FILE ADDR`.

| 1.8 | 2.0 | |
|---|---|---|
| `--offset`, `--length`, `--end` | ADDR: `@OFF+LEN`, `@OFF..END` | alias |
| `--at` | ADDR, or `--at` for a search anchor | |
| `--trailing` | same | |
| `--chunk` | ADDR naming the node (`RIFF/data`) | alias |
| `--raw` | same: include the node's header | |
| `--type`, `--count`, `--struct` | same | |
| `--endian` | `--byte-order` | alias |
| `--field` | ADDR naming the field (`RIFF/fmt_#sample_rate`) | alias |
| `--encoding` | same | |
| `--format` | `--encoding` | removed |
| `--batch`, `--wrap`, `--rate` | same | |
| `-o`, `--output` | same | |
| `-q`, `--quiet` | same | |
| `--layer` | same (added in 2.0.0a1): writes a decoded layer's bytes, `0` the file | |
| (new) | `--as-wav` wraps raw PCM (from `wrap`) | |

### `wrap`

`carve FILE [ADDR] --as-wav` (FILE may be `-`).

| 1.8 | 2.0 | |
|---|---|---|
| `-o`, `--output` | same | |
| `--rate`, `--channels`, `--bits`, `--float` | same, with `--as-wav` | |
| `--endian` | `--byte-order` | alias |

### `probe`

`read`, `table` and `scan` take an ADDR where 1.8 took a chunk id or
`chunk.field` (which still works); a node means its payload, where 1.8's
chunk id gave its header's offset. An address in a decoded layer is refused
with a pointer to `od` and `carve`, which read layers.

| 1.8 | 2.0 | |
|---|---|---|
| `AT`, `--count-at`, `--base`, `--end`: `chunk`, `chunk.field` | an ADDR (`RIFF/fmt_#sample_rate`, `@0x2c+4`); `chunk.field` still resolves | |
| `--output-format`, `--json` | same | |
| `-f` | `--output-format` | removed |
| `table`: `--type`, `-t`, `--count`, `-n`, `--count-at`, `--count-type`, `--base`, `--end` | same | |
| `table`, `read`: `--be`, `--le` | `--byte-order be\|le` | alias |
| `read`: `--type`, `-t`, `--count`, `-n` | same | |
| `scan`: `--type`, `-t` | same | |
| `find`, `diff` | same | |
| `strings`: `--min`, `-m` | same | |
| `hexdump`: `--len`, `-l` | `od FILE ADDR+LEN` | alias |
| `entropy`, `lsb`: `--width`, `-w` | same | |
| `map`: `--order`, `--color` | same | |
| `map`: `--no-color` | `--color never` | removed |

### `classify`

| 1.8 | 2.0 | |
|---|---|---|
| `--shallow` | same | |
| `--output-format`, `--json`, `--csv` | same | |
| `-f` | `--output-format` | removed |
| `--color` | same | |
| `-q`, `--quiet` | same | |

### `locate`

| 1.8 | 2.0 | |
|---|---|---|
| `--mode`, `--analyze`, `--transforms`, `--min-confidence` | same | |
| `--output-format`, `--json`, `--csv` | same | |
| `-f` | `--output-format` | removed |
| `-v`, `--verbose` | same | |
| `-q`, `--quiet` | same | |

### `audit`

Exit 1 counts `defect` findings only.

| 1.8 | 2.0 | |
|---|---|---|
| `--output-format`, `--json` | same | |
| `-f` | `--output-format` | removed |
| `--signal` | same | |

### `validate`

`check FILE...`.

| 1.8 | 2.0 | |
|---|---|---|
| `--deep` | same meaning (verify the checksums a format carries): `Limits(decode=True)` | |
| `-q`, `--quiet` | `--problems-only` (`-q` never changes stdout) | alias |
| `--output-format`, `--json`, `--csv` | same | |
| `-f` | `--output-format` | removed |

### `repair`

`check --fix FILE...`.

| 1.8 | 2.0 | |
|---|---|---|
| `-o`, `--output` | same | |
| `--dry-run`, `--overwrite`, `--keep-pad` | same | |
| `--output-format`, `--json`, `--csv` | same | |
| `-f` | `--output-format` | removed |

### `write`

`edit FILE... --set NAME=VALUE`, where NAME is a metadata-profile field (as
today) or an ADDR naming a typed field.

| 1.8 | 2.0 | |
|---|---|---|
| `--set` | same | |
| `-o`, `--output` | same | |
| `--dry-run`, `--overwrite` | same | |
| `--strip` | same | |
| `--output-format`, `--json`, `--csv` | same | |
| `-f` | `--output-format` | removed |

### `cover`

| 1.8 | 2.0 | |
|---|---|---|
| `-o`, `--output` | `carve FILE cover -o PATH` | alias |
| `--set` | `edit FILE --set cover=@IMG` | alias |
| `--remove` | `edit FILE --unset cover` | alias |
| `--overwrite` | same, on `edit` | |

### `scan`

`stats DIR --by meta`: one row per file, as today.

| 1.8 | 2.0 | |
|---|---|---|
| `-o`, `--output` | same | |
| `-n`, `--num` | `--max-files` (the 500 default goes: `stats` reads the whole tree unless told) | alias |
| `-q`, `--quiet` | same | |
| `-v`, `--verbose` | same | |
| `--output-format`, `--json`, `--csv` | same; the default becomes table | |
| `-f` | `--output-format` | removed |
| `--has` | same | |
| `--fallback` | `analyze --bpm-key` | alias |
| `--features` | `analyze --features` | alias |

### `shape`

`stats TARGET... --by shape`.

| 1.8 | 2.0 | |
|---|---|---|
| `--no-path`, `--coarse`, `--fast`, `--anomalies`, `--warn-only` | same | |
| `--format` | `--only-format` | alias |
| `--output-format`, `--json`, `--csv` | same; the default becomes table (`--output-format tsv` for `sort \| uniq -c`) | |
| `-f` | `--output-format` | removed |

### `survey`

`stats DIR --by chunks`.

| 1.8 | 2.0 | |
|---|---|---|
| `-n`, `--num` | `--max-files` | alias |
| `-q`, `--quiet` | same | |
| `--has`, `--examples` | same | |
| `--output-format`, `--json`, `--csv` | same | |
| `-f` | `--output-format` | removed |
| `-o`, `--output` | same | |

### `census`

`stats TARGET... --by chunks`, with the open-question flags.

| 1.8 | 2.0 | |
|---|---|---|
| `--output-format`, `--json` | same | |
| `-f` | `--output-format` | removed |
| `-o`, `--output` | same | |
| `--limit` | `--max-files` (`--limit` is the Limits flag) | alias |
| `--top` | same | |
| `--jobs`, `--io-hint`, `--follow-symlinks`, `--one-file-system`, `--noatime`, `--no-fadvise` | same, for every `stats --by` | |
| `-q`, `--quiet` | same | |

### `detect`

`analyze --bpm-key TARGET` (needs the `analysis` extra; exit 2 without it).

| 1.8 | 2.0 | |
|---|---|---|
| `-n`, `--num` | `--max-files` | alias |
| `-q`, `--quiet` | same | |
| `--output-format`, `--json`, `--csv` | same | |
| `-f` | `--output-format` | removed |
| `-o`, `--output` | same | |

### `features`

`analyze --features TARGET`.

| 1.8 | 2.0 | |
|---|---|---|
| `-n`, `--num` | `--max-files` | alias |
| `-q`, `--quiet` | same | |
| `--output-format`, `--json`, `--csv` | same; the default becomes table | |
| `-f` | `--output-format` | removed |
| `-o`, `--output` | same | |

### `index`

The action flags become `lib` sub-verbs.

| 1.8 | 2.0 | |
|---|---|---|
| `index DIR` | `lib index DIR` | alias |
| `--label`, `--in-tree`, `--rebuild`, `--features`, `--jobs`, `-j`, `--import-tags` | same, on `lib index` | |
| `--force` | `lib index --reread` (`--force` means "overwrite" elsewhere) | alias |
| `--deep` | `lib index --analyze` (librosa; `--deep` means decode) | alias |
| `--registry` | same, on every `lib` sub-verb | |
| `--list` | `lib list` | alias |
| `--orphans` | `lib list --orphans` | alias |
| `--stats` | `lib stats LIB` | alias |
| `--refresh-stats`, `--refresh-stats-target` | `lib stats --refresh [LIB]` | alias |
| `--forget` | `lib forget LIB` | alias |
| `--remove` | `lib forget LIB --delete-db` | alias |
| `--discover`, `--min-samples`, `--max-depth`, `--label-prefix`, `--dry-run` | `lib index --discover ROOT` with the same options | alias |
| `-q`, `--quiet` | same | |
| `-v`, `--verbose` | same | |

### `query`

`lib query`.

| 1.8 | 2.0 | |
|---|---|---|
| `--registry` | same | |
| `--bpm`, `--key`, `--duration`, `--tag`, `--device`, `--category`, `--creator`, `--product`, `--text`, `--root` | same | |
| `--format` | `--only-format` | alias |
| `--compatible-with`, `--bpm-tolerance`, `--same-key`, `--no-half-double`, `--kind` | same | |
| `--limit` | `--top` | alias |
| `--output-format`, `--json`, `--csv` | same | |
| `-f` | `--output-format` | removed |
| `-o`, `--output` | same | |
| `--paths-only` | same | |
| `-v`, `--verbose` | same | |

### `similar`

`lib similar FILE`.

| 1.8 | 2.0 | |
|---|---|---|
| `-n`, `--num` | `--top` | alias |
| `--kind`, `--no-kind-filter`, `--registry`, `--paths-only` | same | |
| `--output-format`, `--json`, `--csv` | same | |
| `-f` | `--output-format` | removed |
| `-o`, `--output` | same | |

### `convert`

| 1.8 | 2.0 | |
|---|---|---|
| `-o`, `--output` | same | |
| `--division`, `--skip-existing`, `--force`, `--to-pcm`, `--codec` | same | |
| `-q`, `--quiet` | same | |

### `extract`

| 1.8 | 2.0 | |
|---|---|---|
| `-o`, `--output` | same | |
| `--output-format`, `--json`, `--csv` | same | |
| `-f` | `--output-format` | removed |
| `-q`, `--quiet` | same | |

### `formats`

| 1.8 | 2.0 | |
|---|---|---|
| `--fields` | same | |
| `--output-format`, `--json`, `--csv` | same | |
| `--format-out` | `--output-format` | removed |

### `explore`

Reads the Document in process instead of running `inspect --full`.

| 1.8 | 2.0 | |
|---|---|---|
| `-o`, `--output` | same | |

### `tui`

No flags; unchanged.

## 4. Decided

The five questions this page left open, as the user answered them:

1. **Cover art out is `edit FILE --get cover -o PATH`.** `carve FILE cover`
   is not built: an address never names a profile's pseudo-field. `cover -o`
   is an alias for `edit --get cover -o`; `cover --set IMG` for `edit --set
   cover=@IMG`; `cover --remove` for `edit --unset cover`.
2. **Two views: `inspect --summary` and `inspect --tags`.** `--summary` is
   `info`'s format-centred record, `--tags` is `inspect --pretty`'s
   tag-centred view. `info` aliases to `--summary`, `--pretty` to `--tags`.
3. **`--at` search anchors stay outside ADDR** with their 1.8 syntax
   (`end[-N]`, `find:STR`, `find:0xHEX`, `chunk:ID[+N]`, a bare offset). An
   ADDR never searches, so a value that reads as an anchor is an anchor.
4. **`--max-files` has a default on `stats` only: 10,000, for every `--by`.**
   Stopping there is a coverage finding, one line on stderr naming the flag,
   and the run exits 0. `--max-files 0` means no limit. No other verb has a
   default, and `lib index` has no `--max-files`: it never stops part-way
   through a library.
5. **`--problems-only` on `check` and on `classify`.** It is what `validate
   -q` and `classify -q` did; `-q` keeps the standard meaning (stderr only).

### 4.1 One JSON rule

Every verb's `--json` (and csv/tsv, where the verb offers them) follows it:

- **Keys are snake_case and name what the value is**, never a display label
  (`duration`, `acid_root`; not `Duration`, `ACID Root`).
- **A file is named by `path`**: the path as given (`<stdin>` for `-`, never
  a temporary copy's name). No basename beside it; that is the table's.
- **A format is named twice**: `format` is its registry id (what `acidcat
  formats` lists and `--force-format` takes, `wav`) and `label` its display
  label (`RIFF/WAVE`). Both are null when nothing recognises the file.
- **Three shapes, by what the verb reports:**
  - *Document verbs* (`inspect --json`): one object per file, one per line,
    so many files are NDJSON.
  - *Row verbs*: one JSON array of rows, always an array, even for one row:
    `inspect --summary`, `classify`, `check` (and `--fix`), `audit`, `edit`,
    `locate`, `od`, `formats`, `stats --by meta|shape`, `analyze`, `lib
    query|similar`. `audit` gives one row per file, as `check` does.
  - *Report verbs*: one object for the run, `stats --by chunks`.

## 5. As built (2.0.0a1)

Where the build refines this page, and what it does not do yet:

- `od FILE ADDR...` takes several addresses and dumps each, so `dump FILE a b`
  is one `od` (and `dump --json` one JSON array). `od --json` gives
  `{addr, offset, length, hex}` per address. An address that names nothing
  is named on stderr and skipped, as `dump` skipped a missing chunk: exit 0
  when at least one resolved, 1 when none did.
- `od` and `carve` follow an address into a decoded layer
  (`1:program/program_header`, `1:@0+16`, `1:lh5/header#frames`): the bytes
  are the layer's and so are the offsets, and the header names the layer.
- A 1.8 implementation's messages carry the 2.0 verb that runs it
  (`acidcat edit:` from what was `write`, `acidcat lib:` from `index`).
- `carve FILE GLOB#KEY` prints that field from every node the glob matches:
  `carve --field NAME` is `carve FILE **#NAME`. A single field ADDR prints its
  value, as `--field` did. `carve --chunk ID` is `carve FILE ID`, and `--raw`
  carves a node's whole extent. `-o DIR/` names the file after the ADDR
  (`dump --write`).
- A plain byte range (`@OFF+LEN`, `@OFF..END`, `@OFF`) needs no walk, so it
  works on a file no walker reads; `@OFF` alone runs to the end of the file.
  A 1.8 `--offset N` with no length becomes the anchor `N`.
- `inspect --chunks` is inspect's own chunk table; `chunks` output changes to
  it. csv and tsv give it as rows (one per chunk) and need `--chunks`.
  Every id inspect prints, in the table, the field-detail headers and the
  csv `id` column, is the node id an address takes (`RIFF/fmt_`); csv adds
  the walker's `name`. `od` headers print the id an address resolved to.
- `inspect --only/--exclude` take NODE terms of the ADDR grammar (ids,
  globs, names; `fmt,bext` still works). A pattern that names no chunk
  is an error, exit 1, naming the ids there; matching is exact, where
  1.8 folded case.
- `edit --set ADDR=VALUE` on a field a constraint ties to others sets
  those too and prints each on its own line (`RIFF/fmt_#avg_bytes_per_sec:
  44100 -> 88200 (follows sample_rate * block_align)`; `cascade` in
  `--json`); `--no-cascade` refuses the edit instead, as a new defect.
- `edit --unset NAME` clears a tag (`--set NAME=` did); `edit --force` writes
  a typed field whose type was only inferred.
- `stats --by meta|shape` print one line per file in table mode.
- `stats --by chunks` runs census's engine and report over census's scope,
  the IFF family (RIFF, RF64, W64, FORM); files of other extensions are
  counted in one stderr line. The histogram labels both counts: `files`
  holding an id and every `occurrences` of it (the JSON histogram maps each
  id to `{files, occurrences}`; the table shows one column when no file
  repeats an id). IFF-family files with no readable chunk are counted as
  `unparseable`, and a tree of nothing else exits 1, as survey did. csv and
  tsv give the histogram as rows (chunk id, files, occurrences, example).
- `--has IDS` keeps each mode's 1.8 meaning: `--by meta` lists WAV files
  holding **any** of the ids (scan's), `--by chunks` counts only files
  holding **all** of them (survey's and census's), and says how many it
  passed over. `--examples N` keeps N paths per id.
- `survey -n N` and `census --limit N` are `--max-files N`; without either,
  the 10,000 default applies, which neither 1.8 verb had.
- `--jobs`, `--io-hint`, `--follow-symlinks`, `--one-file-system`,
  `--noatime` and `--no-fadvise` are census's reader, which serves
  `--by chunks` only. A flag that belongs to another `--by` is refused
  (exit 2), not ignored. `stats FILE` works in every mode.
- Not in 2.0.0a1, and so not in the standard-flag table: `--no-recurse`
  and `--limit NAME=VALUE`. Adding either later is additive.
- Positional files are `FILE` in every usage line (`DIR` for `lib index`).
  `analyze` takes several; its rows are one array, and it exits 2 once
  when the analysis extra is missing.
- `od --output-format table|json` (`--json` as before).
- `inspect --json` is the contract v1 Document (node-v1.md), one compact
  object per file per line, the one `acidcat.open()` builds from the same
  walk, plus `file.path` and, with `--anomalies`, the forensic findings.
  `--only/--exclude` cut its tree to the chosen nodes and their ancestors.
  `--full` (1.8's positioned dump) is `--json`.
