"""Patient and counter indexes (sprint S001)."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.db.indexes import create_patient_indexes_sync


def upgrade(db: Database[dict[str, Any]]) -> None:
    """Apply the migration. Forward-only: a correction is a new revision."""
    create_patient_indexes_sync(db)
