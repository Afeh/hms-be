"""Stored shape of a global identity (plan §6.3).

``identities`` is exempt from the tenant guard: the same person may be invited by
another hospital after their membership is removed, and login resolves the
identity before the tenant is known. It never carries a ``tenant_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

IdentityStatus = Literal["active", "disabled"]


class PersonName(BaseModel):
    """A person's name. ``given`` is always a list, even with one given name."""

    model_config = ConfigDict(extra="ignore")

    given: list[str] = Field(min_length=1)
    family: str
    other: str | None = None

    @field_validator("given", mode="before")
    @classmethod
    def _given_to_list(cls, value: object) -> object:
        """Accept the pre-0012 seed shape (`given: "Acme"`) as well as a list."""
        if isinstance(value, str):
            return [value]
        return value


class MfaState(BaseModel):
    """MFA state. Enrollment endpoints arrive in 0014; the shape lives here now."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    secret_enc: str | None = None
    recovery_code_hashes: list[str] = Field(default_factory=list)
    enrolled_at: datetime | None = None


class Identity(BaseModel):
    """One global person record."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str | None = Field(default=None, alias="_id")
    email: str
    email_normalized: str
    name: PersonName
    phone: str | None = None
    password_hash: str | None = None
    password_changed_at: datetime | None = None
    failed_login_count: int = 0
    locked_until: datetime | None = None
    email_verified_at: datetime | None = None
    mfa: MfaState = Field(default_factory=MfaState)
    status: IdentityStatus = "active"
    last_login_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _id_to_str(cls, value: object) -> object:
        return str(value) if value is not None else None


__all__ = ["Identity", "IdentityStatus", "MfaState", "PersonName"]
