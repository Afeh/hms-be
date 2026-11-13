"""Stored shape of one rotating refresh token (plan §6.3).

Only the SHA-256 hash of the opaque token is stored. Rows are kept after use so a
replayed token can be recognised and its whole family revoked.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field

from app.db.tenancy import TenantDocument


class RefreshToken(TenantDocument):
    """One generation of a session's refresh token."""

    session_id: str
    family_id: str
    token_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    used_at: datetime | None = None
    replaced_by_hash: str | None = None
    expires_at: datetime


__all__ = ["RefreshToken"]
