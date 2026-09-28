"""The shipped skill must describe the tool that actually exists.

`skills/acidcat/SKILL.md` is instructions for a model. A doc a human reads
wrong wastes their afternoon; a doc a model reads wrong gets executed. It had
drifted in three ways at once by 1.0 -- it named extras `[ml]` and `[viz]`,
neither of which has ever existed, so following its install line fails outright.
Nothing failed, because prose does not get imported.

So the claims that can be checked against the code are checked here: extras,
console scripts, CLI verbs, and MCP tool names. The judgement calls in it
(when to prefer which tool, what to tell the user) cannot be tested and are not
the point of this file.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parent.parent
SKILL = ROOT / "skills" / "acidcat" / "SKILL.md"

pytestmark = pytest.mark.skipif(not SKILL.is_file(), reason="skill not present")


def _text():
    return SKILL.read_text(encoding="utf-8")


def _pyproject():
    import tomllib
    with open(ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)


def test_the_skill_has_frontmatter_a_loader_can_read():
    t = _text()
    assert t.startswith("---\n"), "a skill needs YAML frontmatter to be loaded"
    head = t.split("---", 2)[1]
    assert re.search(r"^name:\s*acidcat\s*$", head, re.M), head[:200]
    assert "description:" in head


def test_every_extra_it_names_exists():
    """The bug that prompted this file: `pip install acidcat[ml]` fails."""
    real = set(_pyproject()["project"].get("optional-dependencies", {}))
    named = set(re.findall(r"`\[([a-z0-9-]+)\]`", _text()))
    assert named, "the regex stopped matching; it found no extras at all"
    unknown = named - real
    assert not unknown, (
        f"the skill tells a model to install extras that do not exist: "
        f"{sorted(unknown)} (real: {sorted(real)})")


def test_it_names_the_extras_that_gate_the_features_it_describes():
    """Not exhaustive -- these three are the ones whose absence turns a
    documented feature into an ImportError."""
    named = set(re.findall(r"`\[([a-z0-9-]+)\]`", _text()))
    for extra in ("mcp", "analysis", "tui"):
        assert extra in named, f"the skill never mentions the [{extra}] extra"


def test_every_console_script_it_names_exists():
    scripts = set(_pyproject()["project"].get("scripts", {}))
    t = _text()
    for name in ("acidcat", "acidcat-mcp"):
        assert name in scripts, f"pyproject no longer ships {name}"
        assert name in t, f"the skill never mentions {name}"


def test_every_mcp_tool_it_names_is_a_real_tool():
    pytest.importorskip("mcp")
    from acidcat.mcp_server.schema import TOOLS

    real = {t["name"] for t in TOOLS}
    # tool-shaped names in backticks: lower_snake_case with an underscore
    named = {m for m in re.findall(r"`([a-z][a-z0-9_]*_[a-z0-9_]*)`", _text())}
    named = {n for n in named if n in real or n.split("_")[0] in {
        "search", "get", "locate", "list", "index", "find", "analyze",
        "detect", "reindex", "register", "forget", "discover", "tag", "set"}}
    assert named, "found no tool references; the regex is wrong"
    unknown = named - real
    assert not unknown, (
        f"the skill names MCP tools that do not exist: {sorted(unknown)}")


def test_it_covers_the_destructive_tools():
    """A model that has not been told which calls write is the whole risk."""
    pytest.importorskip("mcp")
    from acidcat.mcp_server.schema import TOOLS

    destructive = {t["name"] for t in TOOLS
                   if t["annotations"].get("destructiveHint")}
    t = _text()
    missing = {n for n in destructive if f"`{n}`" not in t}
    assert not missing, (
        f"destructive tools the skill never names: {sorted(missing)}")


def test_it_states_that_registering_does_not_populate():
    """The failure this section exists to prevent: an agent registered four
    libraries, reported success, and left four empty shells, because nothing
    said reindex was a separate step."""
    t = _text().lower()
    assert "reindex" in t
    assert "does not" in t or "do not" in t
    window = t[t.find("register does not populate"):][:1200]
    assert window, "the section heading naming this went missing"
    assert "reindex" in window


def test_every_cli_verb_it_shows_exists():
    from acidcat import cli

    parser = cli._build_parser()
    real = set()
    for action in parser._actions:
        if getattr(action, "choices", None) and hasattr(action.choices, "keys"):
            real |= set(action.choices.keys())
    assert real, "could not enumerate the CLI verbs"

    # only where it is written as a command: inline code, or a line in a fenced
    # block. Prose contains "acidcat is a pure-Python tool", and `is` is not a
    # verb this should be hunting.
    t = _text()
    fenced = "\n".join(re.findall(r"```[a-z]*\n(.*?)```", t, re.S))
    shown = set(re.findall(r"`acidcat ([a-z][a-z0-9-]+)", t))
    shown |= set(re.findall(r"^acidcat ([a-z][a-z0-9-]+)", fenced, re.M))
    shown -= {"mcp"}                     # part of the acidcat-mcp script name
    assert shown, "found no command examples; the regex is wrong"
    unknown = shown - real
    assert not unknown, (
        f"the skill shows verbs that do not exist: {sorted(unknown)}")


def test_the_readme_tells_people_the_skill_is_there():
    """It shipped since before 1.0 and nothing pointed at it, so nobody knew.

    A skill in a repo nobody is told about is a file, not a feature.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "skills/acidcat" in readme, (
        "README does not mention the skill, so no user will find it")
    # and the copy line has to name the directory that exists. Mentioning the
    # skill while giving a path that does not resolve is the same dead end.
    install = re.search(r"cp -r (\S+) ~/\.claude/skills/", readme)
    assert install, "README has no install line for the skill"
    named = ROOT / install.group(1)
    assert named.is_dir(), (
        f"README says to copy {install.group(1)}, which does not exist")
    assert (named / "SKILL.md").is_file(), (
        f"{install.group(1)} has no SKILL.md, so copying it installs nothing")
