"""Public session shapes (plan §17). Token hashes never leave the database."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.models.session import Session


class SessionResponse(BaseModel):
    """One login session in a profile response."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    device_label: str | None = None
    user_agent: str | None = None
    ip: str | None = None
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None
    revoked_reason: str | None = None

    @classmethod
    def from_session(cls, session: Session) -> SessionResponse:
        return cls(
            id=session.id or "",
            device_label=session.device_label,
            user_agent=session.user_agent,
            ip=session.ip,
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            idle_expires_at=session.idle_expires_at,
            absolute_expires_at=session.absolute_expires_at,
            revoked_at=session.revoked_at,
            revoked_reason=session.revoked_reason,
        )


__all__ = ["SessionResponse"]
