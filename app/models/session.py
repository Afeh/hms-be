"""Stored shape of a login session (plan §6.3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from app.db.tenancy import TenantDocument

RevokedReason = Literal["logout", "admin", "reuse_detected", "password_change"]


class Session(TenantDocument):
    """One login; refresh token hashes point back at it."""

    user_id: str
    identity_id: str
    family_id: str
    device_label: str | None = None
    user_agent: str | None = None
    ip: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None
    revoked_reason: RevokedReason | None = None

    @property
    def is_active(self) -> bool:
        return self.is_active_at(datetime.now(UTC))

    def is_active_at(self, now: datetime) -> bool:
        return (
            self.revoked_at is None
            and self.idle_expires_at > now
            and self.absolute_expires_at > now
        )


__all__ = ["RevokedReason", "Session"]
