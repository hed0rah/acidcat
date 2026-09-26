# Node contract v1

Status: DRAFT for review, 2026-09-25. Nothing in this document is implemented
yet. The decisions behind every rule are recorded, with their reasons, in
[decisions.md](decisions.md). The machine-readable form is
[node-v1.schema.json](node-v1.schema.json); where the two disagree, this
document is the intent and the schema is the bug.

## 0. What this is

A walk returns one **Document**: the byte layers it read, a tree of nodes,
typed fields, declared capabilities and positioned findings, stamped
`"contract": 1`. It is plain JSON. The TUI, `inspect --json`, the explorer and
any future consumer read the same thing, so they cannot drift apart.

The contract describes the OUTPUT of the walkers that exist. It does not change
how any walker parses. It is produced at the `walk_file` boundary by a
normaliser from today's chunk dicts, so every walker is v1 on the day it lands
and walkers then migrate from inferred to declared information one at a time,
the way geometry did (f1dff46).

Three rules carry over unchanged from the codebase, and every section below is
an application of one of them:

1. **Walkers are the oracle.** The Document says what the walker said, and
   says so honestly when it had to be filled in.
2. **Mark, never repair.** An inferred or invalid value is labelled as such
   (`type_source`, `geometry`, `verdict`), never promoted to a fact because it
   looks plausible.
3. **Enforced at one boundary, by tests.** Conformance is checked on every seed
   and on the hunt corpus, with shrink-only ledgers for known exceptions.

## 1. Terms

| Term | Meaning |
|---|---|
| layer | A byte string a walk reads. Layer 0 is the file. Any other layer is derived from a parent layer by a decoder. |
| locator | Where something is: a byte range in a layer, or a path in a text layer. |
| node | One structural unit: a chunk, box, block, record, table or region. Nodes form a tree. |
| field | One named value inside a node. |
| cap | A capability a node declares: what can be done with it. |
| finding | One structured observation: a defect, a cap that stopped the walk, a missing sibling file, an anomaly. Walker warnings and forensic findings are the same record. |
| normaliser | `core/infra/contract.py`: legacy chunk dicts in, a v1 Document out. |
| address | The text form of a location: `RIFF/fmt_#sample_rate`, `1:lh5/header`, `@0x5d1000+64` (section 13). |

## 2. The Document

```json
{
  "contract": 1,
  "producer": {"name": "acidcat", "version": "2.0.0"},
  "format": {"id": "ym", "label": "ST-Sound YM2149 register dump (YM, usually LHA-packed)"},
  "file": {"size": 1503},
  "layers": [ ... ],
  "nodes": [ ... ],
  "findings": [ ... ],
  "limits": { ... },
  "typing": { ... }
}
```

| Key | Type | Req | Meaning |
|---|---|---|---|
| `contract` | int | yes | Always `1` for this version. Additive changes keep `1`. |
| `producer` | object | yes | `name` and `version` of the tool that made the Document. |
| `format` | object | yes | `id` is the format id from the registry (`wav`, `ym`, ...); `label` its display label; `family` its family (`riff`, `iff`, `chiptune`, ...). `forced: true` when the caller chose the walker. |
| `file` | object | yes | `size` in bytes. `path` only when the caller asks for it (paths leak). |
| `layers` | array | yes | Every layer the Document references, layer 0 first. |
| `nodes` | array | yes | The root nodes of the tree, in file order. |
| `findings` | array | yes | Every warning and anomaly, file-level and node-level, as structured records (section 9). May be empty. |
| `limits` | object | yes | The limits the walk ran under, and which of them were hit (section 9.1). |
| `typing` | object | yes | How much of this Document was declared rather than inferred (section 12). |

A walk that fails degrades exactly as today: zero nodes plus a finding of
kind `error`, never an exception, with `ACIDCAT_WALKER_RAISE=1` re-raising in
tests.

## 3. Layers

```json
{"id": 0, "name": "file", "kind": "file", "length": 1503}

{"id": 1, "name": "unpacked YM6", "kind": "derived",
 "parent": 0, "from_node": "lh5",
 "sources": [{"parent_off": 34, "len": 1468}],
 "mapping": "opaque",
 "decoder": {"name": "lha.lh5", "params": {}},
 "length": 140155, "length_known": true,
 "crypto": "none",
 "verdict": {"result": "verified", "method": "crc16", "detail": "0x3A51"}}
```

| Key | Type | Req | Meaning |
|---|---|---|---|
| `id` | int | yes | Unique in the Document. Layer 0 is the file. |
| `name` | string | yes | Human name for the breadcrumb. |
| `kind` | `file` or `derived` | yes | |
| `parent` | int | derived | The layer this was decoded from. Layers may nest (zip member, then gzip inside it). |
| `from_node` | node id | derived | The node whose bytes were decoded. `descend` on that node opens this layer. |
| `sources` | array | derived | The parent bytes the layer was made from, in order: `{parent_off, len, layer_off?}`. A compressed layer has one source. A scatter layer (future: a CD-XA stream) has many. |
| `mapping` | `opaque` or `exact` | derived | `exact`: layer byte `layer_off + k` IS parent byte `parent_off + k` (decryption excepted, see `crypto`), so a selection in the layer can be lit in the parent. `opaque`: an entropy decoder sits between them and no byte maps back; a consumer lights the `from_node` instead. `layer_off` is required on every source when `exact`. |
| `decoder` | object | derived | `name` from the decoder registry (`lha.lh5`, `ice.2x`, `gzip`, `zlib`, `zip.stored`, `zip.deflate`, `fastlz`, later `xa.stream`, `wii.partition`), and `params` it needs. |
| `length` | int | yes | Bytes in the layer as far as it is known. |
| `length_known` | bool | derived | `false` for a capped prefix (Ableton, NI, Bitwig heads): `length` is what was decoded, not the image's size. |
| `truncated_at` | int | no | Present when a cap stopped decoding; a `coverage` finding names the cap. |
| `crypto` | `none` or `format` | derived | `format`: decryption with a key that is part of the format itself (the Wii common key). User or title licence DRM is never a layer: it is inspected and flagged. |
| `verdict` | object | derived | How the decoded bytes were checked: see below. |

**Verdict.** `result` is one of:

| result | Meaning | Examples |
|---|---|---|
| `verified` | A check over the decoded bytes passed | lh5 CRC-16, gzip CRC32 + ISIZE, Wii H0 to H2 hashes |
| `length-only` | Only the length could be checked | Pack-Ice (the write pointer lands on 0 exactly) |
| `unverified` | Nothing checks the decoded bytes | PSF (its CRC covers the compressed bytes), FastLZ, a capped prefix |
| `failed-shown` | A check failed; the layer is shown anyway, flagged | a bad CRC in a file that still inflates; multisample's known-bad Bitwig CRCs |

`method` names the check (`crc16`, `crc32`, `exact-length`, `hash-tree`,
`none`); `detail` is free text. Malformed files are the subject, not an error,
so `failed-shown` exists rather than refusing the layer.

**Bytes are never stored in the Document.** A consumer that needs layer bytes
asks the decoder registry (`contract.layer_bytes(doc, layer_id)`), which
decodes on demand, caches per session, and enforces the same caps the walkers
do. A walker that only needs a prefix (PSF streams its program and keeps 16
bytes) keeps doing that; declaring a layer does not force materialising it.

**Not layers.** A CPU address space (NSF, GBS, KSS and HES bank switching, the
SPC's 64 KB RAM with its IPL overlay) is not a derived byte string: every byte
is already in layer 0, and what differs is the coordinate. It is reserved as a
node annotation, `maps_to: {space: "cpu", base: 0x8000, bank: 3}`, in a later
v1.x. It is not part of 2.0.

## 4. Locators

Every position in the Document is a locator. There are two kinds.

**Byte locator** (the default; `kind` may be omitted):

```json
{"layer": 1, "off": 12, "len": 4}
```

`off` is **absolute within the layer**. It is never relative to a node, a
payload base or a parent field. A consumer never adds anything to it. That one
rule removes the class of bug the pre-step fixes (five walkers measured field
offsets from the wrong base).

**Path locator**, for text layers (XML, JSON, INI) where a byte range is the
wrong question:

```json
{"kind": "path", "layer": 0, "syntax": "json-pointer", "path": "/meta/rate"}
{"kind": "path", "layer": 1, "syntax": "xml-steps",
 "path": ["Ableton", "LiveSet", "Tracks", "AudioTrack[2]", "Name"]}
```

`syntax` is `json-pointer` (RFC 6901) or `xml-steps` (element names, with a
1-based `[n]` for the nth sibling of that name). A path locator may also carry
`off`/`len` when the walker knows the byte span of the element.

**Unpositioned** is spelled one way: the locator key is absent. A derived value
(a duration computed from a frame count, a count of entries) has no locator
and says what it came from with `derived_from` (section 6).

## 5. Nodes

```json
{"id": "lh5/header", "name": "header", "kind": "record",
 "extent": {"layer": 1, "off": 0, "len": 34},
 "payload": {"layer": 1, "off": 0, "len": 34},
 "geometry": "declared",
 "summary": "YM6, 8,755 frames at 50 Hz",
 "fields": [ ... ], "children": [], "caps": {}}
```

| Key | Type | Req | Meaning |
|---|---|---|---|
| `id` | string | yes | Stable path id, unique in the Document (below). |
| `name` | string | yes | Display name; today's chunk `id`, stripped. |
| `kind` | enum | yes | `container`, `chunk`, `record`, `table`, `array`, `audio`, `code`, `text`, `padding`, `unwalked`. |
| `extent` | byte locator | no | Every byte the node occupies, header included. Absent for a node with no position, and for one whose claimed range does not fit its layer (`geometry: invalid`): the claim is reported as a `geometry.invalid` finding instead, because a locator must be inside its layer. |
| `payload` | byte locator | no | The bytes inside it: what recursion descends into. Same layer as `extent`. |
| `geometry` | enum | yes | `declared`, `defaulted`, `invalid`, `unpositioned`: today's `geometry.py` verdict, carried unchanged. |
| `summary` | string | yes | As today. |
| `fields` | array | yes | Section 6. May be empty. |
| `children` | array of nodes | yes | Section 5.2. May be empty. |
| `caps` | object | yes | Section 8. May be empty. |
| `rows` | object | no | Section 10. |
| `origin` | `walker` or `normaliser` | no | `normaliser` marks a node the normaliser synthesised (a gap). Absent means `walker`. |

### 5.1 Node ids

`id` is the parent's id, a `/`, and a slug of the node's name. A name that
repeats among siblings gets `~n` (1-based, in order) on every occurrence after
the first: `LIST`, `LIST~2`. Slugs keep ASCII letters, digits, `_`, `-`, `.`
and replace anything else with `_`, so `fmt ` becomes `fmt_`. Ids are stable
across runs of the same file and the same acidcat version, which is what
bookmarks, `--layer` arguments and golden tests key on. They are not promised
stable across versions.

### 5.2 Children

Walkers emit a flat chunk list today; nesting is implied by extent enclosure
(the rule f0cece4 already relies on). The normaliser builds `children` from
that:

1. A node is a child of the smallest node in the same layer whose extent
   encloses its extent. Equal extents nest in emission order (the first is the
   parent).
2. A walker may declare parentage explicitly (`parent` on the legacy chunk)
   where enclosure is ambiguous; a declaration wins.
3. Nodes in a derived layer are children of that layer's `from_node`.
4. Siblings must not overlap. Today's sibling-overlap ledger
   (`KNOWN_COLLISIONS`, empty) carries over.
5. Children need not tile their parent. A gap of at least one byte inside a
   parent's payload, or between top-level nodes in layer 0, becomes a
   synthesised node, `kind: "unwalked"`, `origin: "normaliser"`. Bytes that are
   the parent's own header (extent minus payload) are not a gap. This is what
   makes a byte map honest: a clean WAV has no unaccounted bytes, and a real
   cavity shows up as a node you can select.

## 6. Fields

```json
{"name": "frames",
 "at": {"layer": 1, "off": 12, "len": 4},
 "type": "u32be", "type_source": "declared",
 "value": 8755, "display": "8,755",
 "note": "2:55.10 at 50 Hz"}
```

| Key | Type | Req | Meaning |
|---|---|---|---|
| `name` | string | yes | As today. |
| `key` | string | yes | `name`, with `~n` for a repeat within the node (section 5.1 rule). `node_id + "#" + key` addresses a field. |
| `at` | locator | no | Where the field lives. Absent means unpositioned. |
| `type` | type string | yes | How the bytes are stored (section 6.1). `display` when nothing better is known. |
| `type_source` | enum | yes | `declared` (the walker said), `enc` (from a legacy `enc`), `inferred` (the normaliser guessed), `none` (type is `display`). |
| `xform` | xform string | no | How the stored value becomes `value` (section 6.2). |
| `value` | JSON value | yes | The machine value: an int, a float, a string, a bool, a list of ints for `bytes`. Never a formatted number. |
| `display` | string | yes | What a human reads: today's `value`, verbatim. |
| `note` | string | yes | As today; may be empty. |
| `unit` | string | no | `Hz`, `bytes`, `frames`, `samples`, `ms`, `cents`, `dB`, ... |
| `enum` | string | no | For enumerations: the label of `value` (`display` usually repeats it). |
| `flags` | array of string | no | For bit sets: the names of the set bits. |
| `ptr` | locator | no | This field is a pointer, and this is where it points. |
| `derived_from` | array of string | no | Field keys (same node) or `node_id#key` this value was computed from. Only on unpositioned fields. |
| `remote` | bool | no | `true`: the field describes this node but its bytes are stored outside the node's extent (a MOD sample's header lives in the module header, not beside its PCM). |

`value` and `display` split what today's `value` mixes. Today 363 of 1,074
seed fields already hold a machine int and 710 hold a display string; after
normalisation every field has both.

### 6.1 Types

A type says how the value is **stored**, never what it means.

| Type | Bytes | Notes |
|---|---|---|
| `u8` `i8` | 1 | |
| `u16le` `u16be` `i16le` `i16be` | 2 | |
| `u24le` `u24be` | 3 | |
| `u32le` `u32be` `i32le` `i32be` | 4 | |
| `u64le` `u64be` `i64le` `i64be` | 8 | |
| `f32le` `f32be` `f64le` `f64be` | 4, 8 | |
| `f80be` | 10 | IEEE 754 extended, AIFF's sample rate |
| `fourcc` | 4 | Four ASCII bytes; `value` is the string |
| `ascii` | n | Fixed width, value stripped of trailing NULs and spaces |
| `cstring` | n | NUL-terminated within `len` |
| `utf16le` `utf16be` | n | |
| `latin1` `utf8` | n | Fixed width text in that encoding |
| `synchsafe` | 4 | ID3's 7 bits per byte |
| `varint:midi` `varint:leb128` `varint:s98` | n | Variable length; `len` is the encoded length |
| `bits:CLEN:BITPOS:WIDTH` | CLEN | WIDTH bits at BITPOS from the MSB of a CLEN-byte container that starts at `at.off` |
| `bytes` | n | Opaque; `value` is a list of ints, or absent when `len` > 64 |
| `display` | any | No claim about the bytes. The re-read check skips it. |
| `derived` | 0 | Computed, no bytes; `at` must be absent. |

The legacy `enc` language maps onto this table:

| legacy `enc` | v1 `type` + `xform` |
|---|---|
| `<B` `>B` | `u8` |
| `<H` `>H` `<h` `>h` `<I` ... `<q` `>q` | `u16le` ... `i64be` |
| `<f` `>f` `<d` `>d` | `f32le` ... `f64be` |
| `float80` | `f80be` |
| `u24be` | `u24be` |
| `synchsafe` | `synchsafe` |
| `bits:D:C:P:W:B` | `bits:C:P:W` with `at.off` moved by D, and `xform: add(-B)` when B is not 0 |
| `bitsmap:D:C:P:W:MAP` | `bits:C:P:W`, `value` the raw int, `enum` the label from MAP |
| `bitsdyn:D:C:P:W:MAP` | as `bitsmap`, the map resolved from context |

### 6.2 Transforms

Some stored values are not the value: a count stored minus one, a 16.16 fixed
point rate, a pointer relative to itself. The grammar engine froze on exactly
these (0dbe82d). v1 names them rather than forcing walkers to pick between a
lying type and no type:

| xform | value = |
|---|---|
| `add(k)` | stored + k (FLAC channels, STC position count) |
| `neg` | minus stored (KRZ block size) |
| `mul(k)` | stored times k |
| `fixed(m.n)` | stored / 2^n (MP4 sample rate is `fixed(16.16)`) |
| `ascii-dec` | the decimal digits in the stored text (SNDH `##02`) |
| `rel(self)` | stored + the field's own offset (VGM's EOF and data offsets) |
| `rel(node)` | stored + the node's extent offset |
| `mask(0xNN)` | stored AND mask (S3M master volume) |

Transforms compose left to right with `;`: `mask(0x7f);add(1)`. A walker that
needs one not in this table uses `type: "display"` for that field and the gap
is visible in the `typing` counts, not hidden in a guess. New transforms are additive.

### 6.3 Pointers

`ptr` is only ever "this field points there". It never means "this field lives
there" (that is `at`). Today `xref` carries both meanings: HES, SPC and CMF put
it on unpositioned header fields to say where they are. The pre-step turns
those into positioned header fields, so the normaliser maps every legacy
`xref` to `ptr` with `layer` set to the field's own layer. A `ptr` must land
inside its layer; one that does not is kept and marked with a finding
(`pointer.dangling`), never dropped.

## 7. Positions in the legacy model (what the normaliser reads)

The pre-step fixes the legacy model so the normaliser has one rule:

- A field's absolute offset is `payload_base + off`. `off` may be **negative**
  for a field in the node's header (a RIFF chunk's id is `off = -8`).
- A positioned field lies inside its chunk's extent, unless it is marked
  `remote` (stored elsewhere, like a MOD sample's header), in which case it
  lies inside the file. (Landed in 1.8.6.)
- `off = None` is the only spelling of "unpositioned". `_f(0, 0, ...)` is
  banned by test.
- `xref` is only a pointer.

## 8. Capabilities

A cap is a static statement about what a node IS. Whether the machine can act
on it right now (ffplay installed, the `analysis` extra present) is checked by
the consumer at run time, never written into the Document, so the same file
always yields the same JSON.

| Cap | Payload | Meaning | Replaces |
|---|---|---|---|
| `audio` | `{codec, rate, channels, bits, frames?, float?, fields}` | PCM or ADPCM sample data in this node's payload. `fields` names the field keys the parameters came from, and the conformance test checks they agree. | TUI `_audio_params` / `_params_from` guessing |
| `decode` | `{format}` | These bytes, as a file, are format X for an external decoder (ogg, mp3, flac, m4a). | `_DECODABLE` substrings, `_decodable_at` |
| `render` | `{engine, subtunes?, default?}` | Run with an in-house engine: `sid`, `spc`, later `ym`. | `"sid tune" in fmt`, `"spc700 sound snapshot" in fmt` |
| `carve` | `{ext, what?}` | Saving the payload (or the layer, if the node has `descend`) gives a standalone file with this extension. | locate-side heuristics |
| `descend` | `{layer}` or `{format}` | Open as a nested Document: a derived layer, or the payload walked as another format. | the explore pass guessing |
| `edit` | `{profile}` | Text tags a metadata profile can write (`wav`, `aiff`, `tagged`, `vital`). Byte-level editing needs no cap: a field whose `type_source` is `declared` or `enc` is editable. | `edit_profile` in `tui_app/render.py` |

In 2.0.0 the normaliser fills caps by moving today's TUI heuristics, unchanged,
into `core/infra/capabilities.py`, and marks each one `"source": "inferred"`.
A walker that declares a cap writes `"source": "declared"`. From the TUI
inspectors release (2.2) no format name appears in `tui_app/`.

## 9. Findings

Walker warnings (today's strings) and forensic findings (today's anomaly
dicts) become one record, in one list on the Document. A finding about a node
names it; nothing is copied up to file level, so nothing is reported twice.

```json
{"kind": "coverage", "code": "cap.list", "severity": "info",
 "message": "listing the first 256 of 1,024 instruments",
 "node": "INST", "cap": {"name": "list_rows", "limit": 256, "used": 1024}}

{"kind": "defect", "code": "text.control_bytes", "severity": "notice",
 "message": "3 control bytes in a text field",
 "at": {"layer": 0, "off": 1234, "len": 12}, "node": "RIFF/LIST/INAM"}
```

| Key | Type | Req | Meaning |
|---|---|---|---|
| `kind` | enum | yes | `defect` (the file breaks its format), `coverage` (a limit stopped the walk; says nothing about the file), `environment` (something outside the file: a sibling not found, an extra not installed), `info` (worth knowing, not wrong), `error` (the walker or normaliser failed; a bug in acidcat) |
| `code` | string | yes | A stable dotted id consumers key on: `cap.read`, `cap.list`, `cap.inflate`, `sibling.missing`, `magic.mismatch`, `size.overrun`, `pointer.dangling`, `triage.generic`, `walker.error`, ... Registered in `core/infra/findings.py`; a test pins that every emitted code is registered. |
| `severity` | enum | yes | `alert`, `warn`, `notice`, `info`, today's anomaly scale. Each kind has a default; a walker may raise or lower it. |
| `message` | string | yes | Today's text, unchanged. |
| `node` | node id | no | The node it is about. Absent for a file-level finding. |
| `at` | locator | no | The bytes it is about. |
| `cap` | object | no | For `coverage`: `{name, limit, used}`. |

Consumers select by `kind` and `code`, never by message text. `audit`'s exit
status counts `defect` findings at or above its threshold; `coverage`,
`environment` and `info` never fail an audit. This fixes nine caps that are
reported as defects today, and the two consumers that match message text
(`tui_app/app.py:1040`, `forensics/forced.py:64`).

### 9.1 Limits

```json
{"read_bytes": 67108864, "chunk_payload": 65536, "inflate_bytes": 67108864,
 "work_steps": 4000000, "list_rows": null, "frame_rows": 100000, "depth": 32,
 "decode": false, "hit": ["list_rows"]}
```

The limits the walk ran under, from the `Limits` object the caller passed (the
defaults unless changed), and `hit`: the names of the limits that stopped
something, each with its `coverage` finding. Two walks of the same file with
the same limits give the same Document. `decode: true` is what `deep` meant
when it meant "do the extra decoding work"; `list_rows: null` means each
walker's own display default.

A coverage finding's `cap` says which limit (`name`), the bound the walker
applied (`limit`, the format's own value when it has one) and how much the
file asked for (`used`: the total when the walker knows it, otherwise the
count it reached, which is then a lower bound). Its `code` follows the limit:
`cap.read`, `cap.payload`, `cap.inflate`, `cap.steps`, `cap.list`,
`cap.frames`, `cap.depth`.

## 10. Rows

`rows` (the `--frames` listings: MIDI events, MP3 frames, MDX commands) stay
free-form per format in v1, with two rules: a row's position is a byte locator
under `at` or absent (MP3's hex-string `offset` is converted), and the listing
carries `{"total": n, "kept": k, "cap": c}` so a truncated listing says so.
Rows are not typed in v1.

## 11. How the normaliser maps today's model

| Legacy | v1 |
|---|---|
| `(label, chunks, warns)` from `walk_file` | a Document; `label` is `format.label` |
| chunk `id` | node `name`, and the last step of node `id` |
| `offset`, `extent_len` | `extent` |
| `payload_base`, `payload_len` | `payload` |
| `geometry` | `geometry` |
| `size` | dropped (it meant two different things; `extent` and `payload` are the answers) |
| flat list | `children` by enclosure (section 5.2), gaps as `unwalked` nodes |
| field `off` (relative) | `at.off` = `payload_base + off` (absolute) |
| field `off = None` | no `at` |
| field `value` | `display` = `str(value)` as today's renderers print it; `value` = `raw` if present, else a number parsed from an int-valued `value`, else the decoded bytes for a typed field, else `value` unchanged |
| field `enc` | `type` per the mapping table, `type_source: "enc"` |
| field `raw` | `value` |
| field `xref` | `ptr` |
| neither `enc` nor a declared `type` | `type` inferred where the bytes admit exactly one reading consistent with `value` (`type_source: "inferred"`), else `display` |
| rows | `rows`, positions converted |
| `file_warns`, chunk `warnings`, anomalies | `findings`, with `kind` and `code` (strings from `coverage()` become `kind: coverage`; any other string becomes `kind: defect`, `code: legacy` until its walker migrates) |

Inference is conservative on purpose. A field whose bytes read the same in both
byte orders (25 multi-byte int fields in the seeds do) is inferred only when the
format's native order is known, and never counted in the `typing` floor.

## 12. Typing

Per Document, and summed per format by `acidcat formats`:

```json
{"fields": 41, "positioned": 38, "typed_declared": 12, "typed_enc": 9,
 "typed_inferred": 14, "caps_declared": 1, "caps_inferred": 2,
 "nodes": 9, "nodes_unwalked": 0, "findings_legacy": 0}
```

Only `typed_declared` and `typed_enc` count toward the floor a new walker must
meet. The floor starts at today's measurement and only rises.
`findings_legacy` counts findings still carrying `code: legacy`; it only falls.

## 13. Addresses

One text syntax names a place, everywhere a location is accepted: the CLI
(`od`, `carve`, `probe`, `edit`, `inspect --only`), `Document.field()`,
`Document.node()`, the MCP tools, and bookmarks. Every report prints ids in
this form, so output pastes back as input.

```
ADDR   := [LAYER ':'] TARGET [RANGE]
LAYER  := integer                         0 is the file, and the default
TARGET := NODE ['#' KEY]                  RIFF/fmt_   RIFF/fmt_#sample_rate
        | '@' OFFSET                      @0x5d1000   @1234
NODE   := id | glob | name                RIFF/LIST~2   RIFF/*   **/data   fmt
RANGE  := '+' LEN                         from TARGET's start
        | '..' END                        to an absolute end
        | '[' OFF ':' LEN ']'             relative to TARGET's payload
```

- A node address means its payload; a field address means the field's `at`.
- A bare `name` resolves when exactly one node has that name; otherwise the
  error lists the candidates as full ids.
- Globs are accepted where a list makes sense (`inspect --only`) and are an
  error where one place is needed.
- `1:lh5/header#frames` is the YM frame count inside the unpacked layer.
- Numbers accept `0x` hex and decimal.

## 14. Conformance (the test)

Run on every seed in `tests/seeds.py`, and on `ACIDCAT_HUNT_CORPUS` in the
release tier. Each rule has a shrink-only ledger for known exceptions, in the
style of `KNOWN_COLLISIONS`.

1. The Document validates against `node-v1.schema.json`.
2. Every byte locator is inside its layer: `0 <= off` and `off + len <= length`.
3. Every positioned field lies inside its node's extent, in the same layer,
   unless it is `remote`, in which case it lies inside its layer.
4. Children lie inside their parent's extent; siblings do not overlap.
5. Node ids and field keys are unique.
6. Every derived layer re-derives: decoding `sources` with `decoder` gives
   `length` bytes and reproduces its `verdict`. `mapping: exact` is checked
   byte for byte against the parent.
7. **Re-read**: every field with `type_source` `declared` or `enc`, a
   fixed-width type and an `at` is read from its bytes, transformed by
   `xform`, and must equal `value`. Inferred types are excluded because they
   pass by construction.
8. Every `ptr` lands inside its layer, or carries a `pointer.dangling` finding.
9. Every finding's `code` is registered, and no `coverage` limit hit is
   missing from `limits.hit`.
10. An `audio` cap's parameters equal the fields it names.
11. Degrade, never raise: no seed and no corpus file makes the normaliser
    raise.

## 15. Versioning and compatibility

- `contract: 1` covers additive changes: new keys, new types, new transforms,
  new caps, new decoders. Consumers ignore keys they do not know, as the README
  already requires.
- Removing or renaming a key, or changing a key's meaning, is `contract: 2`.
- The contract ships as **2.0.0** (decisions D2 and D25). From 2.0.0,
  `inspect --json` emits v1 and nothing else, `acidcat.walk()` returns a
  Document, and every internal consumer (inspect, od, chunks, carve `--field`,
  the explorer, anomalies, lsb, provenance, extract, the TUI) reads v1. There
  is no `--contract 0`: acidcat has not been announced, so there is no
  installed base to carry a legacy shape for. The legacy chunk dicts stay as
  the walkers' internal output until each walker migrates, and are not public.
- The README's output rule ("removing or renaming a key is a breaking change")
  is honoured by the major version, and holds again from 2.0.0 onward.
- The TypedDicts in `core/infra/contract.py` mirror the schema; a test pins
  that every schema property has a TypedDict key and vice versa.

## 16. Bounds and safety

- Every decoder in the registry takes a cap, and stops as soon as its output
  would pass the cap (streaming), never after. The pre-step fixes the one
  decoder that checks after inflating (VGZ).
- Layer bytes are decoded lazily and cached per session with a byte budget;
  over budget the cache evicts, it does not fail.
- `crypto: format` layers need the `crypto` extra; without it the layer is
  declared with `verdict.result: "unverified"` and `length_known: false`, and
  `layer_bytes` raises a clear missing-extra error.

## 17. Examples

### 17.1 A WAV, abridged

```json
{"contract": 1, "producer": {"name": "acidcat", "version": "2.0.0"},
 "format": {"id": "wav", "label": "RIFF/WAVE", "family": "riff"},
 "file": {"size": 172},
 "layers": [{"id": 0, "name": "file", "kind": "file", "length": 172}],
 "nodes": [
  {"id": "RIFF", "name": "RIFF", "kind": "container",
   "extent": {"layer": 0, "off": 0, "len": 172},
   "payload": {"layer": 0, "off": 8, "len": 164}, "geometry": "declared",
   "summary": "WAVE, 164 bytes",
   "fields": [
    {"name": "id", "key": "id", "at": {"layer": 0, "off": 0, "len": 4},
     "type": "fourcc", "type_source": "declared",
     "value": "RIFF", "display": "RIFF", "note": ""}],
   "caps": {},
   "children": [
    {"id": "RIFF/fmt_", "name": "fmt", "kind": "chunk",
     "extent": {"layer": 0, "off": 12, "len": 24},
     "payload": {"layer": 0, "off": 20, "len": 16}, "geometry": "declared",
     "summary": "PCM, 2 ch, 44,100 Hz, 16-bit",
     "fields": [
      {"name": "sample_rate", "key": "sample_rate",
       "at": {"layer": 0, "off": 24, "len": 4},
       "type": "u32le", "type_source": "enc",
       "value": 44100, "display": "44100", "note": "", "unit": "Hz"}],
     "children": [], "caps": {}},
    {"id": "RIFF/data", "name": "data", "kind": "audio",
     "extent": {"layer": 0, "off": 36, "len": 136},
     "payload": {"layer": 0, "off": 44, "len": 128}, "geometry": "declared",
     "summary": "128 bytes, 32 frames", "fields": [], "children": [],
     "caps": {"audio": {"codec": "pcm_s16le", "rate": 44100, "channels": 2,
                        "bits": 16, "frames": 32, "source": "inferred",
                        "fields": ["RIFF/fmt_#sample_rate",
                                   "RIFF/fmt_#channels",
                                   "RIFF/fmt_#bits_per_sample"]},
              "carve": {"ext": ".pcm", "source": "inferred"}}}]}],
 "findings": [],
 "limits": {"read_bytes": 67108864, "chunk_payload": 65536,
            "inflate_bytes": 67108864, "work_steps": 4000000,
            "list_rows": null, "frame_rows": 100000, "depth": 32,
            "decode": false, "hit": []},
 "typing": {"fields": 2, "positioned": 2, "typed_declared": 1,
              "typed_enc": 1, "typed_inferred": 0, "caps_declared": 0,
              "caps_inferred": 2, "nodes": 3, "nodes_unwalked": 0,
            "findings_legacy": 0}}
```

### 17.2 A packed YM, abridged

```json
{"layers": [
  {"id": 0, "name": "file", "kind": "file", "length": 1503},
  {"id": 1, "name": "unpacked YM6", "kind": "derived", "parent": 0,
   "from_node": "lh5", "sources": [{"parent_off": 34, "len": 1468}],
   "mapping": "opaque", "decoder": {"name": "lha.lh5", "params": {}},
   "length": 140155, "length_known": true, "crypto": "none",
   "verdict": {"result": "verified", "method": "crc16", "detail": "0x3A51"}}],
 "nodes": [
  {"id": "lha_header", "kind": "record",
   "extent": {"layer": 0, "off": 0, "len": 34},
   "fields": [{"name": "packed_size", "key": "packed_size",
               "at": {"layer": 0, "off": 7, "len": 4},
               "type": "u32le", "type_source": "declared",
               "value": 1468, "display": "1,468", "note": ""}]},
  {"id": "lh5", "kind": "container",
   "extent": {"layer": 0, "off": 34, "len": 1468},
   "caps": {"descend": {"layer": 1, "source": "declared"},
            "render": {"engine": "ym", "source": "declared"}},
   "children": [
    {"id": "lh5/header", "kind": "record",
     "extent": {"layer": 1, "off": 0, "len": 34},
     "fields": [{"name": "frames", "key": "frames",
                 "at": {"layer": 1, "off": 12, "len": 4},
                 "type": "u32be", "type_source": "declared",
                 "value": 8755, "display": "8,755",
                 "note": "2:55.10 at 50 Hz"},
                {"name": "duration", "key": "duration",
                 "type": "derived", "type_source": "declared",
                 "value": 175.1, "display": "2:55.10", "note": "",
                 "unit": "s", "derived_from": ["frames", "frame_rate"]}]}]}]}
```

### 17.3 A path-located field (Bitwig multisample, abridged)

```json
{"name": "root_key", "key": "root_key",
 "at": {"kind": "path", "layer": 1, "syntax": "xml-steps",
        "path": ["multisample", "sample[3]", "key"]},
 "type": "display", "type_source": "none",
 "value": 60, "display": "60 (C4)", "note": ""}
```

### 17.4 Reserved: a scatter layer (CD-XA stream, not in 2.0)

```json
{"id": 2, "name": "XA file 1 channel 0", "kind": "derived", "parent": 0,
 "from_node": "xa/f1c0", "mapping": "exact",
 "sources": [{"parent_off": 24, "len": 2324, "layer_off": 0},
             {"parent_off": 18840, "len": 2324, "layer_off": 2324}],
 "decoder": {"name": "xa.stream", "params": {"file": 1, "channel": 0}},
 "length": 4648, "length_known": true, "crypto": "none",
 "verdict": {"result": "unverified", "method": "none", "detail": ""}}
```

## 18. Not in v1

- CPU address spaces (reserved as `maps_to`, section 3).
- Typed rows.
- A format grammar. The grammar engine stays parked; a contract is output, not
  a format language, though a grammar would have a target to emit.
- Undoing DRM. `crypto: format` is the only decryption a layer does.
