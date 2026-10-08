"""ARCHITECTURE.md states counts as facts, so the counts are checked.

Every number in that document was wrong before 1.0 -- 57 formats when there were
64, 46 walker labels when there were 53, 193 modules when there were 199. All
undercounts, because the doc was written once and the tree kept growing. Nothing
was lying on purpose; a number simply has no way to notice that it went stale.

That is the same failure this release is otherwise about: a figure that was true
when measured, reported later as though it still were. Fixing the numbers
without fixing the mechanism just resets the clock, so they are asserted here.

THE COUNTING RULE, because the doc previously used two and the disagreement is
what made the drift hard to see: a module is a .py file, counted RECURSIVELY,
INCLUDING __init__.py. A package with a nested directory (core/grammar/ had
one, until 2.0 removed it) reads fewer modules non-recursively than
recursively -- both defensible, which is exactly why the rule has to be
written down rather than inferred.

The one deliberate exception is "34 walker modules", which excludes
core/walk/__init__.py: that file is the dispatcher, not a walker, so counting it
would make the prose false in a way the raw number would not.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parent.parent
DOC = ROOT / "ARCHITECTURE.md"
SRC = ROOT / "src" / "acidcat"


def _modules(rel):
    return len(list((SRC / rel).rglob("*.py")))


@pytest.fixture(scope="module")
def doc():
    if not DOC.exists():
        pytest.skip("ARCHITECTURE.md not present")
    return DOC.read_text(encoding="utf-8")


def _stated(doc, pattern):
    """The number the doc claims, or a failure naming what could not be found.

    A regex that stops matching silently turns this whole file into a test that
    asserts nothing, which is the bug it exists to catch.
    """
    m = re.search(pattern, doc, re.M)
    assert m, f"pattern no longer matches ARCHITECTURE.md, so nothing was checked: {pattern}"
    return int(m.group(1))


def test_version_matches(doc):
    from acidcat import __version__
    m = re.search(r"^v(\S+) ", doc, re.M)
    assert m, "no version line found in ARCHITECTURE.md"
    assert m.group(1) == __version__, (
        f"ARCHITECTURE.md says v{m.group(1)}, package is {__version__}")


def test_recognized_format_count(doc):
    from acidcat.core.infra import sniff
    stated = _stated(doc, r"`sniff\.py` -- (\d+) recognized formats")
    assert stated == len(sniff.KNOWN_FORMATS)


def test_walker_label_count(doc):
    from acidcat.core import walk
    stated = _stated(doc, r"serving (\d+)\n?\s*registered format labels")
    assert stated == len(walk._WALKERS)


def test_walker_module_count(doc):
    """Excludes the dispatcher -- see the module docstring."""
    stated = _stated(doc, r"(\d+) walkers behind one dispatcher")
    actual = len([p for p in (SRC / "core" / "walk").glob("*.py")
                  if p.name != "__init__.py"])
    assert stated == actual


@pytest.mark.parametrize("rel,pattern", [
    ("core", r"^  core/\s+(\d+) modules"),
    ("core/formats", r"per-format byte decoders \((\d+)\)"),
    ("core/forensics", r"anomalies, entropy/viz, audioscan, provenance \((\d+)\)"),
])
def test_directory_module_counts(doc, rel, pattern):
    stated = _stated(doc, pattern)
    assert stated == _modules(rel), (
        f"ARCHITECTURE.md says {rel} has {stated} modules, tree has "
        f"{_modules(rel)} (rule: recursive, including __init__.py)")


def test_total_module_count(doc):
    stated = _stated(doc, r"\((\d+) modules in total\)")
    assert stated == _modules(".")


def test_readme_format_count_matches_the_dispatcher():
    """The README used to enumerate the formats `inspect` reads, and named
    about twenty of sixty-three.

    A list like that cannot be maintained by anyone who is not already thinking
    about it, and adding a format is not a moment when anyone is. It was
    replaced by a count plus a pointer to `acidcat formats`, which is the same
    move the unsupported-file error already makes -- except the error computes
    its number at runtime and prose cannot. So the number is asserted instead.
    """
    from acidcat.core import walk
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    stated = _stated(readme, r"for the (\d+) formats\s*\n?\s*`inspect` reads")
    assert stated == len(walk._WALKERS), (
        f"README says inspect reads {stated} formats, the dispatcher "
        f"registers {len(walk._WALKERS)}")
