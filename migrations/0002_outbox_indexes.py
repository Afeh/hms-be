"""Outbox indexes: the dispatcher query, and a TTL on settled rows.

The dispatcher looks for unsent, unparked rows whose `available_at` has passed and
whose lease is free; the compound index covers that filter per tenant. The TTL
index only sees documents whose `sent_at` is a date, so pending rows are never
expired (plan section 6.7).
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

OUTBOX_COLLECTION = "outbox"
SETTLED_TTL_SECONDS = 7 * 24 * 60 * 60


def upgrade(db: Database[dict[str, Any]]) -> None:
    """Apply the migration. Forward-only: a correction is a new revision."""
    outbox = db[OUTBOX_COLLECTION]
    outbox.create_index(
        [("tenant_id", 1), ("sent_at", 1), ("parked_at", 1), ("available_at", 1)],
        name="ix_outbox_pending",
    )
    outbox.create_index(
        "sent_at",
        name="ttl_outbox_settled",
        expireAfterSeconds=SETTLED_TTL_SECONDS,
    )
