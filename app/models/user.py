"""Stored shape of a tenant membership (plan §6.3).

``users`` is the tenant-owned half of the identity/membership split. The sprint
fixed contract allows one active membership per identity, enforced by a partial
unique index on ``{tenant_id, identity_id}``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.tenancy import TenantDocument

UserStatus = Literal["invited", "active", "deactivated", "removed"]


class ProfessionalProfile(BaseModel):
    """Professional details shown in the staff directory."""

    model_config = ConfigDict(extra="ignore")

    license_no: str | None = None
    cadre: str | None = None
    signature_image_key: str | None = None


class User(TenantDocument):
    """A person's membership in one tenant."""

    identity_id: str | None = None
    invited_email_normalized: str | None = None
    role_ids: list[str] = Field(default_factory=list)
    perm_version: int = 1
    status: UserStatus = "active"
    professional: ProfessionalProfile | None = None
    display_name: str | None = None
    last_login_at: datetime | None = None
    removed_at: datetime | None = None
    removed_by: str | None = None

    @field_validator("identity_id", mode="before")
    @classmethod
    def _identity_id_to_str(cls, value: object) -> object:
        return str(value) if value is not None else None

    @field_validator("role_ids", mode="before")
    @classmethod
    def _role_ids_to_str(cls, value: object) -> object:
        if isinstance(value, list):
            return [str(item) for item in value]
        return value


__all__ = ["ProfessionalProfile", "User", "UserStatus"]
