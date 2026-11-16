"""Process-wide resources and the authenticated-request dependency (issue 0017).

``get_bearer_token`` is the only place the ``Authorization`` header is read.
``get_current_user`` turns it into a ``CurrentUser`` and populates the request
context; every protected route then works from that context or from the returned
object. Permissions are read from the tenant roles on every request (the sprint
does not use a Redis permission cache).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.context import RequestContext, get_context, set_context
from app.core.errors import ApiError
from app.core.permissions import ALL_PERMISSIONS, validate_permission
from app.core.security import TokenValidationError, decode_access_token
from app.db import (
    IdentityRepository,
    MembershipStore,
    RoleStore,
    TenantConnectionRegistry,
    TenantStore,
)
from app.db.sessions import SessionStore
from app.db.tenant_configs import TenantConfigStore
from app.models.identity import Identity
from app.models.role import Role
from app.models.session import Session
from app.models.tenant import Tenant
from app.models.user import User
from app.services.sessions import touch_session_if_stale


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Everything an authenticated route needs about the caller."""

    identity: Identity
    user: User
    tenant: Tenant
    session: Session
    roles: list[Role]
    permissions: frozenset[str]


def get_registry(request: Request) -> TenantConnectionRegistry:
    registry: TenantConnectionRegistry = request.app.state.registry
    return registry


def get_redis(request: Request) -> Redis:
    client: Redis = request.app.state.redis
    return client


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_tenant_store(request: Request) -> TenantStore:
    return TenantStore(get_registry(request))


def get_tenant_config_store(request: Request) -> TenantConfigStore:
    return TenantConfigStore(get_registry(request))


def get_bearer_token(request: Request) -> str:
    """Read the one header authentication is allowed to read."""
    authorization = request.headers.get("authorization")
    if not authorization:
        raise ApiError(401, "unauthenticated", "Authentication is required")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "invalid_or_expired_token", "Bearer token is malformed")
    return token.strip()


async def get_current_user(
    request: Request,
    token: str = Depends(get_bearer_token),
    registry: TenantConnectionRegistry = Depends(get_registry),
) -> CurrentUser:
    """Resolve a bearer token into identity, membership, session and permissions."""
    settings: Settings = request.app.state.settings
    try:
        claims = decode_access_token(token, settings)
    except TokenValidationError as exc:
        raise ApiError(
            401, "invalid_or_expired_token", "Access token is invalid or expired"
        ) from exc

    sessions = SessionStore(registry)
    session = await sessions.get(claims.tenant_id, claims.session_id)
    if session is None or not session.is_active:
        raise ApiError(401, "session_expired", "Session is no longer valid")
    if session.user_id != claims.user_id or session.identity_id != claims.identity_id:
        raise ApiError(401, "invalid_or_expired_token", "Access token does not match its session")

    identity = await IdentityRepository(registry).get(claims.identity_id)
    if identity is None or identity.status != "active":
        raise ApiError(401, "invalid_credentials", "Invalid email or password")

    membership = await MembershipStore(registry).get(claims.tenant_id, claims.user_id)
    if membership is None or membership.status != "active":
        raise ApiError(403, "account_deactivated", "This account is not active")
    if membership.identity_id != claims.identity_id:
        raise ApiError(
            401, "invalid_or_expired_token", "Access token does not match its membership"
        )

    tenant = await TenantStore(registry).get_by_id(claims.tenant_id)
    if tenant is None or tenant.status != "active":
        raise ApiError(403, "tenant_suspended", "This hospital is not active")

    if membership.perm_version != claims.perm_version:
        raise ApiError(401, "invalid_or_expired_token", "Access token is stale; sign in again")

    roles = await RoleStore(registry).list_by_ids(claims.tenant_id, membership.role_ids)
    permissions = frozenset(
        permission
        for role in roles
        for permission in role.permissions
        if permission in ALL_PERMISSIONS
    )

    existing = get_context()
    set_context(
        RequestContext(
            tenant_id=claims.tenant_id,
            request_id=existing.request_id,
            user_id=claims.user_id,
            session_id=claims.session_id,
            permissions=permissions,
            ip=existing.ip,
            device_info=existing.device_info,
        )
    )
    await touch_session_if_stale(registry, session)

    return CurrentUser(
        identity=identity,
        user=membership,
        tenant=tenant,
        session=session,
        roles=roles,
        permissions=permissions,
    )


def require_permission(
    permission: str,
) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    """Return a dependency that requires a catalogue permission.

    The permission is validated when the route is declared, so a typo fails at
    import time rather than returning a surprising 403 at runtime.
    """
    validated = validate_permission(permission)

    async def _dependency(
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if validated not in current_user.permissions:
            raise ApiError(403, "forbidden", f"Permission {validated!r} required")
        return current_user

    setattr(_dependency, "__required_permission__", validated)  # noqa: B010
    return _dependency


__all__ = [
    "CurrentUser",
    "get_app_settings",
    "get_bearer_token",
    "get_current_user",
    "get_redis",
    "get_registry",
    "get_tenant_config_store",
    "get_tenant_store",
    "require_permission",
]
