"""Public identity shapes (plan §17). Never include password or MFA secrets."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.models.identity import Identity, PersonName

IdentityStatus = Literal["active", "disabled"]


class NameSchema(BaseModel):
    """A person's name in API responses."""

    model_config = ConfigDict(extra="ignore")

    given: list[str]
    family: str
    other: str | None = None


class IdentityResponse(BaseModel):
    """The safe, public part of a global identity."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    email: str
    name: NameSchema
    phone: str | None = None
    is_verified: bool = False
    status: IdentityStatus = "active"
    last_login_at: datetime | None = None
    created_at: datetime | None = None

    @classmethod
    def from_identity(cls, identity: Identity) -> IdentityResponse:
        return cls(
            id=identity.id or "",
            email=identity.email,
            name=NameSchema(
                given=identity.name.given,
                family=identity.name.family,
                other=identity.name.other,
            ),
            phone=identity.phone,
            is_verified=identity.email_verified_at is not None,
            status=identity.status,
            last_login_at=identity.last_login_at,
            created_at=identity.created_at,
        )


def name_schema(name: PersonName) -> NameSchema:
    return NameSchema(given=name.given, family=name.family, other=name.other)


__all__ = ["IdentityResponse", "NameSchema", "name_schema"]
