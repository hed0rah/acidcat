"""Set the version for a release: python scripts/release.py X.Y.Z[rcN]

Writes the number everywhere acidcat states it (pyproject.toml, __version__,
the ARCHITECTURE.md header) and, for a final release, turns the changelog's
Unreleased section into the release's own, dated today, under a fresh empty
Unreleased. A pre-release (aN, bN, rcN) leaves the changelog alone: its
entries stay under Unreleased until the final is cut.

It edits files and prints the git steps; it does not commit, tag or push.
docs/releasing.md is the whole procedure.
"""

import argparse
import datetime
import pathlib
import re
import sys
import tomllib

PEP440 = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")
PRE_ORDER = {"a": 0, "b": 1, "rc": 2, None: 3}


def _key(version):
    m = PEP440.match(version)
    if not m:
        raise ValueError(f"{version!r} is not X.Y.Z or X.Y.Z(a|b|rc)N")
    major, minor, patch, pre, n = m.groups()
    return (int(major), int(minor), int(patch), PRE_ORDER[pre], int(n or 0))


def _sub_once(text, pattern, repl, where):
    new, count = re.subn(pattern, repl, text, count=1, flags=re.M)
    if count != 1:
        raise SystemExit(f"release: no version found in {where}")
    return new


def current_version(root):
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)["project"]["version"]


def fold_changelog(text, version, date):
    """The Unreleased entries become `## [version] - date`; a new empty
    Unreleased goes above it."""
    head = "## [Unreleased]\n"
    if head not in text:
        raise SystemExit("release: CHANGELOG.md has no Unreleased section")
    if f"## [{version}]" in text:
        raise SystemExit(f"release: CHANGELOG.md already has a {version} section")
    start = text.index(head) + len(head)
    nxt = re.search(r"^## \[", text[start:], flags=re.M)
    body = text[start:start + nxt.start()] if nxt else text[start:]
    if not body.strip():
        raise SystemExit("release: the Unreleased section is empty")
    return text.replace(head, f"{head}\n## [{version}] - {date}\n", 1)


def plan(root, version, date):
    """{path: new text} for every file the release changes."""
    old = current_version(root)
    if _key(version) <= _key(old):
        raise SystemExit(f"release: {version} is not after the current {old}")
    files = {}
    p = root / "pyproject.toml"
    files[p] = _sub_once(p.read_text(encoding="utf-8"),
                         r'^version = "[^"]+"', f'version = "{version}"', p.name)
    p = root / "src" / "acidcat" / "__init__.py"
    files[p] = _sub_once(p.read_text(encoding="utf-8"),
                         r'^__version__ = "[^"]+"', f'__version__ = "{version}"', p.name)
    p = root / "ARCHITECTURE.md"
    files[p] = _sub_once(p.read_text(encoding="utf-8"),
                         r"^v\S+ ·", f"v{version} ·", p.name)
    if PEP440.match(version).group(4) is None:
        p = root / "CHANGELOG.md"
        files[p] = fold_changelog(p.read_text(encoding="utf-8"), version, date)
    return old, files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version")
    ap.add_argument("--root", default=pathlib.Path(__file__).resolve().parent.parent,
                    type=pathlib.Path)
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true", help="say what would change")
    args = ap.parse_args(argv)
    try:
        old, files = plan(args.root, args.version, args.date)
    except ValueError as e:
        raise SystemExit(f"release: {e}")
    for path, text in files.items():
        print(f"{'would write' if args.dry_run else 'wrote'} {path.relative_to(args.root)}")
        if not args.dry_run:
            path.write_text(text, encoding="utf-8", newline="\n")
    if not args.dry_run:
        print(f"\n{old} -> {args.version}. Next, on next:\n"
              f"  python -m pytest -q\n"
              f"  git commit -am \"release: {args.version}\"\n"
              f"  git push origin next\n"
              f"then the next -> main PR, and the tag v{args.version} on main "
              f"(docs/releasing.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
