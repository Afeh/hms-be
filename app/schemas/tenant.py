"""Response schemas for tenant-related endpoints (plan §18, issue 0010)."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from app.schemas.base import BaseSchema


class TenantProfileResponse(BaseSchema):
    """The public profile of the caller's tenant.

    Returned by ``GET /tenant``. Only safe, non-sensitive fields are exposed.
    """

    id: str = Field(alias="_id")
    name: str
    slug: str
    code: str | None = None
    status: str
    plan: str
    country: str | None = None
    timezone: str
    legal_hold: bool = False

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


__all__ = ["TenantProfileResponse"]
