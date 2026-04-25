"""Tests for core/registry.py."""

import os
import time

import pytest

from acidcat.core import paths, registry as reg


@pytest.fixture
def reg_conn(tmp_path, monkeypatch):
    """Open a registry inside tmp_path so tests cannot collide with each other
    or with the user's real registry."""
    monkeypatch.setenv("ACIDCAT_REGISTRY", str(tmp_path / "registry.db"))
    conn = reg.open_registry()
    yield conn
    conn.close()


def _mkroot(tmp_path, name):
    """Create a real directory under tmp_path so paths.normalize survives."""
    d = tmp_path / name
    d.mkdir()
    return paths.normalize(str(d))


def _mkdb(tmp_path, root, label):
    """Pretend the per-lib DB file exists at the central path so list_libraries
    only_existing=True returns the row."""
    db = paths.central_db_path_for(root, label)
    os.makedirs(os.path.dirname(db), exist_ok=True)
    open(db, "wb").close()
    return db


class TestSchemaCreation:
    def test_meta_table_seeded(self, reg_conn):
        row = reg_conn.execute(
            "SELECT v FROM meta WHERE k = 'schema_version'"
        ).fetchone()
        assert row["v"] == str(reg.REGISTRY_SCHEMA_VERSION)

    def test_libraries_table_exists(self, reg_conn):
        rows = reg_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert "libraries" in names


class TestRegister:
    def test_basic(self, reg_conn, tmp_path):
        root = _mkroot(tmp_path, "lib_a")
        db = paths.central_db_path_for(root, "lib_a")
        reg.register_library(reg_conn, root, label="lib_a", db_path=db)
        rows = reg.list_libraries(reg_conn)
        assert len(rows) == 1
        assert rows[0]["root_path"] == root
        assert rows[0]["label"] == "lib_a"
        assert rows[0]["in_tree"] == 0

    def test_in_tree_flag(self, reg_conn, tmp_path):
        root = _mkroot(tmp_path, "intree")
        db = paths.in_tree_db_path_for(root)
        reg.register_library(reg_conn, root, label="intree",
                             db_path=db, in_tree=True)
        row = reg.list_libraries(reg_conn)[0]
        assert row["in_tree"] == 1

    def test_label_is_required(self, reg_conn, tmp_path):
        root = _mkroot(tmp_path, "x")
        db = paths.central_db_path_for(root, "x")
        with pytest.raises(ValueError):
            reg.register_library(reg_conn, root, label=None, db_path=db)
        with pytest.raises(ValueError):
            reg.register_library(reg_conn, root, label="", db_path=db)

    def test_idempotent_re_register_preserves_created_at(self, reg_conn, tmp_path):
        root = _mkroot(tmp_path, "x")
        db = paths.central_db_path_for(root, "x")
        reg.register_library(reg_conn, root, label="x", db_path=db)
        first = reg_conn.execute(
            "SELECT created_at, last_seen_at FROM libraries WHERE root_path = ?",
            (root,),
        ).fetchone()
        time.sleep(0.01)
        reg.register_library(reg_conn, root, label="x", db_path=db)
        second = reg_conn.execute(
            "SELECT created_at, last_seen_at FROM libraries WHERE root_path = ?",
            (root,),
        ).fetchone()
        assert first["created_at"] == second["created_at"]
        assert second["last_seen_at"] >= first["last_seen_at"]


class TestOverlapRejection:
    def test_child_of_existing_rejected(self, reg_conn, tmp_path):
        parent = _mkroot(tmp_path, "parent")
        (tmp_path / "parent" / "child").mkdir()
        child = paths.normalize(str(tmp_path / "parent" / "child"))
        reg.register_library(
            reg_conn, parent, label="parent",
            db_path=paths.central_db_path_for(parent, "parent"),
        )
        with pytest.raises(reg.OverlapError) as excinfo:
            reg.register_library(
                reg_conn, child, label="child",
                db_path=paths.central_db_path_for(child, "child"),
            )
        assert "parent" in str(excinfo.value)

    def test_parent_of_existing_rejected(self, reg_conn, tmp_path):
        (tmp_path / "p" / "child").mkdir(parents=True)
        parent = paths.normalize(str(tmp_path / "p"))
        child = paths.normalize(str(tmp_path / "p" / "child"))
        reg.register_library(
            reg_conn, child, label="child",
            db_path=paths.central_db_path_for(child, "child"),
        )
        with pytest.raises(reg.OverlapError):
            reg.register_library(
                reg_conn, parent, label="parent",
                db_path=paths.central_db_path_for(parent, "parent"),
            )

    def test_exact_match_is_allowed(self, reg_conn, tmp_path):
        root = _mkroot(tmp_path, "x")
        db = paths.central_db_path_for(root, "x")
        reg.register_library(reg_conn, root, label="x", db_path=db)
        # exact re-register of the same root: not an overlap
        reg.register_library(reg_conn, root, label="x", db_path=db)
        assert len(reg.list_libraries(reg_conn)) == 1

    def test_sibling_paths_allowed(self, reg_conn, tmp_path):
        a = _mkroot(tmp_path, "a")
        b = _mkroot(tmp_path, "b")
        reg.register_library(reg_conn, a, label="a",
                             db_path=paths.central_db_path_for(a, "a"))
        reg.register_library(reg_conn, b, label="b",
                             db_path=paths.central_db_path_for(b, "b"))
        assert len(reg.list_libraries(reg_conn)) == 2


class TestUpdateStats:
    def test_updates_counts(self, reg_conn, tmp_path):
        root = _mkroot(tmp_path, "x")
        reg.register_library(
            reg_conn, root, label="x",
            db_path=paths.central_db_path_for(root, "x"),
        )
        reg.update_stats(reg_conn, root, sample_count=42, feature_count=10,
                         last_indexed_at=12345.0)
        row = reg.list_libraries(reg_conn)[0]
        assert row["sample_count"] == 42
        assert row["feature_count"] == 10
        assert row["last_indexed_at"] == 12345.0


class TestForgetAndGet:
    def test_forget_by_label(self, reg_conn, tmp_path):
        root = _mkroot(tmp_path, "lab")
        reg.register_library(
            reg_conn, root, label="lab",
            db_path=paths.central_db_path_for(root, "lab"),
        )
        n = reg.forget_library(reg_conn, "lab")
        assert n == 1
        assert reg.get_library(reg_conn, "lab") is None

    def test_forget_by_root(self, reg_conn, tmp_path):
        root = _mkroot(tmp_path, "byroot")
        reg.register_library(
            reg_conn, root, label="byroot",
            db_path=paths.central_db_path_for(root, "byroot"),
        )
        n = reg.forget_library(reg_conn, root)
        assert n == 1

    def test_forget_unknown_returns_zero(self, reg_conn):
        assert reg.forget_library(reg_conn, "nope") == 0

    def test_get_by_label(self, reg_conn, tmp_path):
        root = _mkroot(tmp_path, "g1")
        reg.register_library(
            reg_conn, root, label="g1",
            db_path=paths.central_db_path_for(root, "g1"),
        )
        row = reg.get_library(reg_conn, "g1")
        assert row is not None
        assert row["root_path"] == root

    def test_get_by_root(self, reg_conn, tmp_path):
        root = _mkroot(tmp_path, "g2")
        reg.register_library(
            reg_conn, root, label="g2",
            db_path=paths.central_db_path_for(root, "g2"),
        )
        assert reg.get_library(reg_conn, root) is not None


class TestListAndOrphans:
    def test_only_existing_filters_out_missing_db(self, reg_conn, tmp_path):
        root_real = _mkroot(tmp_path, "real")
        _mkdb(tmp_path, root_real, "real")
        reg.register_library(
            reg_conn, root_real, label="real",
            db_path=paths.central_db_path_for(root_real, "real"),
        )
        # second library: register but do NOT create the DB file
        root_orphan = _mkroot(tmp_path, "orphan")
        reg.register_library(
            reg_conn, root_orphan, label="orphan",
            db_path=paths.central_db_path_for(root_orphan, "orphan"),
        )
        all_rows = reg.list_libraries(reg_conn, only_existing=False)
        existing = reg.list_libraries(reg_conn, only_existing=True)
        assert len(all_rows) == 2
        assert len(existing) == 1
        assert existing[0]["label"] == "real"

    def test_find_orphans_returns_only_orphans(self, reg_conn, tmp_path):
        root_real = _mkroot(tmp_path, "real")
        _mkdb(tmp_path, root_real, "real")
        reg.register_library(
            reg_conn, root_real, label="real",
            db_path=paths.central_db_path_for(root_real, "real"),
        )
        root_orphan = _mkroot(tmp_path, "orphan")
        reg.register_library(
            reg_conn, root_orphan, label="orphan",
            db_path=paths.central_db_path_for(root_orphan, "orphan"),
        )
        orphans = reg.find_orphans(reg_conn)
        assert len(orphans) == 1
        assert orphans[0]["label"] == "orphan"


class TestFindLibraryForPath:
    def test_returns_none_when_path_outside_all_libs(self, reg_conn, tmp_path):
        root = _mkroot(tmp_path, "lib1")
        reg.register_library(
            reg_conn, root, label="lib1",
            db_path=paths.central_db_path_for(root, "lib1"),
        )
        outside = _mkroot(tmp_path, "outside")
        assert reg.find_library_for_path(reg_conn, outside + "/x.wav") is None

    def test_finds_containing_lib(self, reg_conn, tmp_path):
        root = _mkroot(tmp_path, "lib1")
        sub = tmp_path / "lib1" / "drums"
        sub.mkdir()
        reg.register_library(
            reg_conn, root, label="lib1",
            db_path=paths.central_db_path_for(root, "lib1"),
        )
        sample = paths.normalize(str(sub / "kick.wav"))
        result = reg.find_library_for_path(reg_conn, sample)
        assert result is not None
        assert result["label"] == "lib1"
