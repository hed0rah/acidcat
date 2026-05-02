# acidcat codebase review

state: v0.5.3 on main, 232 passing tests, post real-world testing
date: 2026-05-02
scope: full-tree review with three independent subagent passes
method: read-only audit. no code changed. no tests modified.

---

## status update (2026-05-02 post-fix)

All 26 actionable findings have been addressed in 13 stacked commits
on main, ahead of `origin/main`, not yet pushed. Test count grew from
232 to 254. The verification addendum below caused F-03, F-11, and
F-19 to be withdrawn (subagent errors), and F-27 was verified as
already correct (RIFF padding handled at line 213-215). F-01 was
downgraded from CRITICAL to HIGH after reading the actual bound on
the search loop.

Per-finding fix commits, in chronological order:

| commits | findings | files |
|---------|----------|-------|
| `31894a1` | F-04 F-09 F-17 F-20 F-24 F-29 (annotations + descriptions) | mcp_server.py, commands/index.py |
| `b9da2b8` | F-01 F-05 (parser hardening) | core/serum.py, core/riff.py |
| `6d238e6` | F-07 (FTS5 syntax error guard) | mcp_server.py, tests |
| `6695579` | F-13 F-23 (Windows case + path_hash 12) | core/paths.py, core/registry.py, tests |
| `ae81c6c` | F-02 (discover_libraries dry_run default flip) | mcp_server.py, tests |
| `52bf095` | F-06 F-08 (MIDI sysex bounds, find_compatible keyless) | core/midi.py, mcp_server.py, tests |
| `7e9ec3c` | F-14 F-15 (transaction discipline) | core/index.py, core/registry.py |
| `eb8e1c0` | F-18 (describe_sample -> set_sample_description rename) | mcp_server.py, README.md, tests |
| `e1448e3` | F-22 (schema_version mismatch error) | core/index.py, core/registry.py, tests |
| `eda8d2a` | F-10 F-12 F-16 F-25 F-26 F-28 (polish batch) | 6 files + tests |
| `cd09567` | F-21 (format dispatch by magic-byte sniff) | commands/index.py, tests |

The remainder of this document is preserved as the original audit
report. Read the verification addendum below for the per-finding
status with line-of-evidence citations.

---

## review structure

three independent subagents covered non-overlapping concerns:

1. **audio format parsers** (`core/riff.py`, `core/aiff.py`, `core/midi.py`,
   `core/serum.py`, `core/tagged.py`, `core/detect.py`, plus the format
   dispatch in `commands/index.py`). researched each filetype against
   public specs (RIFF, AIFF/AIFC, SMF 1.0, ID3v2.4, Vorbis Comment,
   MP4 atoms) for edge cases and adversarial inputs.
2. **storage and registry architecture** (`core/index.py`,
   `core/registry.py`, `core/paths.py`, `commands/index.py`,
   `commands/query.py`). covered SQL correctness, transaction
   boundaries, schema migration posture, path normalization edge cases,
   no-overlap guard, fan-out logic, FTS5 sync, performance.
3. **MCP server and tool surface** (`mcp_server.py`, ~1300 lines).
   covered description hygiene (cost prefixes), MCP tool annotations,
   input schemas vs handlers, path traversal, FTS5 query injection,
   error path leakage, cost-model truthfulness, concurrency, naming
   ergonomics.

findings are coded by severity. CRITICAL and HIGH are real bugs or LLM
safety-model gaps. MEDIUM are correctness or UX issues that compound.
LOW are nits and edge cases worth knowing about but not urgent.

---

## summary table

| sev | id | finding | file:line |
|-----|----|---------|-----------|
| CRITICAL | F-01 | serum O(n^2) JSON brute-force is a DoS vector | `core/serum.py:42-43` |
| CRITICAL | F-02 | `discover_libraries` defaults to `dry_run=false` | `mcp_server.py:1068, 1500` |
| CRITICAL | F-03 | `register_library` MCP tool marked `idempotentHint=true` but is destructive on re-call | `mcp_server.py:1453` |
| CRITICAL | F-04 | `analyze_sample` and `detect_bpm_key` lack `idempotentHint=false` | `mcp_server.py:1375, 1388` |
| HIGH | F-05 | WAV unbounded chunk read can OOM on malformed files | `core/riff.py:67` |
| HIGH | F-06 | MIDI sysex VLQ underrun crosses track boundary silently | `core/midi.py:143-146` |
| HIGH | F-07 | FTS5 syntax errors leak through as SQL OperationalError | `mcp_server.py:256-259, 1610-1612` |
| HIGH | F-08 | `find_compatible` with keyless target returns unfiltered keys | `mcp_server.py:541-568` |
| HIGH | F-09 | `analyze_sample` cost description hides librosa cold start | `mcp_server.py:1363` |
| MEDIUM | F-10 | BPM filename ceiling at 200 rejects DnB (170-180 is plausible) | `core/detect.py:33-34` |
| MEDIUM | F-11 | SMPL note 0 (C-1) treated as a real key | `commands/index.py:875-880` |
| MEDIUM | F-12 | AIFC compression type validation incomplete | `core/aiff.py:140-143` |
| MEDIUM | F-13 | Windows path case collisions create duplicate registry rows | `core/paths.py:31-37` |
| MEDIUM | F-14 | `rebuild_fts_for_path` is not transactional, can orphan FTS rows | `core/index.py:257-289` |
| MEDIUM | F-15 | `_assert_no_overlap` TOCTOU race on concurrent register | `core/registry.py:103-136` |
| MEDIUM | F-16 | `_close_all` silently swallows close exceptions | `mcp_server.py:63-68` |
| MEDIUM | F-17 | destructive MCP tools lack a "Destructive:" cost prefix | `mcp_server.py:1435, 1458, 1474, 1520, 1537` |
| MEDIUM | F-18 | `describe_sample` name reads as read-only but writes | `mcp_server.py:1537` |
| MEDIUM | F-19 | `infer_kind` heuristic mis-bins 1-second acid loops as `any` | `mcp_server.py:495-508` |
| MEDIUM | F-20 | `analyze_sample` schema declares `deep` parameter that handler ignores | `mcp_server.py:1370` |
| MEDIUM | F-21 | format dispatch in `_extract_for_index` has order-dependent edge cases | `commands/index.py:847-856` |
| LOW | F-22 | schema-version forward-compat undefined (no migration handler) | `core/index.py:86-87`, `core/registry.py:69-70` |
| LOW | F-23 | path_hash is 8 hex chars (32 bits); birthday collision at ~65k libs | `core/paths.py:90-97` |
| LOW | F-24 | `_walk_and_upsert` does not pass explicit `followlinks=False` | `commands/index.py:749` |
| LOW | F-25 | label collision fallback re-uses deterministic hash | `commands/index.py:579-599` |
| LOW | F-26 | UTF-8 BOM not stripped from ID3/Vorbis values | `core/tagged.py` |
| LOW | F-27 | RIFF chunk padding byte handling edge case | `core/riff.py` |
| LOW | F-28 | `prune_missing` walk_start fixed at start, not end of walk | `commands/index.py:813-816` |
| LOW | F-29 | `_REGISTRY_PATH` global mutable, mild concurrency smell | `mcp_server.py:33` |

---

## CRITICAL

### F-01 - serum O(n^2) JSON brute-force

**file:** `src/acidcat/core/serum.py:42-43`

```python
for end in range(json_start + 50, max_search):
    try:
        return json.loads(raw[json_start:end])
```

the parser scans for the JSON start byte, then tries `json.loads` on
every prefix from start+50 up to `max_search`. on a 1 MB preset that is
~1 million parse attempts per file, each of which does its own scan.
quadratic behavior in file size.

**adversarial vector:** a crafted preset with no valid JSON terminator
spends the full O(n^2) budget per file. an attacker who places a
malformed `.fxp` in a sample pack can stall a full library walk.

**fix concept:** parse with `json.JSONDecoder().raw_decode` which
returns the (object, end_index) tuple in a single linear pass, or
locate the closing brace via balance counting and parse once.

### F-02 - `discover_libraries` defaults to `dry_run=false`

**file:** `src/acidcat/mcp_server.py:1068, 1478-1503`

schema declares `dry_run` with default `False`. description says
*"always call once with dry_run=true first to preview"*. an LLM that
forgets the recommendation invokes the tool with no args and gets a
destructive registry mutation. defaults should encode the spirit of
the description, not contradict it.

**fix concept:** flip default to `True`. force opt-in for the
mutation pass.

### F-03 - `register_library` `idempotentHint=true` is wrong

**file:** `src/acidcat/mcp_server.py:1453, 1028`

handler creates the per-library DB file on call. a second call with
the same args, after the user has indexed samples via the CLI between
the two MCP calls, recreates an empty DB. annotation says it is safe
to retry. an MCP client may retry on transient error and silently
destroy data.

**fix concept:** `idempotentHint=false`. or guard with `if not
os.path.isfile(db_path)` before recreating.

### F-04 - `analyze_sample` / `detect_bpm_key` missing `idempotentHint=false`

**file:** `src/acidcat/mcp_server.py:1375, 1388`

both tools read disk and run librosa. semantics are not idempotent in
the strong sense: if the underlying file changes mid-conversation, a
re-call returns different output. annotations should declare this so
that MCP clients do not cache or coalesce identical-arg calls.

**fix concept:** add `idempotentHint=false` explicitly. cheap one-liner.

---

## HIGH

### F-05 - WAV unbounded chunk read

**file:** `src/acidcat/core/riff.py:67`

```python
chunk_data = f.read(chunk_size)
```

`chunk_size` comes from the file. a malformed WAV claiming a 2 GB chunk
allocates 2 GB. the AIFF parser at `core/aiff.py` already does the
right thing (`f.read(min(chunk_size, 4096))`); WAV did not get the
same treatment.

**fix concept:** cap the read at a few KB, or stream and parse only
the fields actually used (the parser only needs the fmt header and
smpl loop block).

### F-06 - MIDI sysex VLQ underrun

**file:** `src/acidcat/core/midi.py:143-146`

VLQ-encoded sysex length is read after the F0 status byte. if the VLQ
points past the end of the track, the parser silently advances past
the track boundary into the next track's data, producing scrambled
output.

**fix concept:** clamp the sysex read to `min(vlq, track_remaining)`,
treat short reads as a parse error.

### F-07 - FTS5 syntax errors leak through

**file:** `src/acidcat/mcp_server.py:256-259, 1610-1612`

user-supplied `text` goes straight to `samples_fts MATCH ?`. FTS5
metacharacters (`*`, `(`, `)`, `"`, `NOT`, `AND`, `OR`) and unbalanced
syntax cause `sqlite3.OperationalError`. the dispatcher's catch-all
returns `f"internal: {e.__class__.__name__}: {e}"` to the MCP client,
exposing FTS5 internals.

**fix concept:** wrap the FTS query in a try/except that catches
`OperationalError` from this code path specifically and returns a
friendly `{"error": "invalid search syntax"}`. or escape the user
string by quoting it: `f'"{text.replace(chr(34), chr(34)*2)}"'`.

### F-08 - `find_compatible` with keyless target returns everything

**file:** `src/acidcat/mcp_server.py:541-568`

```python
compat_keys = camelot.compatible_keys(target_key) if target_key else set()
...
if sql_keys:
    where.append(f"LOWER(s.key) IN ({placeholders})")
```

if the target sample has no key (drum loop, percussion one-shot), the
key filter is skipped entirely. results then include arbitrary keys.
musically this is sometimes acceptable for drums but the description
is *"harmonically and rhythmically compatible"*. an LLM reading the
docs has no signal that "compatible" silently degrades.

**fix concept:** when target key is null, narrow to also-keyless rows,
or document the fallback explicitly in the tool description.

### F-09 - `analyze_sample` hides librosa cold start

**file:** `src/acidcat/mcp_server.py:1363`

description: `"SLOW (~1-10s)"`. reality: first call in a process pays
30-60s of numba JIT. by call 2, it really is 1-10s. averaging hides
the worst case from the LLM, which then thinks the tool is broken on
the first call.

this is the same pain point that drove the deferred pre-warmed worker
plan. labeling it accurately in the tool description is a free win
that doesn't need the worker.

**fix concept:** description: `"SLOW (~1-10s after warm-up; first
call ~30-60s due to librosa import)"`.

---

## MEDIUM

### F-10 - BPM filename ceiling at 200

**file:** `src/acidcat/core/detect.py:33-34`

`60 <= bpm <= 200`. drum and bass pack at 174 BPM passes. but
producers commonly write half-time filenames at 87 (parsing fine) or
double-time labels like `acid_360.wav` for 180 BPM at half-time
display. ceiling 200 also rejects gabber/hardcore (200-250 BPM). not a
hot path, but the user is in the IDM/acid space and these styles do
land in the catalog.

**fix concept:** raise ceiling to 300. document the band.

### F-11 - SMPL note 0 treated as a real key

**file:** `src/acidcat/commands/index.py:875-880`

WAV `smpl` chunk MIDIUnityNote of 0 maps to C-1, which is below piano
range and almost always a "no key set" sentinel. acidcat ingests it as
a real key. the v0.4 fix already filters this in `commands/info.py`
(the "info C-1 phantom key" change), but the same trap exists on the
index side.

**fix concept:** treat MIDIUnityNote == 0 as null.

### F-12 - AIFC compression type validation

**file:** `src/acidcat/core/aiff.py:140-143`

AIFC `COMM` chunk includes a 4-byte compression type. parser does not
validate it against known values (`NONE`, `sowt`, `fl32`, `fl64`,
etc.). unknown types are silently treated as "AIFF" which produces
the wrong sample rate / interpretation.

**fix concept:** maintain a set of known compression types; warn or
skip on unknown.

### F-13 - Windows path case collisions

**file:** `src/acidcat/core/paths.py:31-37`

```python
def normalize(p):
    return os.path.abspath(p).replace("\\", "/")
```

NTFS is case-preserving and case-insensitive. `C:/MyLib` and
`c:/mylib` refer to the same filesystem object but are two different
strings, so the registry happily stores both as separate libraries.
the no-overlap guard does not catch this either, because the equality
check is case-sensitive. fan-out then queries both, and the dedup-by-
path masks the duplication except in `--list`.

**fix concept:** lowercase on Windows in `normalize`. or compare with
`os.path.normcase` in the overlap guard.

### F-14 - `rebuild_fts_for_path` not transactional

**file:** `src/acidcat/core/index.py:257-289`

DELETE then SELECT then INSERT. if the SELECT returns no row, the
function returns early after the DELETE has already fired, leaving
the FTS table missing a row whose `samples` row still exists. callers
do not wrap in a transaction either.

**fix concept:** wrap the three statements in `BEGIN/COMMIT`, or fold
the logic into a single `INSERT OR REPLACE` after a copy from
`samples`.

### F-15 - `_assert_no_overlap` TOCTOU race

**file:** `src/acidcat/core/registry.py:103-136`

read-then-insert without a writer lock. concurrent `register_library`
calls from two processes can both pass the check then both insert
overlapping rows. unlikely in single-user CLI flow but possible in the
MCP server when CLI registration happens during a long-running MCP
session.

**fix concept:** `BEGIN IMMEDIATE` at the start of `register_library`
to take the writer lock for the duration of the check + insert.

### F-16 - `_close_all` swallows exceptions

**file:** `src/acidcat/mcp_server.py:63-68`

```python
def _close_all(pairs):
    for _, c in pairs:
        try: c.close()
        except Exception: pass
```

masks "database is locked", double-close, and corruption signals. a
debug-level log line on the exception class is enough; the swallow
itself is fine, the silence is what hurts.

**fix concept:** log at warn level inside the except.

### F-17 - destructive MCP tools lack a cost prefix

**file:** `src/acidcat/mcp_server.py:1435, 1458, 1474, 1520, 1537`

read tools open with `"Fast. ..."`, slow ones with `"SLOW. ..."`. the
write/registry-mutating tools open with `"Modify the registry."` or
`"Modify the index."`. a tool-ranking LLM may sort by cost and miss
that "Modify" tools are also irreversible. an explicit
`"Destructive: ..."` lead aligns the description with the
`destructiveHint=true` annotation.

**fix concept:** prefix with `"Destructive."` analogous to the
existing `"Fast."` / `"SLOW."` ladder.

### F-18 - `describe_sample` reads as a getter

**file:** `src/acidcat/mcp_server.py:1537`

natural language "describe the sample" means tell-me-about-it. the
tool writes the description field. an LLM may misroute. body text
clarifies but the name is the trap.

**fix concept:** rename to `set_sample_description` or
`annotate_sample`.

### F-19 - `infer_kind` mis-bins 1-second acid

**file:** `src/acidcat/mcp_server.py:495-508`

a 1.0s clip with `acid_beats=1` maps to `"any"` rather than `"loop"`,
because the rule is `b > 0 OR d >= 2.0`, and the second clause kicks
in only at 2 seconds. for the user's repertoire (acid loops are often
1-2 bars at fast tempos, frequently sub-2 seconds), this misroutes
short loops to the one-shot/any bucket.

**fix concept:** if `acid_beats > 0` alone, return `"loop"` regardless
of duration.

### F-20 - `analyze_sample` ignores declared `deep` parameter

**file:** `src/acidcat/mcp_server.py:1370`

schema lists `deep: boolean (default false)`. handler never reads it.
either implement (forward to `extract_audio_features(..., deep=...)`)
or remove from the schema.

### F-21 - format dispatch order-dependent

**file:** `src/acidcat/commands/index.py:847-856`

extension-based dispatch uses if/elif and tests `.wav` before `.aiff`.
files that double-suffix (e.g. `.aiff.wav` from a botched batch
convert) hit the WAV path first, parse the WAV header, then fail or
mis-tag because the AIFF body is wrong. unlikely but documented.

---

## LOW

condensed. these don't need patches before PyPI; they are mostly
"know about it" items.

- **F-22 schema migration undefined.** if `SCHEMA_VERSION` ever bumps
  to 2, current code skips `_create_tables` and runs old SQL against a
  newer schema. add a guard that errors on `version > expected`, plus
  a migration registry, before the first version bump.
- **F-23 path_hash 32 bits.** at ~100 libs the user has, collision odds
  are 1 in millions. at 1000 libs it is 1 in tens of thousands. cheap
  to grow to 12 hex chars before any wider release.
- **F-24 `_walk_and_upsert` no explicit `followlinks=False`.** default
  is correct on `os.walk`, but explicit is better. `discover` already
  does this right.
- **F-25 label fallback hash deterministic.** two unrelated roots that
  both default to the same `base_label` fall back to identical hash
  suffixes. add the path into the hash input.
- **F-26 UTF-8 BOM in tagged values.** mutagen returns text with BOM
  in some ID3v2.4 / Vorbis files; index it raw, search becomes flaky
  on the affected entries.
- **F-27 RIFF chunk padding.** spec requires odd-sized chunks be
  padded to even. parser does not always re-align after an odd chunk,
  reading 1 byte off into the next chunk header.
- **F-28 `prune_missing` walk_start race.** files added near end of a
  big walk could be pruned. minor; user re-runs index.
- **F-29 `_REGISTRY_PATH` mutable global.** set once at startup, but
  type signature does not enforce that. if the server ever supports
  hot-reload, this is a footgun.

---

## non-findings (good news)

things the audit looked for and did not find:

- **SQL injection.** all dynamic SQL uses parameterized `?`
  placeholders. no string concat on user input outside FTS5 (which
  has its own non-SQL syntax issue, F-07).
- **path traversal in MCP tools.** `_resolve_stored_path` and
  `_open_owning_library` go through the registry; an LLM cannot point
  at an arbitrary path outside a registered library.
- **command injection.** no shell-out in the codebase. all subprocess
  use is in test fixtures where it is parameterized.
- **secret exposure.** no creds, tokens, API keys in tree. no .env
  references. the only "auth" surface is the GitHub no-reply email
  used in commits.
- **dependency surface.** core deps minimal (sqlite3 stdlib, mutagen,
  optionally librosa+numpy+sklearn). no surprise transitive code
  execution surfaces.
- **test coverage of core paths.** 232 passing tests across 10 test
  files. core flows (index, query, registry, FTS, format parsers,
  MCP fan-out, Camelot) all exercised.
- **schema.** `samples`, `tags`, `descriptions`, `features`,
  `samples_fts`, `scan_roots` are well-shaped. `last_seen_at` enables
  prune. `path` PK is the obvious unique key. FTS5 is wired correctly
  apart from F-14.

---

## what to do, in what order

ordered by ROI, not by severity.

### immediate (one-line fixes, no risk)

these are all under five lines each. no architectural change.

- F-04 add `idempotentHint=false` to `analyze_sample` and
  `detect_bpm_key` annotations
- F-09 update `analyze_sample` description to mention cold start
- F-17 prefix destructive tool descriptions with `"Destructive."`
- F-20 either remove the `deep` parameter from `analyze_sample` schema
  or pass it through to `extract_audio_features`
- F-24 explicit `followlinks=False` in `_walk_and_upsert`
- F-29 comment on `_REGISTRY_PATH` declaring intent

### before PyPI publish (small fixes, real bugs)

- F-01 serum linear-pass JSON parse (replaces the O(n^2) loop)
- F-05 cap WAV chunk reads
- F-07 catch `OperationalError` from FTS path, return clean error
- ~~F-11 SMPL note 0 -> null in index path~~ (already fixed; verified
  at `commands/index.py:877-880`)
- F-13 case-insensitive path normalize on Windows
- F-23 grow `path_hash` to 12 hex chars

### post-PyPI / next minor

- F-02 flip `discover_libraries` default to `dry_run=true`. user-
  visible behavior change, deserves a release note
- F-06 MIDI sysex bounds check
- F-08 `find_compatible` keyless target policy decision (fall back to
  also-keyless or document)
- F-14 transaction-wrap `rebuild_fts_for_path`
- F-15 `BEGIN IMMEDIATE` in `register_library` registry path
- F-18 rename `describe_sample` -> `set_sample_description`. breaking
  rename, deserves a release note
- F-22 schema migration handler before any SCHEMA_VERSION bump

### nice-to-haves (no urgency)

F-10, F-12, F-16, F-21, F-25, F-26, F-27, F-28.

(F-03, F-19 dropped after verification. F-01 stays in pre-PyPI but at
HIGH severity not CRITICAL.)

---

## architectural read

the codebase is in good shape for a v0.5.x. choices that aged well:

- per-library DBs over single global. the regret-rate on this is zero
  in field testing.
- registry as the only join point, fan-out everywhere else. clean
  separation, easy to reason about.
- normalized path with forward slashes as the only path canonical.
  cross-platform stable, even if F-13 is the one Windows-specific
  edge case.
- mutagen as the single dependency for tagged formats. v0.3 cleanup
  paid off; the parser surface is now limited to the formats acidcat
  understands deeply (WAV/AIFF/MIDI/Serum) and one well-maintained
  library for the rest.
- MCP cost-prefix scheme. the `Fast.` / `SLOW.` / `VERY SLOW.`
  ladder is cheap, descriptive, and the only signal an LLM can use
  for tool ranking. extending it to `Destructive.` (F-17) is the
  obvious next step.
- librosa as optional. confirmed by usage that it is the right call;
  most queries do not need it, and the cold-start tax is real.

choices that may need rethinking later, not now:

- 32-bit `path_hash`. fine for one user, not fine for distribution.
- single-process MCP server with a module-global registry path. fine
  today, will hurt the day someone wants to test or hot-reload.
- no migration scaffolding. acceptable while the schema is stable.
  becomes urgent the moment v0.6 wants to add a column.

the librosa cold-start problem is a real bottleneck for big_pack
feature extraction, and the deferred pre-warmed worker plan is the
right shape. nothing in this audit changes that conclusion. F-09
(updating the description) is a free near-term improvement that does
not depend on the worker shipping.

---

## verification addendum (2026-05-02 post-audit)

after the three subagent passes, i sanity-checked the CRITICAL and HIGH
findings against the actual source. two corrections to flag before
acting on the report:

### F-03 is wrong. drop it.

claim: `register_library` MCP tool's `idempotentHint=true` is a lie
because re-calling it recreates the DB and destroys data indexed in
between.

reality: the handler at `mcp_server.py:1015-1037` calls
`reg.register_library` (UPSERT, idempotent in the registry) then
`idx.open_db(db_path); conn.close()`. `open_db` (`core/index.py:48`)
opens an existing DB without modification. `_apply_schema`
(`core/index.py:66`) checks for the `schema_version` row first and
only runs `_create_tables` (which uses `IF NOT EXISTS` everywhere) on
a fresh DB. existing data is untouched.

`idempotentHint=true` on `register_library` is correct. F-03 should
be removed from the docket.

### F-01 severity overstated. HIGH, not CRITICAL.

claim: serum O(n^2) JSON brute-force is a DoS vector. on a 1 MB preset
that is ~1 million parse attempts.

reality: line 41 of `core/serum.py` bounds the search:
`max_search = min(len(raw), json_start + 10000)`. so the loop runs at
most ~9950 iterations regardless of file size. each iteration parses
up to ~10 KB. worst-case work is ~100 MB of parse activity, which is
seconds-scale slow per file, not a hang. real performance issue,
worth fixing with `json.JSONDecoder().raw_decode`, but not a true DoS.

downgrade to HIGH. fix priority unchanged (it's still a bad loop).

### F-11 is wrong. drop it.

claim: SMPL note 0 is treated as a real key on the index path.

reality: `commands/index.py:877-880` already contains:

```python
smpl = meta.get("smpl_root_key")
acid = meta.get("acid_root_note")
if not smpl:
    smpl = None
if not acid:
    acid = None
```

`if not smpl:` is True for both `None` and `0`, so the index path
already drops the C-1 sentinel before storing. the v0.4 fix mentioned
in the audit was on the info side, but the same guard exists on the
index side too. drop F-11.

### F-19 is wrong. drop it.

claim: `infer_kind` mis-bins a 1.0s clip with `acid_beats=1` as `"any"`
because the rule `b > 0 or d >= 2.0` allegedly requires BOTH conditions.

reality: that is just an `or`, not an `and`. `mcp_server.py:495-508`:

```python
if b > 0 or d >= 2.0:
    return "loop"
```

for `b=1, d=1.0`: `b > 0` is True, the branch returns `"loop"`. Python
`or` short-circuits. the subagent misread the boolean operator.

drop F-19. infer_kind is correct as written.

### everything else verified

spot-checks on the rest of the CRITICAL / HIGH / MEDIUM set:

- **F-02** verified. `mcp_server.py:1068` reads `dry_run` with default
  `False`; `mcp_server.py:1500` schema declares default `false`;
  `mcp_server.py:1478-1480` description says "always call once with
  dry_run=true first". the contradiction is real.
- **F-04** verified. `mcp_server.py:1375` and `:1388` annotation dicts
  do not mention `idempotentHint`. they default to whatever the SDK
  picks.
- **F-05** verified. `core/riff.py:67` reads `chunk_size` bytes
  unbounded. `core/aiff.py` does cap at 4 KB. real OOM path on
  malformed input.
- **F-06** verified. `core/midi.py:143-146` sysex branch reads VLQ
  length and adds to `pos` with no bound check. compare lines 102-103
  for meta events, which DO bound check (`if pos + event_len >
  len(trk_data): break`). asymmetric and wrong.
- **F-07** verified. `mcp_server.py:256-259` passes user text
  unguarded to `samples_fts MATCH ?`.
- **F-08** verified. `mcp_server.py:541-568` skips key filter when
  `compat_keys` is empty.
- **F-09** verified. `mcp_server.py:1363` says `"SLOW (~1-10s)"` with
  no cold-start mention.

### so the real ledger is

- 2 CRITICAL: **F-02, F-04**. (F-01 downgraded to HIGH, F-03 withdrawn.)
- 7 HIGH: F-01 (downgraded), F-05, F-06, F-07, F-08, F-09. (so 6 not 7.)
- 10 MEDIUM: F-10..F-21 minus F-11 (already fixed) and F-19 (withdrawn).
- 8 LOW: F-22..F-29 unchanged.

net: **26 actionable findings** out of 29 originally reported. 2 truly
critical (both MCP tool annotation / default issues). 6 high. 10
medium. 8 low. the immediate "one-line fixes" list at the top of this
report is unaffected. F-11, F-19 dropped after verification (already
fixed / misread `or` as `and`). F-03 dropped (open_db is idempotent).
F-01 stays in the pre-PyPI set at HIGH not CRITICAL.

---

## scope notes

- not reviewed in depth, but spot-checked: `docs/architecture.md` (594
  lines) is **fully stale**. zero mentions of `registry`, `per-library`,
  `libraries/`, or related terms. lines 35 and 248 still describe the
  v0.4 single-DB layout (`~/.acidcat/index.db`). a new reader who
  follows that doc will build the wrong mental model. worth a rewrite
  or at minimum a "this describes v0.4; see HANDOVER.md for v0.5"
  banner before PyPI ship. format_internals.md (96 lines) is shorter
  and more algorithm-focused; less acute drift but worth a sweep.
- not reviewed: the test suite for completeness. existing tests pass;
  whether each finding above has a regression test is a separate
  audit.
- pyproject metadata: not reviewed in depth in the main audit but
  spot-checked during verification. concrete gaps captured in the
  appendix below so the pre-PyPI session has a starting point.
- not reviewed: cross-platform behavior. Windows-only audit
  environment. macOS / Linux smoke test still outstanding from the
  PyPI plan.

---

## appendix: pyproject.toml gaps for PyPI prep

read of `pyproject.toml` against the standard PyPI publishing checklist.
not blocking issues, just things the next session needs to address.

- **no `[project.urls]` table.** add `Homepage`, `Repository`,
  `Issues`. without these the PyPI page has no links back to
  github.com/hed0rah/acidcat or the issue tracker.
- **no `authors` field.** without it PyPI shows `UNKNOWN`. should be
  `[{name = "hed0rah", email = "18272116+hed0rah@users.noreply.github.com"}]`
  using the no-reply per the git workflow rule.
- **classifier set is thin.** has `Development Status :: 3 - Alpha`,
  `Environment :: Console`, two `Intended Audience` lines, one `Topic`,
  one `License`, one `Programming Language :: Python :: 3`. missing:
  per-minor python classifiers (`3.9` through `3.13`),
  `Operating System :: OS Independent` (or platform-specific until
  cross-platform smoke test passes), and arguably bumping to
  `4 - Beta` given 232 passing tests and several months of real use.
- **`mutagen` is in the optional `tags` extra**, but the index command
  invokes the tagged-format dispatch on any non-WAV/AIFF/MIDI/Serum
  file. if a fresh user runs `pip install acidcat` (no extras) and
  points it at a folder of MP3s, the parser will fail. either move
  `mutagen` to base `dependencies`, or default-install the `tags`
  extra, or add a clear startup error when an unsupported extension
  is encountered without the extra installed.
- **`Development Status` for first PyPI release.** the user mentioned
  `0.5.3a1` as a possible pre-release tag in their release plan. if
  shipping as alpha, status `3 - Alpha` is correct; if shipping as a
  proper `0.5.3`, bump to `4 - Beta`.
- **`description` field uses `--` (double-hyphen).** that is fine.
  not an em-dash. PyPI renders it as a hyphen.
- **`README.md` rendering on PyPI not verified.** PyPI uses
  `long_description_content_type` but pyproject does not set it
  explicitly. setuptools defaults to inferring from the readme
  extension. adding
  `dynamic = []` and ensuring `readme = {file = "README.md", content-type = "text/markdown"}`
  is the safe form.

verification run flow before first publish:

```
python -m build              # produces dist/*.tar.gz and dist/*.whl
twine check dist/*           # validates metadata + readme rendering
# inspect dist/*.whl with `unzip -l` to confirm package contents
twine upload --repository testpypi dist/*    # dry-run to TestPyPI
pip install --index-url https://test.pypi.org/simple/ acidcat
acidcat --version            # smoke
twine upload dist/*          # real publish
```

cross-platform smoke test ideally happens between `twine check` and
`twine upload --repository testpypi`. minimum: a fresh Linux container
running `pip install -e .` then `acidcat index test_samples --label test`
then `acidcat query --bpm 100:140`. macOS would be nice-to-have but is
much less likely to surface a regression that Linux misses, given
acidcat's path normalization is designed around the Windows/Unix
divide.

---

end of report.
