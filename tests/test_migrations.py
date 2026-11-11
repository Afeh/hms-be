"""The migration runner: apply once, record once, fail without a record.

Integration tests: run with `MONGODB_URI=mongodb://127.0.0.1:27017 pytest -m integration`.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from migrations.runner import (
    MIGRATIONS_DIR,
    TRACKING_COLLECTION,
    Migration,
    MigrationError,
    apply_migrations,
    create_migration,
    discover,
    pending,
)
from pymongo import MongoClient
from pymongo.database import Database

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("MONGODB_URI") is None,
        reason="set MONGODB_URI to run migration integration tests",
    ),
]


@pytest.fixture
def db() -> Iterator[Database[dict[str, Any]]]:
    uri = os.environ["MONGODB_URI"]
    client: MongoClient[dict[str, object]] = MongoClient(uri, serverSelectionTimeoutMS=2000)
    name = f"hms_test_migrations_{uuid.uuid4().hex[:8]}"
    client.drop_database(name)
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


def test_every_revision_applies_once_and_leaves_nothing_pending(
    db: Database[dict[str, Any]],
) -> None:
    applied = apply_migrations(db, reason="pytest")

    assert applied == [migration.revision for migration in discover()]
    assert pending(db) == []
    assert apply_migrations(db, reason="pytest") == []
    assert db[TRACKING_COLLECTION].count_documents({}) == len(discover())


def test_a_failing_migration_records_nothing(db: Database[dict[str, Any]], tmp_path: Path) -> None:
    path = tmp_path / "9999_boom.py"
    path.write_text(
        "from pymongo.database import Database\n\n\n"
        "def upgrade(db: Database) -> None:\n"
        "    raise RuntimeError('boom')\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationError, match="9999_boom"):
        apply_migrations(db, [Migration("9999", "boom", path)])

    assert pending(db, [Migration("9999", "boom", path)]) == [Migration("9999", "boom", path)]
    assert db[TRACKING_COLLECTION].count_documents({}) == 0


def test_the_tree_has_a_baseline(db: Database[dict[str, Any]]) -> None:
    del db
    assert (MIGRATIONS_DIR / "0001_baseline.py").exists()


def test_new_migration_takes_the_next_revision(tmp_path: Path) -> None:
    first = create_migration("add patient indexes", tmp_path)
    second = create_migration("next", tmp_path)

    assert first.name == "0001_add_patient_indexes.py"
    assert second.name == "0002_next.py"
    assert "def upgrade(db:" in first.read_text(encoding="utf-8")
