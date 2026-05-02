# acidcat handover

State as of 2026-04-30, after v0.5.3 shipped (PR #7, merged 2026-04-29).
Snapshot for picking up cold without re-reading the full session
transcript.

## Where the project is

Currently shipped: **v0.5.3** on `main`. Per-library SQLite indexes,
global registry at `~/.acidcat/registry.db`, fan-out queries, MCP
server, harmonic mixing via Camelot, librosa-based analysis tools.

Recent release history (each is a single PR, mergeable independently):

- **v0.4.0** (PR #1): single global SQLite index + 16-tool MCP server
- **v0.4.x docs** (PR #2): architecture and DSP deep-dive docs
- **v0.4.x fixes** (PR #3): info C-1 phantom key, `-v` stderr-only, `dump
  -f json`, `__main__.py` exit code propagation
- **v0.5.0** (PR #4): replaced single global DB with per-library DBs +
  registry + fan-out. 18-tool MCP surface
- **v0.5.1** (PR #5): BPM parser regex tightening (rejects `91V` as
  bpm 91), register-library reattach stats, `find_similar` kind_filter
  + percentile/relative scores, periodic commits in CLI walker
- **v0.5.2** (PR #6): `--discover ROOT` walker for bulk library
  registration, plus `discover_libraries` MCP tool
- **v0.5.3** (PR #7): CLI collision guard (target + management flag),
  `--discover` pre-touches DBs, new `--refresh-stats` migration command

Next thing on the docket: **pre-warmed librosa worker**. Deferred so the
user and Claude tackle it in a session together. Not a self-serve item.

After that: **PyPI ship**. Target was "next weekend" per user message
2026-04-26 (so early May). Cross-platform smoke test outstanding.

## Architecture in one paragraph

Each indexed directory becomes a *library* with its own SQLite DB. By
default DBs live centrally at `~/.acidcat/libraries/<label>_<hash>.db`;
opt-in `--in-tree` puts the DB at `<library>/.acidcat/index.db`. A
single registry at `~/.acidcat/registry.db` lists every library with
cached `sample_count` / `feature_count` / `last_indexed_at`. Reads fan
out across all registered libraries and dedup by path. Writes route to
the innermost containing library. Nested libraries are rejected at
registration time. Labels are mandatory; auto-derived from basename if
not given.

## What is registered right now

Roughly 20 libraries. The big one is `big_pack` at G:/DATAz/SampleZ/11111
overheat (32058 audio files). The rest live under
`C:/Users/joshr/PROJECTZ/acidcat/sample_packs/`. Run `acidcat index
--list` for the current snapshot. Many libraries still report `?` for
`sample_count` because they were registered before v0.5.1's reattach
stats fix; the new `acidcat index --refresh-stats` command (v0.5.3)
populates them in one pass.

## What is NOT done yet

**Librosa cold-start kills MCP throughput on slow disks.** First call
in any spawned process pays ~30-60s of numba JIT. The MCP timeout is
60s, so on G: drive the timeout fires before useful work happens.
`reindex_features` chunked at limit=20 commits in time but extracts
only 20 files per call. Big_pack feature extraction is therefore a
25-30 hour job currently impractical via MCP alone.

**Cross-platform validation.** Only ever tested on Windows + Git Bash.
macOS and Linux paths have not been exercised. Suspected risks: path
normalization (we use forward slashes everywhere), `os.path.expanduser`
behavior, librosa's audioread fallback chain. Need before PyPI.

**PyPI metadata polish.** `pyproject.toml` is missing `[project.urls]`
(Homepage/Repository/Issues), classifiers could be richer, README
rendering on PyPI not yet verified. None of this is hard, just hasn't
been done.

## How the user works

- Macro pattern: ship small, live with it, capture findings, plan next
  release from evidence. The `docs/v0.5_field_notes.md` working doc is
  the artifact; intentionally uncommitted, intentionally chronological.
- Splits work between this session and a parallel "cowork" Claude. Cowork
  output sometimes carries `Co-Authored-By: Claude` trailers and the
  user's personal email; the primary session must scrub before any commit
  goes out.
- Pushes from PowerShell (HTTPS auth via Windows Credential Manager;
  bash hangs on push). Merges via `gh pr create` or GitHub web UI.
  After merge, runs `git fetch origin && git checkout main && git
  reset --hard origin/main && git branch -D <branch>`.
- Style rules (already in CLAUDE.md but worth repeating because they
  bite): no em-dashes, no emojis, no AI co-authorship anywhere, no
  capital-leading inline comments in new code, never personal email in
  commits (use the GitHub no-reply
  `18272116+hed0rah@users.noreply.github.com`).

## Files outside the source tree to know about

- `~/.acidcat/registry.db` -- the global registry
- `~/.acidcat/libraries/` -- central per-library DBs
- `~/.acidcat/index.db` -- legacy v0.4 file, ignored, can be removed
- `~/AppData/Roaming/Claude/claude_desktop_config.json` -- MCP
  registration. Quit Claude Desktop fully before editing or it
  rewrites the file from memory state on next save
- `~/.local/bin/acidcat` and `acidcat-mcp` -- bash shims pointing at
  the full-qualified .exe paths in the WindowsApps Python Scripts dir
- Plan file: `~/.claude/plans/analyze-the-repo-and-stateful-cherny.md`
- Field notes: `docs/v0.5_field_notes.md` (working doc, uncommitted)

## Suggested first move for a cold start

1. `git -C /c/Users/joshr/PROJECTZ/acidcat status -sb` to see branch
   state
2. `acidcat --version` to confirm install reflects the source tree
3. `acidcat index --list` to see registered libraries
4. Ask the user what they want to work on. Default offer: librosa
   warmer (the explicit "together" item) or PyPI prep (the explicit
   "weekend" item).

If neither, default to `cat docs/v0.5_field_notes.md` and look for
unaddressed issues with low effort estimates. There are several.

## What to NOT do without explicit user direction

- Push to main directly
- Force-push anything
- Run `git filter-branch` or `git rebase -i`
- Delete the local `dev_9-21` or `requirements` branches (user has
  not yet OK'd the cleanup, even though they are local-only)
- Start the librosa warmer work autonomously (the user explicitly
  wants to do that one together)
- Ship to real PyPI without the user explicitly saying "go"
