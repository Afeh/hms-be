"""Atomic per-tenant counters (sprint S001)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.db.tenancy import TenantCollection

COUNTERS_COLLECTION = "counters"


async def next_value(collection: TenantCollection, key: str) -> int:
    """Increment and return the named counter for the current tenant.

    ``find_one_and_update`` with ``upsert`` makes concurrent callers receive
    distinct values. The tenant guard scopes the filter and stamps the upsert.
    """
    now = datetime.now(UTC)
    document = await collection.find_one_and_update(
        {"key": key},
        {
            "$inc": {"value": 1},
            "$setOnInsert": {"created_at": now, "version": 1},
        },
        upsert=True,
        return_document=True,
    )
    return int(document["value"]) if document else 0


__all__ = ["COUNTERS_COLLECTION", "next_value"]
