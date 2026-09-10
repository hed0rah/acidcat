"""The one rule that keeps the two packages from becoming one.

`acidcat_lab` imports `acidcat`. `acidcat` never imports `acidcat_lab`.

Stated as an intention that is a comment, and comments do not fail. So it is a
test, because the whole value of the split rests on it: `pip install acidcat`
must put no construction tooling on the machine of someone who only wants to
read files, and that is only true while the base package can be imported,
installed and used with the lab absent entirely.

The arrow is also the promotion rule. If something in the base ever needs the
lab, the thing it needs was analysis all along and belongs in the base -- move
it across rather than reversing the import. `probe` and `viz` arrived that way.
Nothing has ever needed to go the other direction, and if it does, that is a
design conversation, not a quick import.
"""

import ast
import os

import pytest

BASE = os.path.join(os.path.dirname(__file__), "..", "src", "acidcat")
LAB = os.path.join(os.path.dirname(__file__), "..", "src", "acidcat_lab")


def _python_files(root):
    for dirpath, _dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _imported_roots(path):
    """Top-level module names this file imports, however it spells it."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        try:
            tree = ast.parse(fh.read(), path)
        except SyntaxError:
            pytest.fail("%s does not parse" % path)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import, which cannot leave the package
            if node.level == 0 and node.module:
                out.add(node.module.split(".")[0])
    return out


def test_the_base_package_never_imports_the_lab():
    """The load-bearing assertion.

    Checked by reading the source rather than by importing, because an import
    inside a function or a try/except would not run during a test that only
    imports the module -- and a lazy import is exactly how this rule gets broken
    without anyone noticing.
    """
    offenders = []
    for path in _python_files(BASE):
        if "acidcat_lab" in _imported_roots(path):
            offenders.append(os.path.relpath(path, BASE))
    assert not offenders, (
        "the base package imports the lab in %s.\n"
        "Every install of acidcat would now need the construction tooling "
        "present. If the base genuinely needs this, the code it needs is "
        "analysis and belongs in acidcat -- move it across rather than "
        "importing backwards." % ", ".join(sorted(offenders)))


def test_the_base_package_does_not_mention_the_lab_at_all():
    """Not even in a string.

    A dynamic import -- importlib, __import__, a module name assembled at
    runtime -- would pass the AST check above while breaking the rule just as
    thoroughly.
    """
    offenders = []
    for path in _python_files(BASE):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            if "acidcat_lab" in fh.read():
                offenders.append(os.path.relpath(path, BASE))
    assert not offenders, (
        "acidcat_lab is named in %s. If that is a docstring pointing at the "
        "other package it is harmless, and this test is the wrong shape -- but "
        "check it is not a dynamic import first." % ", ".join(sorted(offenders)))


@pytest.mark.skipif(not os.path.isdir(LAB), reason="acidcat_lab not present")
def test_the_lab_depends_on_the_base():
    """The other half of the arrow, and the reason the lab is not a fork.

    A lab that reimplemented parsing would drift from the tool it is supposed
    to be testing, and the drift would be invisible: both would be wrong in the
    same direction because the same person wrote them a week apart.
    """
    uses = set()
    for path in _python_files(LAB):
        uses |= _imported_roots(path)
    assert "acidcat" in uses, (
        "no module in acidcat_lab imports acidcat. The lab exists to exercise "
        "the engine; if it has its own parsing, the two will drift and neither "
        "will notice.")


@pytest.mark.skipif(not os.path.isdir(LAB), reason="acidcat_lab not present")
def test_the_base_imports_cleanly_with_the_lab_uninstallable():
    """What a base-only install actually experiences.

    `acidcat_lab` is made unimportable for the length of this test, which is
    the honest simulation: not absent from disk, but absent from the import
    system the way it would be for someone who never asked for the extra.
    """
    import importlib
    import sys

    saved = {k: v for k, v in sys.modules.items() if k.startswith("acidcat")}
    blocker = _Blocker()
    sys.meta_path.insert(0, blocker)
    try:
        for name in list(sys.modules):
            if name.startswith("acidcat"):
                del sys.modules[name]
        mod = importlib.import_module("acidcat")
        assert mod.__version__, "the base package imported but reported no version"
        importlib.import_module("acidcat.core.walk")
    finally:
        sys.meta_path.remove(blocker)
        for name in list(sys.modules):
            if name.startswith("acidcat"):
                del sys.modules[name]
        sys.modules.update(saved)


def _imported_modules(path):
    """Every dotted module name this file imports, however it spells it."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        tree = ast.parse(fh.read(), path)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                out.add(node.module)
    return out


# The engine's front ends. A library that imports one of these is depending on
# argparse plumbing, a Textual app, or a transport, and breaks the day a flag
# or a screen moves. The lab consumes the engine: the public facade, or the
# core packages beneath it. Nothing above them.
_FRONT_ENDS = ("acidcat.commands", "acidcat.cli", "acidcat.tui_app",
               "acidcat.mcp_server")


@pytest.mark.skipif(not os.path.isdir(LAB), reason="acidcat_lab not present")
def test_the_lab_consumes_the_engine_not_its_front_ends():
    """The arrow points one way, and it also points at one layer.

    The one-way tests above are satisfied by a lab module that imports a
    command module, since that is still the right direction. It is the wrong
    layer: the first migrated module did exactly this, importing
    `acidcat.commands.inspect` and never using it, and nothing here said so.
    """
    offenders = []
    for path in _python_files(LAB):
        for mod in sorted(_imported_modules(path)):
            if any(mod == fe or mod.startswith(fe + ".") for fe in _FRONT_ENDS):
                offenders.append((os.path.relpath(path, LAB), mod))
    assert not offenders, (
        "the lab imports a front end: " + ", ".join(
            p + " -> " + m for p, m in offenders)
        + ". Use the public facade (acidcat.<name>) or acidcat.core; a command "
          "module is not a library.")


class _Blocker:
    """Refuses acidcat_lab at the import-system level."""

    def find_module(self, fullname, path=None):        # pragma: no cover
        return self if fullname.split(".")[0] == "acidcat_lab" else None

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] == "acidcat_lab":
            raise ImportError("acidcat_lab is not installed (simulated)")
        return None
