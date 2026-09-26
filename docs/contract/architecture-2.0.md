# acidcat 2.0: architecture

Status: DRAFT for review, 2026-09-25. Companion to [node-v1.md](node-v1.md)
(the output contract) and [decisions.md](decisions.md) (why). This document
covers everything around the contract: how walkers read, how they are limited,
how formats are registered, and what the CLI, the Python API, editing, MCP and
the index look like on top of it. Nothing here is implemented yet.

## 1. The shape of 2.0

```
                 FormatSpec registry  (core/infra/registry.py)
                         |  sniff order, walker, extractor, caps, docs
                         v
 Source ----------> walker(source, limits) ---> legacy chunks + findings
 (mmap | bytes |          |                          |
  slice | layer)          | child(bytes) for layers  v
                          |                  normaliser (core/infra/contract.py)
                          +-- decoder registry --->  |
                                                     v
                                                 Document (contract v1)
                                                     |
      +----------------+-------------+---------------+------------+---------+
      v                v             v               v            v         v
 acidcat.open()   CLI verbs      TUI model      MCP tools     index v4   explorer,
 (Python API)     (ADDR)         (2.1+)         (read, edit)  (lib)      anatomy
```

Three rules hold across it:

1. **One source of truth per fact.** A format is one `FormatSpec`; a location
   is one address; a finding is one record; an edit goes through one API.
2. **The Document is the only thing consumers read.** No consumer touches a
   legacy chunk dict, a walker, or a file path after 2.0.0.
3. **Every limit is visible.** A cap that stops work is a `coverage` finding
   and a `limits.hit` entry, never a silent truncation and never a defect.

## 2. Source: what walkers read

Built (2.0 engine, first milestone). Walkers read a `Source` instead of a
path:

```python
class Source:
    size: int
    name: str | None      # display name; its extension is a format hint
    path: str | None      # the file on disk, None for bytes in memory
    def view(self, off, n) -> memoryview   # short at the end, never raises
    def read(self, off, n) -> bytes
    def file(self) -> BinaryIO             # seekable, for zipfile, gzip, seeks
    def sibling(self, name) -> "Source | None"
```

| Backend | Used for |
|---|---|
| `MappedSource` | a file on disk (built on `core/infra/mapped.map_file`); zero-copy views |
| `BytesSource` | bytes in memory: stdin, a carved region, the SMF inside an RMID, later a decoded layer |

A walker's signature is unchanged: `filepath` is a path or a Source, and the
walker reads it through `_open`, `_size` and `_name` (`walk/base.py`) and
`zip_open` / `gzip_open` (`core/infra/source.py`). The format helpers the
walkers call (`riff.iter_chunks`, `aiff.iter_chunks`,
`flac.iter_metadata_blocks`, the `mp3` readers, `mp4.find_moov`, the container
and codec probes, sniff's disk checks) take a path or a Source the same way,
and for a plain path they open the file exactly as before, so a caller outside
the walk never gets a mapping it did not ask for. `walk_file` maps a path once
per walk and closes it; a Source passed in is the caller's to close.

**Siblings** (a PSF's `_lib`, a cue sheet's BIN, an Ableton `.asd`'s audio
file, a SigMF pair, a PortaPack `.TXT`) come from `source.sibling(name)`,
which looks in the file's directory and is None for bytes in memory. A walker
that has no directory to look in skips the check: psf says nothing, cue marks
each BIN "not checked". The `sibling.unchecked` environment finding arrives
with structured findings (section 4).

**What this removed:** `walk_bytes`' temp file (1.9x slower at 300 bytes,
400x at 64 MB), RMID's temp-file re-walk (the inner SMF is walked in memory),
the second `getsize` at the
walk boundary, and filename reads through the path (akai and mpc names come
from `_name`).

**Enforced by** `tests/test_walker_invariants.py`, which bans `open`,
`os.path.getsize/exists/isfile/join/dirname`, `os.stat`, `zipfile.ZipFile` and
`gzip.open` in `core/walk/`, and `tests/test_source.py`, which requires every
seed's Document from bytes to equal the one from disk except the ledgered
sibling checks (cue, psf).

Deferred to layers (section 6 of the plan): `SliceSource` (a window onto
another Source) and deriving a child Source from a decoded image. Nothing
calls them yet.

### Layers, as built in 2.0.0a1

A decoded image is a layer of the Document (node-v1.md section 3). The
decoder registry is `core/infra/layers.py`:

| Decoder | Mapping | Check |
|---|---|---|
| `lha.lh5` | opaque | CRC-16 of the decoded bytes |
| `lha.lh0` | exact (stored) | CRC-16 |

`decode(name, src, params, cap)` runs one, bounded by `cap`, and fails on a
failed check; `layer_bytes(doc, layer_id, data)` re-derives any layer of a
Document from layer 0's bytes, nested layers included. The bytes are never
stored.

A walker declares a layer on the chunk whose payload it decoded, and walks the
image as that layer's chunks:

```python
body["layer"] = {"name": "unpacked YM5", "decoder": "lha.lh5",
                 "params": {"size": 132, "crc": 0x596D},
                 "length": 132, "length_known": True,
                 "verdict": {"result": "verified", "method": "crc16", "detail": "0x596D"}}
body["layer_chunks"] = _bare(image, y)          # the bare-YM walk of the image
```

The layer's chunks stay off the flat chunk list on purpose: every consumer of
`walk_file` (inspect, the TUI, `carve --chunk`, the editors) reads a chunk's
offset as a file offset. Geometry normalises them against the layer's length;
the normaliser decodes the layer through the registry (so the Document never
claims a layer the registry cannot produce), gives it an id, hangs its nodes
under the declaring node (`descend` cap, rule 3 of spec 5.2), fills its gaps
within the layer, and reports a decode failure as a `layer.error` finding.
The normaliser obeys two more Limits here: `depth` (layers nest no deeper) and
`inflate_bytes` (a layer longer than that is not decoded); each stops with a
`cap.*` coverage finding.

YM is the first layered format: `1:lh5/header#frames` is the frame count, at
layer 1 offset 12. `carve FILE --layer N` writes a layer's bytes (0 is the
file), decoded and checked as the walk checked them.

**Enforced by** `tests/test_layers.py` (the frame count's byte range, `carve
--layer 1` byte-identical to the image for `-lh5-` and `-lh0-`, the limits, a
decoder failure, a tampered body) and the conformance corpus, which now walks
packed YMs so every rule of spec 14 sees a derived layer.

**Departures from the plan, and why:**

- One layered format, YM. SNDH (Pack-Ice), VGZ (gzip), PSF (zlib) and the
  Ableton heads follow the same two keys and a registry entry each; the
  brief's done test is YM's.
- The walker decodes the image to walk it and the normaliser decodes it again
  through the registry. Handing the walker's bytes across would put bytes in
  the legacy chunk (and in `inspect --json`); the second decode is the price
  of the registry being the only source of a layer's bytes.
- No child Source: a walker walks the image with its own parser (`_bare`
  here). `SliceSource` and `child()` arrive with the first format whose inner
  image needs another format's walker.
- No cache for `layer_bytes`, and no shared Budget: nothing nests yet.
- `inspect --layer`, `od` on a layer and the `1:` address prefix are CLI work
  for the breaking pass (cli-2.0.md).
- YM's header fields carry display strings, so `frames` has a byte range but
  `type: display`; typing them (`enc`) is a walker change for later.

## 3. Limits: one object, visible when hit

As built in 2.0.0a1 (`core/infra/limits.py`):

```python
@dataclass(frozen=True)
class Limits:
    read_bytes: int = 64 << 20       # default read window per format
    chunk_payload: int = 64 << 10    # bytes of a chunk payload kept for display
    inflate_bytes: int = 64 << 20    # output of any one decompression
    work_steps: int = 4_000_000      # chunks, objects, commands, sectors walked
    list_rows: int | None = None     # None: each walker's own display default
    frame_rows: int = 100_000        # --frames listings
    depth: int = 32                  # nested layers and explore
    decode: bool = False             # the "extra decoding work" half of deep

def hit(name, limit, used, message) -> Note     # a coverage note naming the cap
```

**A cap hit is a coverage finding by construction.** `hit()` is the only way
to make a coverage note: `Note` refuses the coverage kind without a `cap`
(`{name, limit, used}`), and `name` must be a Limits field. All 101 sites that
announce a cap use it, including 22 that reported the hit as a plain-string
defect (so `audit` failed a clean file for being large): amiga, svx, voc, dmx,
bfdlac, emu (eight), midi (two), mp3, sigmf, tracker, mpc (two) and the
generic triage (two). The Document's `limits.hit` is read off the findings, so
the two cannot disagree (node-v1.md section 14, rule 9); each coverage finding
carries `code: cap.read|cap.payload|cap.inflate|cap.steps|cap.list|cap.frames|cap.depth`
and its `cap`.

`contract.walk(path, deep=False, limits=None)` records the Limits on the
Document; `deep=True` is `Limits(decode=True)`.

**Enforced by** `tests/test_cap_announcements.py`: each swept cap, patched
small and crossed, must now produce a coverage note whose `cap.limit` is the
patched value, a `cap.*` finding in the Document, the limit in `limits.hit`,
and a Document that validates. The pending list shrank from 60 to 58 (sigmf's
annotation listing and triage's chunk listing are swept).

**Departures from the plan, and why:**

- Walkers still bound themselves with their module constants; Limits does not
  yet set them. The cap ledger sweeps those constants (it patches them), and
  each is a per-format value (a 512-byte chip image and a 64 MB WAV do not
  share a read window). A constant is the format's default for its limit; the
  `per_format` overrides come with FormatSpec.
- The Limits values obeyed today are `decode` (what `deep` was) and, since
  layers, `depth` and `inflate_bytes` in the normaliser's layer decode.
  `list_rows` and the rest are recorded on the Document but no walker reads
  them, so `--limit NAME=VALUE` has little to set until they are wired, and
  `deep` does not yet unbound `list_rows`.
- No shared, mutable Budget. It exists to stop nested layers multiplying the
  allowance; the decoder registry has landed but no format nests one layer in
  another yet, so it lands with the first that does (a zip of gzips).
- `hit()` returns the note for the walker to append; there is no context
  object recording hits on the side. The notes are the record, which keeps the
  walkers' signatures and their tests unchanged.
- The sandboxed walk (`inspect --sandbox`) returned its result as JSON, which
  dropped every note's kind, so a sandboxed walk reported cap hits as defects.
  Fixed with structured findings (section 4).

## 4. Findings

One record for walker warnings and forensic findings; the fields are in
[node-v1.md section 9](node-v1.md#9-findings).

As built in 2.0.0a1: a walker warning stays a `Note`, the `str` subclass every
consumer already handles, and gains a `code`. The kinds are `defect`,
`coverage`, `environment`, `info` and `error`. A walker makes one with the
helper for its kind:

```python
warns.append(defect("size.overrun", f"chunk {cid} claims {n} bytes but only {k} remain"))
warns.append(environment("sibling.missing", f"names library {lib!r} and it is not beside this file"))
warns.append(hit("list_rows", _CAP, total, f"listing the first {_CAP} of {total}"))
```

Codes live in `core/infra/findings.py` (`REGISTRY`: code to kind, default
severity and meaning); a helper refuses an unregistered code or one used with
the wrong kind, and `tests/test_findings.py` fails on a literal code the
registry lacks and on a registered code nothing emits. Messages keep their
text: every seed and fixture walks to the same text as before, and only the
kind or code changed (cue, psf, stm, the generic triage).

**Converted:** 100 emit sites carry a code: `size.overrun` 28,
`magic.mismatch` 17, `pointer.dangling` 14, `parse.failed` 8,
`length.misaligned` 6, `reserved.nonzero` 5, `header.truncated` 5,
`checksum.mismatch` 4, `chunk.short` 3, `sibling.missing` 3,
`sibling.unchecked` 2, and one each of `encoding.unknown`, `walker.error`,
`geometry.error`, `geometry.invalid`, `triage.generic`; plus the seven
`cap.*` codes on all 101 cap hits. **Legacy, finished:** the last walker
and format-decoder warnings were given codes in one pass (about 440 sites in
`core/walk/` and `core/formats/`), which added sixteen codes for what the
first families did not cover: `count.mismatch`, `field.inconsistent`,
`value.invalid`, `text.invalid`, `address.outside`, `required.missing`,
`chunk.order`, `id.unknown`, `reference.unresolved`, `bytes.stray`,
`sibling.mismatch`, `value.assumed`, `layout.unmeasured`,
`convention.noted`, `decode.partial` and `format.unrecognized`. A resolver
that fails for one of several reasons carries the code with its reason
(`h["code"]` beside `h["why"]`), and `coded(code, message)` makes a note
whose kind comes from the registry. `tests/test_findings.py` requires every
warning written as text to be coded and every seed to walk to no `legacy`
finding. The `legacy`
code stays registered, and `typing.findings_legacy` still counts it.

**Consumers:** `anomalies.scan` reports a walker note by its kind
(`environment` and `info` are their own rules, not `structure`) and gives
every finding a `code` (`anomaly.<rule>` for its own rules); `audit` no longer
fails a file for an `environment` or `info` finding (a PSF whose library is
not beside it exits 0); the forced parse picks a walker's complaint by code,
not by the words "magic" or "spec says"; the TUI hides the triage preamble by
its code. The sandboxed walk carries each note's kind and code across its JSON
boundary.

**Departures from the plan, and why:**

- No `emit` object and no `node=`/`at=` on a walker's finding. The walker's
  note already sits on its chunk, which the normaliser turns into the node, so
  `node` is filled there; a byte locator per finding waits for a walker that
  needs one.
- Forensic findings keep their dict shape (`severity`, `offset`, `rule`,
  `message`) and gain `code`; the Document does not yet include them. Merging
  them is one call in the normaliser once `acidcat.open()` decides when the
  forensic scan runs (it reads the file tail, which a plain walk does not).
- The forensic rules are coded `anomaly.<rule>` rather than the spec's
  illustrative names (`text.control_bytes` is `anomaly.nonprintable_text`):
  one rule, one code, no second vocabulary to keep in step.
- A severity is the code's default; no site raises or lowers it yet.

## 5. FormatSpec: a format is one record

```python
FormatSpec(
    id="ym", label="ST-Sound YM2149 register dump (YM, usually LHA-packed)",
    family="chiptune", extensions=(".ym",), variants=("ym2", "ym3", "ym5", "ym6"),
    magic=(Probe(0, b"YM"), Probe(2, b"-lh5-")),
    confirm="acidcat.core.formats.ym:confirm",      # disk check, optional
    after=("lha_generic",),                          # explicit sniff ordering
    walker="acidcat.core.walk.ym:walk",              # lazy import path
    extractor=None, convert=None, repair=None, edit=None,
    endian="be", carve=None,
    decoders=("lha.lh5",), render="ym",
    seed="ym", corpus_env="ACIDCAT_YM_CORPUS",
    anatomy=AnatomySpec(specimen="tests/fixtures/anatomy/space_gun.ym",
                        prose="docs/formats/ym.prose.toml", accent="#c94"),
    since="1.8.5",
)
```

`core/infra/registry.py` holds an explicit list of specs (no plugin discovery)
and derives everything that is a separate list today: `KNOWN_FORMATS`,
`_WALKERS`, `_EXTRACT_ONLY`, `EXTRACTABLE`, `AUDIO_CONTAINERS`, `_BE_FMTS`
(keyed on id, not label), the whole `acidcat formats` matrix,
`corpus_env.sh`, `tests_for.py`'s shared-walker map, the anatomy mirror's
cards, and the format counts in README and ARCHITECTURE. Capabilities that
are per format (render engine, decoders) live here; per-node caps stay in the
Document.

**Sniff ordering** becomes data: each spec's `magic` probes plus `after`
edges form a graph whose topological order must equal today's hand order.
Before anything moves, a **golden sniff test** freezes today's behaviour over
every seed and a set of known near-misses (S3M against SNES ROM, RTF against
Vital, an `.lzh` of NSF files, PT2/STC extension gates). The refactor passes
only if every id is unchanged.

**Variants** (PT2 under `pt3`, `aiff`/`aifc`, `e4b`/`e5b`, `mod15`) are one
spec with `variants`, and the variant is a field in the Document's `format`.

**Adding a format** becomes: the decoder, the walker, one spec, the seed, the
tests, the anatomy prose. The five to seven bookkeeping edits per format
measured over the last six format commits disappear.

## 6. The Python API

```python
import acidcat

doc = acidcat.open("space gun 1.ym")            # path, bytes, or a Source
doc = acidcat.open(data, limits=acidcat.Limits(decode=True))

doc.format.id                                   # "ym"
doc.node("lh5/header").summary
f = doc.field("1:lh5/header#frames")            # an ADDR
f.value, f.display, f.type, f.at                # 8755, "8,755", "u32be", Loc(1, 12, 4)
doc.layer_bytes(1)[:4]                          # b"YM6!"
[x for x in doc.findings if x.kind == "defect"]
doc.to_json()                                   # the contract v1 dict

patch = doc.edit({"RIFF/fmt_#sample_rate": 48000})
patch.verify()                                  # re-reads, checks round trips
patch.commit("out.wav", backup=True)
```

`Document`, `Node`, `Field`, `Layer`, `Finding` and `Loc` are thin read-only
views over the v1 dict (the dict stays the source of truth, per decision C5).
Helper namespaces stay: `acidcat.probe`, `acidcat.viz`, `acidcat.play`,
`acidcat.locate`, `acidcat.sniff`. `read_metadata` is exported. The tuple API
(`walk`, `walk_file`) is removed. An API reference page is generated from the
docstrings and pinned by a test that every public name is documented.

`acidcat-lab` moves to this API in the same release.

## 7. Editing: one front door

```
doc.edit({ADDR: value, ...})
   |
   +-- field with type_source declared/enc, fixed width
   |      encode(type, inverse xform, value) -> bytes of the same length
   |      verify: decode(new bytes) == value, neighbours in a bits container kept
   |
   +-- node with an `edit` cap (metadata profile: wav, aiff, tagged, vital, ...)
   |      the profile writer (edit_riff, edit_aiff, mutagen, JSON presets)
   |
   +-- cover art: the `cover` pseudo-field of a tagged file
   |
   v
 Patch (list of byte ranges and replacements, or a whole new file from a profile)
   |  verify()   re-walk the patched bytes; the edited fields read back; no new defects
   |  repair()   optional: run constraints.repair for sizes the edit changed
   |  commit()   writer.commit: atomic write plus backup, as today
```

The CLI's `edit`, the TUI and MCP's `edit_field` all call this. What stays
format-specific is exactly what must: variable-length text re-serialisation,
size cascades (`structure.py`, `mp4repair`), and the metadata profiles. A field
with `type_source: inferred` is not editable without `--force`, because its
type is a guess.

## 8. The CLI

Fifteen verbs replace twenty-nine. Every one takes `-` for stdin, `--json` /
`--output-format`, `--color`, and addresses in the ADDR grammar
([node-v1.md section 13](node-v1.md#13-addresses)).

| 2.0 verb | Replaces | Notes |
|---|---|---|
| `inspect FILE...` | inspect, chunks, info | `--summary` is today's info view; `--only ADDR-glob`; `--layer N`; `--frames` |
| `od FILE [ADDR]` | od, dump, probe hexdump | annotated hex of any node, field, layer or range |
| `carve FILE ADDR` | carve, dump --write, wrap | `-o`, `--as-wav` for raw PCM (wrap), `--layer` writes a decoded image |
| `probe SUB FILE...` | probe | read, table, scan, find, strings, diff, entropy, map, lsb |
| `classify FILE...` | classify | unchanged |
| `locate FILE` | locate | unchanged |
| `audit FILE...` | audit | exit 1 counts `defect` findings only |
| `check FILE...` | validate, repair | `--fix` repairs; `--dry-run`, `-o`, `--overwrite` |
| `edit FILE --set ADDR=V` | write, cover | `--set cover=@img.jpg`, `--unset cover`, `--strip` |
| `stats DIR... --by shape|chunks|meta` | census, survey, shape, scan | census's parallel engine for all of them |
| `analyze FILE... [--bpm-key] [--features]` | detect, features | needs the `analysis` extra; exits 2 without it |
| `lib index|query|similar|list|forget|stats` | index, query, similar | index's action flags become sub-verbs |
| `convert`, `extract` | convert, extract | unchanged |
| `formats` | formats | the matrix, derived from the registry |
| `explore FILE` | explore | reads the Document in process, no subprocess |
| `tui [FILE]` | tui | |

Standard flags, the same everywhere: `--json`, `--output-format
table|json|csv|tsv` (default `table` for every verb), `--color
auto|always|never`, `-o/--output`, `--no-recurse`, `--max-files N`,
`--byte-order be|le|both`, `--only-format FMT` (filter) and `--force-format
FMT` (override; plain `--format` is retired), `--limit NAME=VALUE`, `--deep`.
Exit codes: 0 ok, 1 the answer is no, 2 could not run (including a missing
extra). The retired verb names are removed, not aliased (decision S1).

## 9. MCP

Read-only tools added beside the catalogue tools:

| Tool | Returns |
|---|---|
| `inspect_file(path, only?, layer?)` | the Document, or the named subtree |
| `read_field(path, addr)` | one field: value, display, type, at |
| `list_layers(path)` | layers with verdicts |
| `findings(path, kind?)` | the findings |

`edit_field(path, addr, value, dry_run=true)` exists only when the server is
started with `--allow-writes`, returns the Patch's verification report, and
commits only when `dry_run` is false. `get_sample` also returns
`read_metadata`'s canonical fields.

## 10. The index (schema v4)

- The fixed columns (bpm, key, rate, channels, bits, duration, title, ...) are
  filled from Documents for **every** format through the canonical alias map
  in `core/metadata.py`: each canonical name lists candidate field addresses in
  precedence order, then the filename and librosa fallbacks as today.
- A `fields` table stores an allowlist of typed values per file: the `audio`
  cap parameters, the canonical bindings, and declared caps
  (`fields(path, addr, type, num, txt)` with indexes on `(addr, num)` and
  `(addr, txt)`).
- New query filters: `--rate`, `--channels`, `--bits`, `--can render|decode|carve`.
- The row records the producer version; a version change triggers a reindex
  of that row on the next scan, because node ids are stable across runs, not
  across versions.
- `ctx` side-dicts and `CTX_KEYS` retire; their keys become canonical bindings.

## 11. Explorer and anatomy pages

- `explore` renders from the Document and `layer_bytes` in process.
- One shared page builder replaces the 22 `build_*_anatomy.py` copies. Byte
  maps are generated by walking a **real specimen** (kept as a fixture; the
  pages promise every byte comes from a real file), with field colours from
  declared types and kinds. Prose lives in a sidecar per format keyed by field
  address. A test fails when prose names an address the walker no longer
  emits, and when a generated map differs from the walk.
- The hand-typed byte maps, `check_bytemaps.py`'s self-consistency role and
  the copied-template bugs go away.

## 12. Removed in 2.0

- `core/grammar/` and its three test files (git history keeps them);
  `test_ctx_keys_covers_walker` moves out first.
- The tuple API, the retired CLI verbs and flags, `walk_bytes`' temp file,
  `ctx`/`CTX_KEYS`, the per-builder anatomy scripts, Python 3.10.

## 13. Platform

- `requires-python >= 3.11`; CI 3.11, 3.12, 3.13, 3.14 on Linux, one version
  each on macOS and Windows as today.
- `textual>=8.0,<9`; `pytest-textual-snapshot` and `jsonschema` in `dev`.
- Optional extras added in 2.x: `graphics` (`textual-image`, Pillow) and `web`
  (`textual-serve`).
