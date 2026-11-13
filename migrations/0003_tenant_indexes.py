"""Tenant indexes: unique slug and status lookup (issue 0010)."""

from __future__ import annotations

from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

TENANTS_COLLECTION = "tenants"


def upgrade(db: Database[dict[str, Any]]) -> None:
    """Apply the migration. Forward-only: a correction is a new revision."""
    tenants = db[TENANTS_COLLECTION]
    tenants.create_index(
        [("slug", ASCENDING)],
        unique=True,
        name="ix_tenants_slug_unique",
    )
    tenants.create_index(
        [("status", ASCENDING)],
        name="ix_tenants_status",
    )
