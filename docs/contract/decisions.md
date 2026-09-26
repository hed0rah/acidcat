# Node contract: decision record

2026-09-25. The decisions behind [node-v1.md](node-v1.md) and
[architecture-2.0.md](architecture-2.0.md), made in a review session before
any code was written. Each entry gives the question, the
answer, the reason, and what it commits us to. Entries marked
**(recommended, accepted)** were decided by accepting the reviewer's
recommendation rather than by picking between options; they deserve a second
look in review.

Status: DRAFT for review. Nothing below is implemented. The next step is review, then the
pre-step (1.8.6). Evidence for the engine, surface and data decisions (E, S, K)
comes from a second round of four audits: the CLI, format registration, walker
I/O and limits, and the API, index, MCP and editing paths.

## Evidence the decisions rest on

Measured on 7d8f781 (1.8.5) by walking every seed in `tests/seeds.py` with
`deep=True`, plus four read-only audits (git history, every consumer of the
chunk dict, the TUI, the packed formats).

| Measure | Value |
|---|---|
| Fields over 91 seeds | 1,074 |
| ... with a display-string `value` | 710 (182 of them numbers written as text) |
| ... with a machine int `value` already | 363 |
| ... with `enc` | 99 |
| ... with `xref` | 54 |
| Formats with no typed field at all | 61 of 91 |
| Formats with no positioned field at all | 10 (labx, vital, xpm, xpn, xtd, sigmf, multisample, mpcpattern, akp, ni) |
| Chunks with stated children | 0 |
| Spellings of "unpositioned" | 3: `off=None` (382 sites), `_f(0, 0, ...)` (69 sites), `off=None` + `xref` as location (HES, SPC, CMF) |
| Multi-byte int fields that read the same in both byte orders | 25 |
| Places in `tui_app/` keyed on a format name, region kind or field name | about 30, plus `_BE_FMTS` in core |

**Bugs the re-read check found.** The first five chunks are marked
`geometry: declared` and all pass the existing "field inside its payload"
test, because a shifted field is still inside the payload:

| Walker, chunk | Shift | Cause |
|---|---|---|
| krz `type1` (every object) | 4 bytes | header fields measured from the block start, `payload_base` declared 4 later; body fields measured from a third base |
| vgm `Gd3` | 12 bytes | fields measured from the chunk start, `payload_base` past the 12-byte header |
| dff `FRM8` | 12 bytes | same |
| rmid `data` | 8 bytes | same (the payload must stay the SMF, so the fields move, not the base) |
| psf `tags` | 5 bytes | same |
| gf1pat `GF1` | 8 bytes | no `payload_base` declared, so the default (offset + 8) applied to a header with no id+size prefix (found while building the 1.8.6 test) |
| ogg `OggS` | n/a | a derived value (`codec`) positioned on the page's `OggS` capture pattern (found while building the 1.8.6 test) |

Also found: dff `PROP.sample_rate` reads past its bytes; `formats/vgm.py`
inflates the whole gzip stream before checking its cap (a decompression bomb,
though the docstring says it is bounded); `forensics/anomalies.py:409` reports
a payload-relative offset as absolute; `tests/test_concealment.py` and
`tests/test_detect_fallback.py` fail rather than skip without the `analysis`
extra; `textual>=0.60` has no ceiling, against the repo's own rule.

## Pre-step (ships as 1.8.6, before the contract)

| # | Question | Answer | Reason | Commits us to |
|---|---|---|---|---|
| P1 | How does a header field (magic, size, form type), which sits before `payload_base`, state its position? | Negative `off`, relative to `payload_base`; the invariant becomes "a field lies inside its chunk's extent" | `base + off` arithmetic is unchanged, so no consumer needs edits; the other options either hide the field from every `off is None` check or make `explore` descend into header bytes | fixing krz, vgm Gd3, dff, rmid, psf; widening `test_no_positioned_field_escapes_its_payload` from payload to extent |
| P2 | Which spelling of "unpositioned" survives? | Only `off=None` | One spelling is the only way a consumer can tell "no position" from "zero-length at base+0" | reviewing and rewriting the 69 `_f(0, 0, ...)` sites; turning HES, SPC and CMF's located header fields into positioned fields; a test that bans `(0, 0)` and a non-pointer `xref` |
| P3 | How does the re-read check treat legitimate transforms (count stored minus one, 16.16 fixed point, ASCII digits, masked bits, self-relative offsets)? | A `KNOWN_TRANSFORMS` ledger in the test, shrink-only | The field model stays unchanged until 2.0.0 gives transforms a vocabulary (`xform`) | a ledger of about ten entries today, each with a reason |
| P4 | Where does the re-read check run? | Every seed always, and `ACIDCAT_HUNT_CORPUS` in the release tier | Real files take paths the minimal seeds do not; matches `test_chunk_geometry` | a new test module in the style of `test_chunk_geometry.py` |

Also in the pre-step, because they are bugs rather than design: fix
`anomalies.py:409`, and make the two test modules skip cleanly without the
`analysis` extra. Bounding the VGZ inflate (stream with a cap, like
`gunzip_capped`) is a separate task already under way.

## Contract shape

| # | Question | Answer | Reason | Commits us to |
|---|---|---|---|---|
| C1 | What are v1 field offsets relative to? | Absolute within the layer | No base to get wrong; the pre-step bugs are all "wrong base" | the normaliser converts `payload_base + off` once |
| C2 | How are types the normaliser guesses treated? | Marked: `type_source` is `declared`, `enc`, `inferred` or `none` | An inferred type passes the re-read check by construction, so counting it would make the check prove nothing; mirrors geometry's declared/defaulted | the re-read check and the coverage floor count only `declared` and `enc` |
| C3 | How are fields located in XML, JSON and archive-metadata formats? | A path locator in v1 (`json-pointer` or `xml-steps`) | A byte range is the wrong question for ten formats, and "unpositioned" undersells them | locator is a union type; the TUI shows paths, and later highlights them in a text layer |
| C4 | Where do `children` come from? | Derived by the normaliser from extent enclosure; walkers declare only when ambiguous; gaps become `unwalked` nodes; siblings never overlap | Enclosure is already the implicit rule (f0cece4); declaring everywhere means touching 58 walkers first | a tree builder in the normaliser; honest byte maps (no false "unaccounted" in a clean WAV) |
| C5 | Dicts or dataclasses in code? (doc decision 1) | Dicts at runtime, with TypedDicts in `core/infra/contract.py` | Walkers already emit dicts, they serialise as they are, and TypedDicts give readers and type checkers the shape at no runtime cost | a test pinning the TypedDicts to the schema |
| C6 | How is the schema validated? | `jsonschema` in the `dev` extra only | The schema file stays the single source of truth; the runtime keeps one hard dependency | `jsonschema` added to `dev` |

## Layers

| # | Question | Answer | Reason | Commits us to |
|---|---|---|---|---|
| L1 | How does a layer report how it was checked? | A graded verdict: `verified`, `length-only`, `unverified`, `failed-shown`, with a method | Decoders differ (CRC-16, exact length, CRC over compressed bytes, deliberately ignored CRCs); a failed check is still worth showing | the `verdict` object |
| L2 | What do layers cover? | Contiguous derived buffers now (including capped prefixes), with the record ready for scatter: `sources` is a list of extents, `mapping` is `opaque` or `exact` | CD-XA streams, ISO extents and Wii clusters then arrive as new decoders, not a new contract version; `exact` mapping lets a selection in a stream light the real bytes in the image | three extra keys today; CPU bank switching is kept out of layers (reserved `maps_to` node annotation) |
| L3 | Can decryption be a layer? | Format crypto yes (a key that is part of the format, like the Wii common key); user or title DRM never | Decrypting a disc format is reading the format; undoing licence DRM is not what this tool is for | `crypto: none or format` on the layer; the rule stated in the spec |
| L4 | Is a decoded image walked by the normal walker? | Yes, and add an in-memory walk path | VGZ then gets its data blocks back and YM its digidrums; `walk_bytes`' temp file is 84.8% of its time | walkers accept a buffer as well as a path (a mechanical change, done as each walker is touched) |
| L5 | Where does "can this run here" (ffplay present, an extra installed) live? | Caps are static; the consumer checks availability at run time | The same file must always give the same JSON, so tests and golden screenshots stay stable | the TUI greys an action out with a reason instead of hiding it |

## Output and versioning

| # | Question | Answer | Reason | Commits us to |
|---|---|---|---|---|
| D2 | When does `inspect --json` switch to v1? (doc decision 2) | At the contract release, with no legacy shape | acidcat has not been announced, so there is no installed base to protect; carrying two shapes costs code and tests for nobody | every internal consumer migrates in the same release (inspect, od, chunks, carve `--field`, explorer, anomalies, lsb, provenance, extract, TUI) |
| D25 | Is the contract release 1.9.0 or 2.0.0? | **2.0.0**, and make it count | Changing `value` from display to machine value in place is a breaking change under the README's own rule. With no users, take the major version once and make every breaking change worth making inside it (sections E, S, K below) | the release plan below; `acidcat.open()` returns a Document from 2.0.0 |

## Engine (2.0)

Evidence: no walker uses mmap and every walker takes a path, so `walk_bytes`
writes a temp file (1.9x slower at 300 bytes, 400x at 64 MB) and RMID re-walks
through one; about 60 caps across 38 constants from 64 KB to 512 MB, nine cap
hits reported as defects, coverage notes reported twice; warnings are plain
strings and two consumers match their text; a format is registered in about 20
places, about 11 files per new format of which 5 to 7 are bookkeeping, and the
sniff order lives in comments.

| # | Question | Answer | Reason | Commits us to |
|---|---|---|---|---|
| E1 | Should walkers read a Source instead of a path? | Yes: `view`, `head`, `file`, `sibling` (through a Resolver), `child` for decoded layers; mmap, bytes and slice backends | Layers need in-memory walks constantly; zero-copy views end the temp files; siblings become an explicit, testable dependency | porting about 45 head-read walkers (one line each) and the seek-based helpers (riff, aiff, flac, mp3, mp4); an AST test banning `open` and `os.path` in walkers |
| E2 | One Limits object instead of ~60 caps and the overloaded `deep`? | Yes, with a shared Budget across nested layers | One place to read and set limits; a zip of gzips cannot multiply the allowance; a cap hit is structurally unable to be reported as a defect | `limits.take` / `limits.hit` in every walker; `Document.limits`; `--limit NAME=VALUE` on the CLI |
| E3 | Structured warnings? | One record for walker warnings and forensic findings: `kind`, `code`, `severity`, `message`, `node`, `at`, `cap` | Consumers key on stable codes, not text; `environment` stops blaming the file for a missing sibling; one list ends double reporting | a code registry (`core/infra/findings.py`); about 469 sites migrate mechanically; `code: legacy` counted until gone |
| E4 | Is a format one record? | Yes: `FormatSpec` in `core/infra/registry.py`, every existing list derived from it, a golden sniff test written first | One source of truth per format; sniff order becomes data; adding a format drops to the real work | a golden test over all seeds and known near-misses before sniff moves; lazy imports; variants as one spec |

## Surface (2.0)

Evidence: 29 verbs with heavy overlap (`chunks`, `dump`, `survey` still
RIFF-only); `--format` means four things; five syntaxes address a chunk or
field and none is the contract's; missing extras exit 1 against the README's
rule; the public API is the tuple; five separate write paths; MCP sees only
the catalogue.

| # | Question | Answer | Reason | Commits us to |
|---|---|---|---|---|
| S1 | Consolidate the CLI? | Fifteen verbs (table in architecture-2.0.md section 8); retired names removed, not aliased | No users to break; aliases would keep five syntaxes alive | rewriting the cross-verb test suites, README, CHEATSHEET and the skill |
| S2 | One address syntax? | The ADDR grammar (node-v1.md section 13) everywhere a location is taken | Output pastes back as input; one parser, one set of errors | retiring `--chunk`, `--field`, `--offset`, `--at`, `--region` |
| S3 | Public Python API? | `acidcat.open()` returning a read-only `Document` view over the v1 dict; tuple API removed; `read_metadata` exported; generated API reference | Discoverable, typed access without giving up the dict as the source of truth | `acidcat-lab` migrates in the same release |
| S4 | Editing and MCP? | One `doc.edit()` front door (typed byte patch or `edit` cap profile, then verify, optional repair, commit); MCP read-only structure tools, plus `edit_field` only with `--allow-writes` | One verified write path for CLI, TUI and MCP; writes over MCP are opt-in | inferred types are not editable without `--force` |

## Data, docs and platform (2.0)

| # | Question | Answer | Reason | Commits us to |
|---|---|---|---|---|
| K1 | What does the index store? | Fixed columns filled from Documents for every format through canonical aliases, plus a curated `fields` table (audio cap params, canonical bindings, caps); schema v4 | About 80 formats contribute nothing to the index today; a full EAV table would be large and go stale with node ids | new filters (`--rate`, `--channels`, `--bits`, `--can`); reindex on producer version change; `ctx` and `CTX_KEYS` retire |
| K2 | Anatomy pages? | One shared builder; byte maps generated from walking real specimens with declared kinds; prose in sidecars keyed by address | The 22 builders are copies that never read the walker, and copied bugs have already spread twice | specimen fixtures per page; a test that prose addresses still exist |
| K3 | The grammar engine? | Delete it | The re-read check against declared types replaces its oracle role for every format, not two; one less field model | moving `test_ctx_keys_covers_walker` out first |
| K4 | Python floor? | `>=3.11`; CI 3.11 to 3.14 | 3.10 reaches end of life in October 2026 | updating CI and classifiers |

## Shipping

| # | Question | Answer | Reason | Commits us to |
|---|---|---|---|---|
| R1 | How is 2.0 released? | Pre-releases on PyPI (`2.0.0a1`, `a2`, `b1`), then `2.0.0`; the TUI arc is 2.1 to 2.5 | pip ignores pre-releases unless asked, so real installs can be tested safely | the milestone plan below |
| R2 | Branch model? | A long-lived `next` branch; each milestone is its own PR into it with the full CI matrix; `main` stays on 1.8.x and is merged forward | Keeps 1.8.x fixable while 2.0 is built; every milestone gated by CI | creating `next` (needs your go-ahead when building starts) |
| R3 | When to announce? | With the new TUI (2.1/2.2), quietly releasing 2.0.0 first | The first impression should be the best version | anatomy pages and a demo recording ready for the announcement |

## TUI

| # | Question | Answer | Reason | Commits us to |
|---|---|---|---|---|
| T1 | Split `app.py` into a Textual-free `DocumentModel`? (doc decision 3) **(recommended, accepted)** | Yes, keyed by node id paths; widgets observe it; `app.py` becomes wiring | The audit found real seams (working copy and undo, play decisions, `_FRAME_ATTRS`); the node index keyed by `TreeNode` is the part to rebuild | model tests that run without Textual |
| T2 | Layout (doc decision 4) **(recommended, accepted)** | Tree 35 / bytes 65 at 120 columns and wider; inspectors collapse to toggles below that | The tree pane is mostly empty today; fixed panes fold on narrow terminals | responsive layout rules and narrow-terminal golden screenshots |
| T3 | Textual pin | `textual>=8.0,<9` | The floor, not the ceiling, was the constraint: `>=0.60` promised support for a Textual eight majors old. 8.2.8 is the current release | using all of Textual 8; testing before 9.0 |
| T4 | Snapshot style | SVG via `pytest-textual-snapshot` (dev only), one CI cell, regenerated deliberately | The new views say most of what they say in colour, which a text export loses | a new dev dependency; model tests cover what pixel graphics cannot |
| T5 | Which ambitious features are in the plan | All four: virtual-scroll hex and minimap; a pixel-graphics extra (`textual-image`, Kitty and Sixel with a half-block fallback); live chip scopes while SPC, SID and YM play; a caps-driven command palette, an acidcat theme from the house tokens, and `textual-serve` | The TUI should be the reason to install acidcat, and the emulators already exist | optional extras for graphics and web; see the release plan |
| T6 | Playback for YM and SNDH (doc decision 5) **(recommended, accepted)** | YM2149 player now (it also feeds the chip scopes); SNDH playback (68000 plus the ST's timer chip) not planned | YM is a register writer over a three-voice chip; SNDH is a CPU emulator project | a `ym` render engine |

## Process

| # | Question | Answer | Reason | Commits us to |
|---|---|---|---|---|
| X1 | Branching (doc decision 6) **(recommended, accepted)**; refined by R2 | One branch per milestone, merged by PR on a green full tier (into `next` during 2.0) | Since September work has gone straight to `main`, so CI checks after the fact; the TUI rebuild touches one large file across several releases | PR per release; CI's three-OS matrix gates each merge |
| X2 | Where the spec lives | `docs/contract/` in the repo, pushed for review, no code until approved | Reviewable by any agent or person, versioned with the code it describes | this directory |

## Order after review (2026-09-26)

The review accepted the architecture in direction and changed the order:
engine first (Source, Limits, structured findings, layers), then the TUI
(DocumentModel, context hex, inspectors, layers, caps-driven actions: the 2.1
to 2.5 work moves ahead of the CLI), then one breaking pass (CLI
consolidation, `acidcat.open()`, the edit API, JSON v1 as default, Python 3.11,
`core/grammar/` removed). MCP structure tools and index schema v4 are parked.
All 2.0 work lands on `next`, one commit per finished milestone; non-breaking
milestones ship from `main` as 1.9.x after the corpus check. The CLI mapping
(every command and flag, what it becomes, the alias that keeps it working
through 2.x) is written as `docs/contract/cli-2.0.md` before any CLI code.

E1 as built: walker signatures stay `inspect_x(filepath, ...)` with `filepath`
a path or a Source; siblings come from a fixed directory lookup rather than an
injected resolver; `head`, `child` and the slice backend wait for layers.
architecture-2.0.md section 2 describes what exists.

The table below is the plan as first written.

## Release plan

| Release | Branch | Ships | Done when |
|---|---|---|---|
| 1.8.6 | `main` | The pre-step: five displaced chunks fixed, one "unpositioned" spelling, `xref` only a pointer, the re-read test with its ledger, the anomalies offset, clean skips without extras (the VGZ bound is a separate task already running) | the re-read test passes on every seed and the hunt corpus with only ledgered transforms; full tier green |
| 2.0.0a1 | `next` | Engine and contract: the golden sniff test, `FormatSpec` registry, `Source`, `Limits`, structured findings; the normaliser, schema, conformance test and `typing` counts; the decoder registry and layers (YM, SNDH, VGZ, PSF, Ableton); Python 3.11 floor; grammar engine removed | every seed and the hunt corpus conform; golden sniff unchanged; `1:lh5/header#frames` has a byte range; `carve --layer 1` is byte-identical to the verified image |
| 2.0.0a2 | `next` | Surface: `acidcat.open()` and the Document views, the edit API, the ADDR grammar, the fifteen-verb CLI, MCP structure tools, index v4, the explorer on the Document, `acidcat-lab` migrated | no consumer reads a legacy chunk dict; README exit-code rule holds for every verb |
| 2.0.0b1 | `next` | Docs: generated anatomy byte maps with prose sidecars, the API reference, README, CHEATSHEET, ARCHITECTURE and the skill rewritten | every page's byte map equals its walk; every public name documented |
| 2.0.0 | `next` into `main` | Fixes from the pre-releases | full tier green on the whole matrix |
| 2.1 | | TUI frame: `DocumentModel`, virtual-scroll hex, minimap, status line, responsive 35/65, SVG snapshot harness, `textual>=8.0,<9` | a selected field is lit in context at any file size; model tests run without Textual |
| 2.2 | | TUI inspectors: field and data inspectors, layer breadcrumb and descend, caps-driven actions and command palette | no format name in `tui_app/` |
| 2.3 | | TUI polish: acidcat theme, generated help, open dialog, bookmarks (stored as ADDR plus a locator fallback), text-layer viewer | no narrow-terminal overrun |
| 2.4 | | `graphics` extra (Kitty/Sixel byte maps, waveforms, spectrograms) and `web` extra | identical layout in the half-block fallback |
| 2.5 | | Live chip scopes for SPC, SID and YM (with the YM player) | scopes track the rendered audio in model tests |

Walkers migrate from inferred to declared types, caps and finding codes
alongside, a few per release; the `typing` floor rises and `findings_legacy`
falls with them. New formats keep shipping in between, on `main` for 1.8.x and
as `FormatSpec`s on `next`.

## Open for review

1. **Scope of 2.0.0a1.** It carries the engine groundwork and the contract
   together. They could be split (a1 engine, a2 contract) if review prefers
   smaller milestones; the cost is one more migration of the consumers.
2. **Node ids** (spec 5.1) are stable across runs, not across versions. Is
   that enough for bookmarks, or should bookmarks store a locator as a
   fallback?
3. **Transforms** (spec 6.2): is the eight-entry vocabulary right, and should
   an unlisted transform really force `type: display` rather than allowing a
   free-text `xform`?
4. **Rows** stay untyped in v1. MIDI events and MP3 frames are the largest
   listings; typing them could wait for v1.x.
5. **The `size` key is dropped** in v1 (it meant payload length for RIFF and
   box length for MP4). Anyone scripting against `inspect --json` today
   loses it; `extent.len` and `payload.len` replace it.
