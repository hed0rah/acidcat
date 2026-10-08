"""Nothing in acidcat calls the deprecated tuple API.

`acidcat.walk` and `acidcat.walk_file` stay through 2.x with a
DeprecationWarning naming `acidcat.open()` and go in 3.0
(architecture-2.0.md section 6). A deprecation acidcat's own code still
leans on cannot be removed on schedule, so every module in src/ and in
acidcat-lab is checked: none imports those names from the package root or
reaches them as `acidcat.walk` / `acidcat.walk_file`.

The walker dispatcher itself, `acidcat.core.walk.walk_file`, is not the tuple
API: it is what `acidcat.open()` is built on, and it stays internal.
"""

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_TREES = [ROOT / "src" / "acidcat", ROOT / "lab" / "src"]
_DEPRECATED = {"walk", "walk_file"}


def _modules():
    for tree in _TREES:
        for path in sorted(tree.rglob("*.py")):
            if path == ROOT / "src" / "acidcat" / "__init__.py":
                continue                  # where the deprecated names live
            yield path


def _calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "acidcat":
            out += [f"line {node.lineno}: from acidcat import {a.name}"
                    for a in node.names if a.name in _DEPRECATED]
        elif (isinstance(node, ast.Attribute) and node.attr in _DEPRECATED
              and isinstance(node.value, ast.Name) and node.value.id == "acidcat"):
            out.append(f"line {node.lineno}: acidcat.{node.attr}")
    return out


def test_the_trees_are_there():
    assert len(list(_modules())) > 200


@pytest.mark.parametrize("path", list(_modules()),
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_no_module_calls_the_tuple_api(path):
    found = _calls(path)
    assert not found, (f"{path.relative_to(ROOT)} uses the deprecated tuple "
                       f"API; use acidcat.open(): {found}")


def test_the_check_would_catch_one(tmp_path):
    p = tmp_path / "m.py"
    p.write_text("import acidcat\nfrom acidcat import walk_file, sniff\n"
                 "acidcat.walk('x')\n", encoding="utf-8")
    assert _calls(p) == ["line 2: from acidcat import walk_file",
                         "line 3: acidcat.walk"]
