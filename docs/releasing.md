# Releasing acidcat

Work lands on `next`. `main` is what was released, and it moves only by a
pull request from `next`. A tag is cut only on `main`, and PyPI publishes only
when a GitHub Release is created from that tag (`.github/workflows/publish.yml`
runs the full test matrix first).

## The numbers

Semantic versioning: a minor release adds, a major one breaks something a user
relied on (a removed command or flag, a changed output contract, a package
split). An index migrated forward that an older acidcat refuses is not a break:
downgrading is not covered.

Pre-releases are `X.Y.ZbN` while features are still landing and `X.Y.ZrcN`
once the only work left is checking it. **Any code change after an rc is a new
rc**; a docs-only change is not.

## Steps

1. **`next` is green.** Every job of the last CI run on `next` passed. Red
   means stop.
2. **Real files.** Run the library index and the commands that changed on real
   libraries, read-only, and once on Linux. A finding on an intact file is a
   defect only if a reader sees wrong data.
3. **Cut the candidate.**

   ```sh
   python scripts/release.py X.Y.Zrc1
   python -m pytest -q
   git commit -am "release: X.Y.Zrc1"
   git push origin next
   ```

   The script writes the version into `pyproject.toml`, `__version__` and the
   `ARCHITECTURE.md` header. `--dry-run` says what it would write.
4. **Soak.** Use the candidate for real work for at least a few days: the
   library, `lib query`, the MCP server, the commands that changed.
5. **Cut the final.** `python scripts/release.py X.Y.Z` also turns the
   changelog's Unreleased section into `## [X.Y.Z] - <today>` under a fresh
   empty Unreleased. Commit it as `release: X.Y.Z` and push to `next`.
6. **Pull request `next` -> `main`.** Merge it once the PR's checks pass.
7. **Tag on `main` and release.**

   ```sh
   git fetch origin && git checkout main && git pull
   git tag vX.Y.Z && git push origin vX.Y.Z
   gh release create vX.Y.Z --title X.Y.Z --notes-file <the changelog section>
   ```

   The Release, not the tag, publishes to PyPI.
8. **Merge `main` back into `next`** the same day, so the two agree.

## The lab

`acidcat-lab` (`lab/`) is its own distribution with its own version, published
by `publish-lab.yml`. Its `acidcat` pin must accept the core version being
released (`tests/test_version_coherence.py` checks this); move the pin in the
same commit as the version that needs it.

## A patch release

A fix to a released version goes to `next` like anything else and is released
the same way, as `X.Y.Z+1`. Merge `main` back into `next` the same day.
