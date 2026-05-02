"""F-22: schema_version mismatch must surface as a clean error so we
do not silently run old SQL against a future schema.

Covers both the per-library DB (core/index.py) and the global registry
(core/registry.py).
"""

import sqlite3

import pytest

from acidcat.core import index as idx
from acidcat.core import registry as reg


def _stamp_meta(db_path, value):
    """Open the meta table directly and overwrite schema_version."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE meta SET v = ? WHERE k = 'schema_version'",
            (str(value),),
        )
        conn.commit()
    finally:
        conn.close()


class TestPerLibraryDbVersion:
    def test_open_db_accepts_current_version(self, tmp_path):
        db = str(tmp_path / "ok.db")
        idx.open_db(db).close()
        # second open should also succeed
        idx.open_db(db).close()

    def test_open_db_rejects_future_version(self, tmp_path):
        db = str(tmp_path / "future.db")
        idx.open_db(db).close()
        _stamp_meta(db, idx.SCHEMA_VERSION + 1)
        with pytest.raises(idx.SchemaVersionError, match="newer client"):
            idx.open_db(db)

    def test_open_db_rejects_unparseable_version(self, tmp_path):
        db = str(tmp_path / "weird.db")
        idx.open_db(db).close()
        _stamp_meta(db, "not-a-version")
        with pytest.raises(idx.SchemaVersionError, match="unparseable"):
            idx.open_db(db)


class TestRegistryDbVersion:
    def test_open_registry_accepts_current_version(self, tmp_path):
        db = str(tmp_path / "registry.db")
        reg.open_registry(db).close()
        reg.open_registry(db).close()

    def test_open_registry_rejects_future_version(self, tmp_path):
        db = str(tmp_path / "registry_future.db")
        reg.open_registry(db).close()
        _stamp_meta(db, reg.REGISTRY_SCHEMA_VERSION + 1)
        with pytest.raises(reg.RegistrySchemaVersionError, match="newer client"):
            reg.open_registry(db)
