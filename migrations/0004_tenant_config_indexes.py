"""Tenant config index: unique tenant_id per config document (issue 0011)."""

from __future__ import annotations

from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

TENANT_CONFIGS_COLLECTION = "tenant_configs"


def upgrade(db: Database[dict[str, Any]]) -> None:
    """Apply the migration. Forward-only: a correction is a new revision."""
    db[TENANT_CONFIGS_COLLECTION].create_index(
        [("tenant_id", ASCENDING)],
        unique=True,
        name="ix_tenant_configs_tenant_id_unique",
    )
