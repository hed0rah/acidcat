"""A doc that points at `core/foo.py` should point at a file that exists.

Modules moved during the 1.0 restructure -- sniff to core/infra/, indexing to
core/catalogue/, the DSP modules to core/analysis/, the codecs to core/codecs/ --
and 17 references across README and docs/ kept naming the old locations. Nothing
failed, because prose does not get imported. A reader following the doc just
found nothing there.

CHANGELOG.md is deliberately exempt. Its entries describe where a file was when
that version shipped, so "corrected" paths would make it a less accurate record,
not a more accurate one. A changelog is the one document that is supposed to go
stale.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parent.parent
SRC = ROOT / "src" / "acidcat"

# `core/x/y.py` or `src/acidcat/x/y.py` inside backticks
_REF = re.compile(r"`(core/[\w/]+\.py|src/acidcat/[\w/]+\.py)`")

_EXEMPT = {"CHANGELOG.md"}

# A design proposal names the modules it plans to create, which do not exist
# yet; that is the point of it. Such a doc says so on a line of its own, and is
# checked like any other doc from the moment that line changes.
_DRAFT = re.compile(r"^Status: DRAFT\b", re.M)


def _is_draft(p):
    return bool(_DRAFT.search(p.read_text(encoding="utf-8", errors="replace")))


def _live_docs():
    docs = [p for p in ROOT.glob("*.md") if p.name not in _EXEMPT]
    docs += sorted((ROOT / "docs").rglob("*.md"))
    return [p for p in docs if not _is_draft(p)]


def _resolve(ref):
    return (SRC / ref) if ref.startswith("core/") else (ROOT / ref)


def test_docs_were_actually_scanned():
    """Guards the guard.

    If the glob or the regex stops matching, every other test here passes by
    finding nothing -- a green suite that checked no documents. This asserts the
    scan has a corpus and that the corpus contains references to check.
    """
    docs = _live_docs()
    assert len(docs) >= 5, f"only {len(docs)} docs found; the glob is wrong"
    refs = sum(len(_REF.findall(p.read_text(encoding="utf-8", errors="replace")))
               for p in docs)
    assert refs >= 20, f"only {refs} module references found; the regex is wrong"


def test_every_referenced_module_exists():
    broken = []
    for p in _live_docs():
        for i, line in enumerate(
                p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in _REF.finditer(line):
                if not _resolve(m.group(1)).exists():
                    broken.append(f"{p.relative_to(ROOT)}:{i} -> {m.group(1)}")
    assert not broken, (
        "docs reference modules that do not exist:\n  " + "\n  ".join(broken))


def test_changelog_is_exempt_on_purpose():
    """Not a formality: this records WHY, so nobody 'fixes' the exemption.

    CHANGELOG.md contains stale paths by design. If someone removes the
    exemption the suite goes red on entries that are correct as history, and the
    tempting repair is to rewrite the log.
    """
    cl = ROOT / "CHANGELOG.md"
    if not cl.exists():
        pytest.skip("no CHANGELOG.md")
    assert "CHANGELOG.md" in _EXEMPT
    assert cl not in _live_docs()


def test_only_design_proposals_are_drafts():
    """The draft exemption is for proposals, not a way to silence the guard.

    Drafts live under docs/contract/ (the 2.0 design); a draft marker anywhere
    else, such as on the README or an anatomy page, is a mistake.
    """
    docs = list(ROOT.glob("*.md")) + sorted((ROOT / "docs").rglob("*.md"))
    stray = [str(p.relative_to(ROOT)) for p in docs
             if _is_draft(p) and p.parent != ROOT / "docs" / "contract"]
    assert not stray, "draft marker outside docs/contract/: " + ", ".join(stray)
