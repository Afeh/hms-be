"""Stored shape of a tenant record (plan §6.2).

The `tenants` collection is exempt from the tenant guard (see TENANT_EXEMPT_COLLECTIONS)
because it *is* the registry of tenants; every lookup uses an explicit tenant ID.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TenantStatus = Literal["active", "suspended", "offboarding", "deleted"]
TenantPlan = Literal["starter", "professional", "enterprise"]
DatabaseMode = Literal["shared", "dedicated"]


class DatabaseConfig(BaseModel):
    """Storage allocation for a tenant."""

    model_config = ConfigDict(extra="ignore")

    mode: DatabaseMode = "shared"
    secret_ref: str | None = None  # path to the dedicated connection secret (future)


class Tenant(BaseModel):
    """One tenant (hospital / clinic group) in the platform.

    The `tenants` collection is exempt from the tenant guard (see TENANT_EXEMPT_COLLECTIONS).
    The document ``_id`` serves as the canonical tenant ID across the platform.
    The ``slug`` uniqueness is enforced by a database index (migration 0003).
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str | None = Field(default=None, alias="_id")
    name: str
    slug: str  # URL-safe, lowercase, immutable after creation
    code: str | None = None  # 3-letter hospital code, e.g. "ACH", "BSC"
    status: TenantStatus = "active"
    plan: str = "starter"
    country: str | None = None  # ISO 3166-1 alpha-2
    timezone: str = "UTC"  # IANA tz name, e.g. "Europe/London"
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    legal_hold: bool = False

    # Soft-delete / lifecycle
    suspended_at: datetime | None = None
    offboarding_at: datetime | None = None
    deleted_at: datetime | None = None
    deleted_by: str | None = None

    # Audit & versioning
    created_at: datetime | None = None
    created_by: str | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None
    version: int = 1

    @field_validator("id", mode="before")
    @classmethod
    def _id_to_str(cls, value: object) -> object:
        return str(value) if value is not None else None

    @property
    def tenant_id(self) -> str | None:
        return self.id


__all__ = [
    "DatabaseConfig",
    "DatabaseMode",
    "Tenant",
    "TenantPlan",
    "TenantStatus",
]
