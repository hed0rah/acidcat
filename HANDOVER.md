# acidcat handover

State as of 2026-05-02, after the 2026-05-02 audit batch shipped on
main (commits 31894a1..1756683 + version bump). Snapshot for picking up
cold without re-reading the full session transcript.

## Where the project is

Currently shipped: **v0.5.4** on `main` (working tree; not pushed,
stacked ahead of origin/main pending a PR or direct push). Per-library
SQLite indexes, global registry at `~/.acidcat/registry.db`, fan-out
queries, MCP server, harmonic mixing via Camelot, librosa-based analysis
tools. 254 passing tests. Closed all 26 actionable findings from the
audit.

Recent release history:

- **v0.4.0** (PR #1): single global SQLite index + 16-tool MCP server
- **v0.4.x docs** (PR #2): architecture and DSP deep-dive docs
- **v0.4.x fixes** (PR #3): info C-1 phantom key, `-v` stderr-only,
  `dump -f json`, `__main__.py` exit code propagation
- **v0.5.0** (PR #4): replaced single global DB with per-library DBs +
  registry + fan-out. 18-tool MCP surface
- **v0.5.1** (PR #5): BPM parser regex tightening, register-library
  reattach stats, `find_similar` kind_filter + scoring, periodic
  commits in CLI walker
- **v0.5.2** (PR #6): `--discover ROOT` walker for bulk library
  registration, plus `discover_libraries` MCP tool
- **v0.5.3** (PR #7): CLI collision guard, `--discover` pre-touches
  DBs, new `--refresh-stats` migration command
- **v0.5.4** (this branch, on main, not yet pushed): full audit
  closeout. 14 commits + version bump + CHANGELOG. See
  `CHANGELOG.md` for the per-finding breakdown.

What's left before v0.5.4 is shipped to PyPI:

- Push to origin/main (or open as a PR for review). Stacked locally.
- Cross-platform validation (Linux smoke test). Only Windows tested.
- Real PyPI publish (or TestPyPI dry-run first). `python -m build`
  and `twine check dist/*` are both green.

The deferred **pre-warmed librosa worker** is still on the docket.
The user wants to do that one together (not autonomous). Real
motivation: librosa cold start (~30-60s of numba JIT) blows the 60s
MCP timeout on slow disks (big_pack on G:). Persistent subprocess that
warms once, then services analyze/extract calls in <1s each.

## Architecture in one paragraph

Each indexed directory becomes a *library* with its own SQLite DB. By
default DBs live centrally at
`~/.acidcat/libraries/<safe_label>_<hash>.db` (12 hex chars of
`sha1(normalized_root)`). Opt-in `--in-tree` puts the DB at
`<library>/.acidcat/index.db`. A single registry at
`~/.acidcat/registry.db` lists every library with cached
`sample_count` / `feature_count` / `last_indexed_at`. Reads fan out
across all registered libraries and dedup by path. Writes route to the
innermost containing library via longest-prefix match. Nested libraries
are rejected at registration time. Labels are mandatory; auto-derived
from basename if not given. Path comparison is case-insensitive on
Windows.

## What is registered right now

Roughly 20 libraries on the user's machine. The big one is `big_pack`
at G:/DATAz/SampleZ/11111 overheat (32058 audio files). The rest live
under `C:/Users/joshr/PROJECTZ/acidcat/sample_packs/`. Run
`acidcat index --list` for the current snapshot. After v0.5.3 the user
can run `acidcat index --refresh-stats` once to populate stale `?`
counts for libraries registered before v0.5.1.

## What is NOT done yet

**Librosa cold-start kills MCP throughput on slow disks.** First call
in any spawned process pays ~30-60s of numba JIT. The MCP timeout is
60s, so on G: drive the timeout fires before useful work happens.
`reindex_features` chunked at limit=20 commits in time but extracts
only 20 files per call. Big_pack feature extraction is therefore a
25-30 hour job currently impractical via MCP alone. The
`analyze_sample` tool description was updated in v0.5.4 to set
expectations about the cold-start tax but the real fix is the
deferred pre-warmed worker.

**Cross-platform validation.** Only ever tested on Windows + Git Bash.
macOS and Linux paths have not been exercised. Suspected risks: path
normalization (we use forward slashes everywhere), `os.path.expanduser`
behavior, librosa's audioread fallback chain. Need before PyPI ship.

**v0.5.4 push.** 16 commits stacked on local main, none pushed.

## How the user works

- Macro pattern: ship small, live with it, capture findings, plan next
  release from evidence. The `docs/v0.5_field_notes.md` working doc is
  the artifact; intentionally uncommitted, intentionally chronological.
- Splits work between this session and a parallel "cowork" Claude.
  Cowork output sometimes carries `Co-Authored-By: Claude` trailers
  and the user's personal email; the primary session must scrub
  before any commit goes out.
- Pushes from PowerShell (HTTPS auth via Windows Credential Manager;
  bash hangs on push). Merges via `gh pr create` or GitHub web UI.
  After merge, runs `git fetch origin && git checkout main && git
  reset --hard origin/main && git branch -D <branch>`.
- Style rules (already in CLAUDE.md but worth repeating because they
  bite): no em-dashes, no emojis, no AI co-authorship anywhere, no
  capital-leading inline comments in new code, never personal email
  in commits (use the GitHub no-reply
  `18272116+hed0rah@users.noreply.github.com`).

## Files outside the source tree to know about

- `~/.acidcat/registry.db` - the global registry
- `~/.acidcat/libraries/` - central per-library DBs
- `~/.acidcat/index.db` - legacy v0.4 file, ignored, can be removed
- `~/AppData/Roaming/Claude/claude_desktop_config.json` - MCP
  registration. Quit Claude Desktop fully before editing or it
  rewrites the file from memory state on next save
- `~/.local/bin/acidcat` and `acidcat-mcp` - bash shims pointing at
  the full-qualified .exe paths in the WindowsApps Python Scripts dir
- Plan file: `~/.claude/plans/analyze-the-repo-and-stateful-cherny.md`
- Field notes: `docs/v0.5_field_notes.md` (working doc, uncommitted)
- Audit report: `docs/codebase_review_2026-05-02.md`
- Codebase explorer: `docs/codebase_explorer.html`
- Release notes: `CHANGELOG.md`

## Suggested first move for a cold start

1. `git -C /c/Users/joshr/PROJECTZ/acidcat status -sb` to see branch
   state
2. `git log --oneline origin/main..HEAD` to see the unpushed v0.5.4
   stack
3. `acidcat --version` to confirm install reflects the source tree
4. `acidcat index --list` to see registered libraries
5. Ask the user what they want to work on. Default offer: push v0.5.4
   to origin (or open as a PR for review), then PyPI prep with the
   cross-platform smoke test, OR start the librosa warmer (the
   explicit "together" item).

## What to NOT do without explicit user direction

- Push to main directly
- Force-push anything
- Run `git filter-branch` or `git rebase -i`
- Delete the local `dev_9-21` or `requirements` branches
- Start the librosa warmer work autonomously (the user explicitly
  wants to do that one together)
- Ship to real PyPI without the user explicitly saying "go"
