"""One version number, written in five places.

pyproject.toml is what pip installs, `acidcat.__version__` is what the CLI and
the API report, and three docs quote it. Nothing held them together, so the
package could ship claiming one version and answer `--version` with another --
and the docs could quote a third. That is the drifted-duplicate shape: two
places answering one question with nothing keeping them honest.

The docs are checked loosely on purpose. A doc quoting an older version is
stale, not wrong, and blocking every release on a docs edit would just get the
check deleted. The build metadata and the runtime constant are the pair that
must never disagree.
"""

import pathlib
import re

import tomllib

import pytest

import acidcat


ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject_version():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)["project"]["version"]


def test_the_installed_version_matches_the_runtime_one():
    """The pair that must never disagree: pip installs one, `--version` says
    the other."""
    assert acidcat.__version__ == _pyproject_version()


def test_the_version_is_a_valid_pep440_release():
    """PyPI rejects a bad version at upload, which is the worst place to find
    out -- publishing is irreversible and a number can never be reused."""
    pat = r"^\d+\.\d+\.\d+((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$"
    assert re.match(pat, acidcat.__version__), acidcat.__version__


def test_the_changelog_has_an_entry_for_a_released_version():
    """A pre-release (b/rc) may sit under Unreleased while it is being cut.
    A final x.y.z must have its own section before it can be tagged."""
    ver = acidcat.__version__
    if re.search(r"(a|b|rc)\d+$", ver):
        pytest.skip(f"{ver} is a pre-release")
    body = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{ver}]" in body, f"CHANGELOG.md has no section for {ver}"


@pytest.mark.parametrize("doc", ["ARCHITECTURE.md", "docs/architecture.md"])
def test_the_docs_quote_a_version_that_existed(doc):
    """Loose: the docs may lag. But a version they quote must be one this
    project actually used, not a typo -- v1.O.0b1 would sail through review."""
    body = (ROOT / doc).read_text(encoding="utf-8")
    quoted = set(re.findall(r"v(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)", body))
    if not quoted:
        pytest.skip(f"{doc} quotes no version")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    known = set(re.findall(r"## \[([^\]]+)\]", changelog)) | {acidcat.__version__}
    unknown = quoted - known
    assert not unknown, f"{doc} quotes {sorted(unknown)}, which no release used"


def test_the_lab_accepts_this_acidcat():
    """lab/pyproject.toml pins the acidcat it runs against. CI installs both
    from the tree (`pip install -e ".[dev]" -e ./lab`), so a pin this version
    does not satisfy is a red CI install and nothing else says why: `next`
    at 1.8.6 against the lab's `acidcat>=2.0.0a1` was exactly that
    (review V3). Pre-releases count, as pip counts them for an explicit
    pre-release pin."""
    packaging = pytest.importorskip("packaging.requirements")
    lab = tomllib.loads((ROOT / "lab" / "pyproject.toml").read_text(encoding="utf-8"))
    reqs = [packaging.Requirement(r) for r in lab["project"]["dependencies"]]
    pins = [r for r in reqs if r.name == "acidcat"]
    assert pins, "the lab no longer names acidcat among its dependencies"
    for r in pins:
        assert r.specifier.contains(acidcat.__version__, prereleases=True), (
            f"lab/pyproject.toml requires {r}, the tree is {acidcat.__version__}")
