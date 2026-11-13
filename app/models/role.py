"""Stored shape of a tenant role (plan §6.3)."""

from __future__ import annotations

from pydantic import Field

from app.db.tenancy import TenantDocument


class Role(TenantDocument):
    """A named permission set within one tenant."""

    key: str
    name: str
    permissions: list[str] = Field(default_factory=list)
    is_system: bool = False
    mfa_required: bool = False


__all__ = ["Role"]
