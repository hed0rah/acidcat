# acidcat 2.0: the CLI, old to new

Every command and flag acidcat 1.8 accepts, what it becomes in 2.0, and
whether the old spelling keeps working. This is the plan for the breaking
pass; no CLI code has changed yet. `tests/test_cli_mapping.py` fails when a
verb or flag exists in the parser and not here, so the table cannot fall
behind the code.

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
  | `--color auto\|always\|never` | every verb that prints to a terminal |
  | `-o/--output PATH` | write here instead of stdout |
  | `-q/--quiet` | drop progress and summary lines on stderr; never changes stdout |
  | `-v/--verbose` | add diagnostic lines on stderr; never changes stdout |
  | `--max-files N` | stop after N files (replaces `-n/--num` where it counted files, and `census --limit`) |
  | `--no-recurse` | do not descend into directories given as targets |
  | `--top N` | keep the first N results (replaces `similar -n`, `query --limit`) |
  | `--byte-order be\|le\|both` | replaces `carve --endian`, `probe --be/--le`, `wrap --endian` |
  | `--only-format FMT` | filter targets by format (replaces `shape --format`, `query --format`) |
  | `--force-format FMT` | parse as FMT whatever the magic says (replaces `inspect --format`) |
  | `--deep` | `Limits(decode=True)`: do the extra decoding work (frames, checksums, compressed subtrees) |
  | `--limit NAME=VALUE` | set one limit (`read_bytes`, `list_rows`, `frame_rows`, ...; node-v1.md section 9.1) |

- **`--deep` means one thing.** In 1.8 it meant "decode more" on `validate`
  and `index`, and "run librosa" on `info`. The librosa meaning moves to
  `analyze`.
- **`-v` never changes stdout.** In 1.8 `inspect -v` was a synonym for
  `--frames` and `info -v` showed more fields; both move to real flags.
- **Addresses.** Every place a location is taken accepts an ADDR (node-v1.md
  section 13): `RIFF/fmt_`, `RIFF/fmt_#sample_rate`, `@0x100+64`,
  `@0x100..0x200`, `1:lh5/header#frames`. `--at` keeps its search anchors
  (`end[-N]`, `find:STR`, `find:0xHEX`), which are not addresses.
- **Exit codes**: 0 ok, 1 the answer is no (a defect finding, a failed check,
  nothing matched), 2 could not run (bad arguments, unreadable input, a missing
  extra). `coverage`, `environment` and `info` findings never exit 1.
- `acidcat --version` is unchanged.

## 2. Verbs

| 1.8 | 2.0 | |
|---|---|---|
| `inspect` | `inspect` | |
| `info` | `inspect --summary` | alias |
| `chunks` | `inspect --quiet` | alias |
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
| `-q`, `--quiet` | same: the chunk table only | |
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

`inspect --quiet FILE...`.

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

| 1.8 | 2.0 | |
|---|---|---|
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

## 4. Open

Decisions this mapping needs before the CLI code is written:

1. `cover -o` as `carve FILE cover`: `cover` has to resolve as an address.
   Either the metadata profiles name a `cover` node, or `carve` grows a
   profile lookup. The simplest honest alternative is `edit --get cover -o`.
2. `inspect --pretty` and `info` folded into one `--summary`: the two views
   overlap but are not identical today (`--pretty` is tag-centred, `info` is
   format-centred). One view, or `--summary` and `--tags`?
3. `--at` search anchors (`end-N`, `find:`) stay outside ADDR. Folding them in
   (`@end-16`, `@find:LIST`) would make every address a potential search.
4. `stats --by meta` defaults to reading the whole tree where `scan` stopped
   at 500 files. A default `--max-files` keeps a mistyped `/` from walking a
   disk.
5. `validate -q` becoming `--problems-only` follows the `-q` rule; `classify
   -q` has the same shape ("only report files that are not plain") and would
   follow it.
