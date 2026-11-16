"""Authentication request and response shapes (plan §17, issues 0014/0016)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.base import BaseSchema
from app.schemas.identity import NameSchema, name_schema
from app.schemas.tenant import TenantProfileResponse

if TYPE_CHECKING:
    from app.models.identity import Identity
    from app.models.role import Role
    from app.models.tenant import Tenant
    from app.models.user import User


class LoginRequest(BaseSchema):
    """Credentials accepted by ``POST /auth/login``."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    device_label: str | None = Field(default=None, max_length=100)


class RoleSummary(BaseModel):
    """The role fields the client needs after login and on the profile."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    key: str
    name: str
    mfa_required: bool = False

    @classmethod
    def from_role(cls, role: Role) -> RoleSummary:
        return cls(id=role.id or "", key=role.key, name=role.name, mfa_required=role.mfa_required)


class LoginUser(BaseModel):
    """The signed-in identity, membership and roles returned by login."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    email: str
    name: NameSchema
    is_verified: bool
    tenant_id: str
    roles: list[RoleSummary]
    permissions: list[str]
    created_at: datetime | None = None

    @classmethod
    def from_models(
        cls,
        identity: Identity,
        user: User,
        roles: list[Role],
        permissions: list[str],
    ) -> LoginUser:
        return cls(
            id=user.id or "",
            email=identity.email,
            name=name_schema(identity.name),
            is_verified=identity.email_verified_at is not None,
            tenant_id=user.tenant_id,
            roles=[RoleSummary.from_role(role) for role in roles],
            permissions=permissions,
            created_at=user.created_at,
        )


class LoginResponse(BaseModel):
    """The successful login response envelope payload."""

    model_config = ConfigDict(extra="ignore")

    mfa_required: bool = False
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    user: LoginUser


class RefreshRequest(BaseModel):
    """Optional body for non-browser clients; browser clients use the cookie."""

    model_config = ConfigDict(extra="ignore")

    refresh_token: str | None = Field(default=None, min_length=1, max_length=512)


class RefreshResponse(BaseModel):
    """A rotated token pair returned by ``POST /auth/refresh``."""

    model_config = ConfigDict(extra="ignore")

    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime


class LogoutResponse(BaseModel):
    """The sign-out result; cookies are cleared even when no session matched."""

    model_config = ConfigDict(extra="ignore")

    signed_out: bool = True


class MeUser(BaseModel):
    """The caller's identity and membership fields exposed by ``GET /auth/me``."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    email: str
    name: NameSchema
    phone: str | None = None
    is_verified: bool
    mfa_enabled: bool = False
    tenant_id: str
    status: str
    display_name: str | None = None
    last_login_at: datetime | None = None
    created_at: datetime | None = None

    @classmethod
    def from_models(cls, identity: Identity, user: User) -> MeUser:
        return cls(
            id=user.id or "",
            email=identity.email,
            name=name_schema(identity.name),
            phone=identity.phone,
            is_verified=identity.email_verified_at is not None,
            mfa_enabled=identity.mfa.enabled,
            tenant_id=user.tenant_id,
            status=user.status,
            display_name=user.display_name,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )


class MeResponse(BaseModel):
    """The caller's profile, effective permissions and tenant."""

    model_config = ConfigDict(extra="ignore")

    user: MeUser
    roles: list[RoleSummary]
    permissions: list[str]
    tenant: TenantProfileResponse
    perm_version: int


def tenant_profile(tenant: Tenant) -> TenantProfileResponse:
    """Map a tenant document for the profile response without importing the service."""
    return TenantProfileResponse(
        id=tenant.id or "",
        name=tenant.name,
        slug=tenant.slug,
        code=tenant.code,
        status=tenant.status,
        plan=tenant.plan,
        country=tenant.country,
        timezone=tenant.timezone,
        legal_hold=tenant.legal_hold,
    )


__all__ = [
    "LoginRequest",
    "LoginResponse",
    "LoginUser",
    "LogoutResponse",
    "MeResponse",
    "MeUser",
    "RefreshRequest",
    "RefreshResponse",
    "RoleSummary",
    "tenant_profile",
]
