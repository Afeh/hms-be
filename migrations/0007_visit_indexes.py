"""Visit and vital-sign indexes (sprint S002)."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.db.indexes import create_visit_indexes_sync


def upgrade(db: Database[dict[str, Any]]) -> None:
    """Apply the migration. Forward-only: a correction is a new revision."""
    create_visit_indexes_sync(db)
