"""The stored shape of one outbox row: a side effect owed after a commit.

A row is written in the same transaction as the state change that needs it (plan
section 12, "Transactional outbox"), so a request that fails to commit leaves no
pending email and no queued job behind. Nothing here queries MongoDB; the access
layer is `app/db/outbox.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from app.db.tenancy import TenantDocument


class OutboxEvent(TenantDocument):
    """One side effect, waiting for the transaction that produced it to commit."""

    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))
    available_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    attempts: int = 0
    sent_at: datetime | None = None
    last_error: str | None = None
    #: Lease held by the dispatcher working on the row; the sweep reclaims it.
    locked_until: datetime | None = None
    locked_by: str | None = None
    #: Set once the attempt cap is reached: the row stays unsent and is never retried.
    parked_at: datetime | None = None
