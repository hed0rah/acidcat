"""scripts/release.py writes one version everywhere acidcat states it, and
folds the changelog only for a final release."""

import importlib.util
import pathlib
import shutil

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _release():
    spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tree(tmp_path):
    for rel in ("pyproject.toml", "ARCHITECTURE.md", "src/acidcat/__init__.py"):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dst)
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- **A thing.**\n\n"
        "## [2.0.1] - 2026-10-14\n\n- **Old.**\n", encoding="utf-8")
    return tmp_path


def _bump(tree, version):
    rel = _release()
    old = rel.current_version(tree)
    major, minor = (int(x) for x in old.split(".")[:2])
    return rel, old, version.format(major=major, minor=minor + 1, next=major + 1)


def test_a_final_release_writes_every_place_and_folds_the_changelog(tree):
    rel, old, new = _bump(tree, "{major}.{minor}.0")
    assert rel.main([new, "--root", str(tree), "--date", "2026-11-01"]) == 0
    assert f'version = "{new}"' in (tree / "pyproject.toml").read_text(encoding="utf-8")
    assert f'__version__ = "{new}"' in (tree / "src/acidcat/__init__.py").read_text(encoding="utf-8")
    assert f"v{new} ·" in (tree / "ARCHITECTURE.md").read_text(encoding="utf-8")
    log = (tree / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [Unreleased]\n\n## [{new}] - 2026-11-01\n\n### Added\n\n- **A thing.**" in log
    assert "## [2.0.1] - 2026-10-14" in log


def test_a_release_candidate_leaves_the_changelog_alone(tree):
    rel, old, new = _bump(tree, "{major}.{minor}.0rc1")
    before = (tree / "CHANGELOG.md").read_text(encoding="utf-8")
    rel.main([new, "--root", str(tree)])
    assert (tree / "CHANGELOG.md").read_text(encoding="utf-8") == before
    assert f'version = "{new}"' in (tree / "pyproject.toml").read_text(encoding="utf-8")


def test_the_final_sorts_after_its_candidates(tree):
    rel = _release()
    assert rel._key("2.1.0rc2") > rel._key("2.1.0rc1") > rel._key("2.1.0b3")
    assert rel._key("2.1.0") > rel._key("2.1.0rc9")


@pytest.mark.parametrize("version", ["2.0.0", "2.0.1", "2.1", "v2.1.0", "2.1.0-rc1"])
def test_a_bad_or_backwards_version_is_refused(tree, version):
    rel = _release()
    with pytest.raises(SystemExit):
        rel.main([version, "--root", str(tree)])


def test_an_empty_unreleased_section_is_refused(tree):
    (tree / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n## [2.0.1] - 2026-10-14\n", encoding="utf-8")
    rel, old, new = _bump(tree, "{major}.{minor}.0")
    with pytest.raises(SystemExit, match="empty"):
        rel.main([new, "--root", str(tree)])


def test_dry_run_writes_nothing(tree):
    rel, old, new = _bump(tree, "{major}.{minor}.0")
    before = {p: p.read_bytes() for p in tree.rglob("*") if p.is_file()}
    rel.main([new, "--root", str(tree), "--dry-run"])
    assert {p: p.read_bytes() for p in tree.rglob("*") if p.is_file()} == before
