"""Authentication routes: login and the caller's profile (issues 0014/0016).

The sprint exposes only ``POST /auth/login`` and ``GET /auth/me``. MFA and
lockout endpoints are deliberately deferred; login is still rate limited per IP.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.api.deps import CurrentUser, get_app_settings, get_current_user, get_registry
from app.api.routing import ApiRouter, response_message
from app.core.config import Settings
from app.core.context import current_context
from app.core.permissions import ALL_PERMISSIONS
from app.core.rate_limit import rate_limit
from app.db import TenantConnectionRegistry
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LoginUser,
    MeResponse,
    MeUser,
    RoleSummary,
    tenant_profile,
)
from app.services.auth import login as authenticate

router = ApiRouter(prefix="/auth", tags=["auth"])


async def _login_rate_limit(request: Request) -> None:
    """Apply the configured per-IP login quota."""
    settings: Settings = request.app.state.settings
    limiter = rate_limit(
        "auth_login_ip",
        limit=settings.rate_limit_login_ip,
        window_seconds=settings.rate_limit_window_seconds,
    )
    await limiter(request, request.app.state.redis)


@router.post("/login", dependencies=[Depends(_login_rate_limit)])
@response_message("Login successful")
async def login(
    body: LoginRequest,
    settings: Annotated[Settings, Depends(get_app_settings)],
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
) -> LoginResponse:
    """Authenticate an email and password, then issue a session token pair."""
    context = current_context()
    result = await authenticate(
        registry,
        settings,
        email=body.email,
        password=body.password,
        device_label=body.device_label,
        user_agent=context.device_info if context else None,
        ip=context.ip if context else None,
    )
    permissions = sorted(
        {
            permission
            for role in result.roles
            for permission in role.permissions
            if permission in ALL_PERMISSIONS
        }
    )
    return LoginResponse(
        access_token=result.issued.access_token,
        expires_in=result.issued.expires_in,
        refresh_token=result.issued.refresh_token,
        refresh_expires_at=result.issued.refresh_expires_at,
        user=LoginUser.from_models(result.identity, result.user, result.roles, permissions),
    )


@router.get("/me")
@response_message("Profile retrieved successfully")
async def me(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> MeResponse:
    """Return the authenticated caller's profile, roles and permissions."""
    roles = sorted(current_user.roles, key=lambda role: role.key)
    return MeResponse(
        user=MeUser.from_models(current_user.identity, current_user.user),
        roles=[RoleSummary.from_role(role) for role in roles],
        permissions=sorted(current_user.permissions),
        tenant=tenant_profile(current_user.tenant),
        perm_version=current_user.user.perm_version,
    )


__all__ = ["router"]
