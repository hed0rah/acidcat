"""docs/contract/cli-2.0.md names every verb and flag the CLI accepts.

The mapping is the plan for the 2.0 CLI pass: what each 1.8 spelling becomes
and whether it keeps working as an alias. It is only worth having if it is
complete, so a verb or flag added to the parser without a line here fails.
"""

import argparse
import pathlib
import re

from acidcat.cli import _build_parser

DOC = (pathlib.Path(__file__).parent.parent / "docs" / "contract"
       / "cli-2.0.md").read_text(encoding="utf-8")


def _subparsers(parser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a.choices
    return {}


def _flags(parser):
    return {o for a in parser._actions for o in a.option_strings
            if o not in ("-h", "--help")}


def _sections():
    """{verb: text} for each `### \\`verb\\`` section."""
    parts = re.split(r"^### `([a-z0-9-]+)`\s*$", DOC, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


VERBS = _subparsers(_build_parser())


def test_the_parser_still_has_verbs():
    """Guards the guard: an empty parser passes everything below."""
    assert len(VERBS) >= 29


def test_every_verb_has_a_section_and_a_row():
    sections = _sections()
    missing = sorted(set(VERBS) - set(sections))
    assert not missing, f"verbs with no section in cli-2.0.md: {missing}"
    table = DOC.split("## 2. Verbs", 1)[1].split("## 3.", 1)[0]
    unrowed = [v for v in VERBS if f"| `{v}`" not in table]
    assert not unrowed, f"verbs missing from the verb table: {unrowed}"


def test_every_flag_is_mapped():
    sections = _sections()
    missing = []
    for verb, parser in VERBS.items():
        text = sections.get(verb, "")
        flags = set(_flags(parser))
        for sub in _subparsers(parser).values():       # probe's sub-verbs
            flags |= _flags(sub)
        missing += [f"{verb} {f}" for f in sorted(flags) if f"`{f}`" not in text]
    assert not missing, "flags cli-2.0.md does not map:\n  " + "\n  ".join(missing)


def test_no_section_names_a_verb_that_does_not_exist():
    """The other direction: a removed verb leaves the page."""
    ghosts = sorted(set(_sections()) - set(VERBS))
    assert not ghosts, f"sections for verbs the parser does not have: {ghosts}"
