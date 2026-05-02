"""Tests for core/paths.py path-layout helpers."""

import os

import pytest

from acidcat.core import paths


class TestNormalize:
    def test_returns_absolute(self):
        result = paths.normalize("rel/path")
        assert os.path.isabs(result)

    def test_forward_slashes(self):
        result = paths.normalize("rel/path")
        assert "\\" not in result

    def test_idempotent(self):
        once = paths.normalize("/tmp/x")
        twice = paths.normalize(once)
        assert once == twice


class TestSafeLabel:
    def test_keeps_alnum(self):
        assert paths.safe_label("Hypnotize_03") == "Hypnotize_03"

    def test_strips_unsafe_runs(self):
        assert paths.safe_label("My / Pack #1") == "My_Pack_1"

    def test_collapses_dashes(self):
        # dashes are safe so they pass through, only "unsafe runs" collapse
        assert paths.safe_label("a-b-c") == "a-b-c"

    def test_dots_safe(self):
        assert paths.safe_label("v1.2.3") == "v1.2.3"

    def test_trims_leading_trailing_underscores(self):
        assert paths.safe_label("___wat___") == "wat"

    def test_empty_falls_back(self):
        assert paths.safe_label("") == "library"
        assert paths.safe_label(None) == "library"
        assert paths.safe_label("///") == "library"


class TestComparePath:
    def test_passthrough_on_posix(self, monkeypatch):
        monkeypatch.setattr(os, "name", "posix")
        assert paths.compare_path("/foo/Bar") == "/foo/Bar"

    def test_lowercases_on_windows(self, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        assert paths.compare_path("C:/Foo/Bar") == "c:/foo/bar"


class TestPathHash:
    def test_twelve_chars(self):
        h = paths.path_hash("/foo/bar")
        assert len(h) == 12
        # all hex
        int(h, 16)

    def test_stable_across_calls(self):
        a = paths.path_hash("/foo/bar")
        b = paths.path_hash("/foo/bar")
        assert a == b

    def test_distinguishes_paths(self):
        a = paths.path_hash("/foo/bar")
        b = paths.path_hash("/foo/baz")
        assert a != b

    def test_normalizes_input(self):
        # equal-after-normalization paths should produce equal hashes
        a = paths.path_hash("/foo/bar")
        b = paths.path_hash("/foo/bar/")  # trailing slash
        assert a == b


class TestCentralDbPathFor:
    def test_lives_in_libraries_dir(self):
        p = paths.central_db_path_for("/foo/Hypnotize", "hypnotize")
        assert p.startswith(paths.central_libraries_dir() + "/")
        assert p.endswith(".db")

    def test_label_and_hash_in_filename(self):
        p = paths.central_db_path_for("/foo/Hypnotize", "hypnotize")
        name = os.path.basename(p)
        assert name.startswith("hypnotize_")
        assert len(name) == len("hypnotize_") + 12 + len(".db")

    def test_unsafe_label_sanitized(self):
        p = paths.central_db_path_for("/foo/x", "weird name / with junk")
        name = os.path.basename(p)
        assert "/" not in name
        assert " " not in name


class TestInTreeDbPathFor:
    def test_inside_root(self):
        p = paths.in_tree_db_path_for("/foo/Hypnotize")
        assert p.startswith("/foo/Hypnotize/" if os.name != "nt"
                            else paths.normalize("/foo/Hypnotize") + "/")
        assert p.endswith("/.acidcat/index.db")


class TestRegistryPathResolution:
    def test_default_under_home(self, monkeypatch):
        # cli/env both unset
        p = paths.resolve_registry_path()
        assert p.endswith("/.acidcat/registry.db")

    def test_env_overrides_default(self, monkeypatch, tmp_path):
        custom = str(tmp_path / "alt.db")
        monkeypatch.setenv("ACIDCAT_REGISTRY", custom)
        assert paths.resolve_registry_path() == custom

    def test_cli_overrides_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ACIDCAT_REGISTRY", str(tmp_path / "from_env.db"))
        cli = str(tmp_path / "from_cli.db")
        assert paths.resolve_registry_path(cli) == cli


class TestLegacyDetection:
    def test_path_format(self):
        # not asserting existence; this is a deterministic path
        assert paths.legacy_global_db_path().endswith("/.acidcat/index.db")


class TestFindLibraryRootAbove:
    def test_returns_none_when_no_in_tree_db(self, tmp_path):
        f = tmp_path / "x.wav"
        f.write_bytes(b"")
        assert paths.find_library_root_above(str(f)) is None

    def test_finds_in_tree_db(self, tmp_path):
        lib = tmp_path / "lib"
        (lib / paths.ACIDCAT_DIR_NAME).mkdir(parents=True)
        (lib / paths.ACIDCAT_DIR_NAME / paths.INDEX_DB_NAME).write_bytes(b"")
        sub = lib / "drums"
        sub.mkdir()
        sample = sub / "kick.wav"
        sample.write_bytes(b"")
        result = paths.find_library_root_above(str(sample))
        assert result == paths.normalize(str(lib))
