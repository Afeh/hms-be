"""Authentication routes: login, refresh, logout and the caller's profile.

The sprint exposes login, refresh, logout and the caller's profile. MFA and
lockout endpoints are deliberately deferred; login is still rate limited per IP.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, Response

from app.api.deps import (
    CurrentUser,
    get_app_settings,
    get_current_user,
    get_optional_bearer_token,
    get_registry,
)
from app.api.routing import ApiRouter, response_message
from app.core.config import Settings
from app.core.context import current_context
from app.core.cookies import REFRESH_TOKEN_COOKIE, clear_auth_cookies, set_auth_cookies
from app.core.errors import ApiError
from app.core.permissions import ALL_PERMISSIONS
from app.core.rate_limit import rate_limit
from app.core.security import TokenValidationError, decode_access_token
from app.db import TenantConnectionRegistry
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LoginUser,
    LogoutResponse,
    MeResponse,
    MeUser,
    RefreshRequest,
    RefreshResponse,
    RoleSummary,
    tenant_profile,
)
from app.services.auth import login as authenticate
from app.services.sessions import revoke_refresh_token, revoke_session, rotate_refresh_token

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
    response: Response,
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
    set_auth_cookies(
        response,
        settings,
        access_token=result.issued.access_token,
        access_expires_in=result.issued.expires_in,
        refresh_token=result.issued.refresh_token,
        refresh_expires_at=result.issued.refresh_expires_at,
    )
    return LoginResponse(
        access_token=result.issued.access_token,
        expires_in=result.issued.expires_in,
        refresh_token=result.issued.refresh_token,
        refresh_expires_at=result.issued.refresh_expires_at,
        user=LoginUser.from_models(result.identity, result.user, result.roles, permissions),
    )


@router.post("/refresh")
@response_message("Token refreshed successfully")
async def refresh(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_app_settings)],
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    body: RefreshRequest | None = None,
) -> RefreshResponse:
    """Rotate the refresh token from its cookie (or body) and reset both cookies."""
    raw_refresh_token = body.refresh_token if body else None
    if raw_refresh_token is None:
        raw_refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if not raw_refresh_token:
        raise ApiError(401, "invalid_or_expired_token", "Refresh token is invalid or expired")

    issued = await rotate_refresh_token(registry, settings, raw_refresh_token)
    set_auth_cookies(
        response,
        settings,
        access_token=issued.access_token,
        access_expires_in=issued.expires_in,
        refresh_token=issued.refresh_token,
        refresh_expires_at=issued.refresh_expires_at,
    )
    return RefreshResponse(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        refresh_token=issued.refresh_token,
        refresh_expires_at=issued.refresh_expires_at,
    )


@router.post("/logout")
@response_message("Logged out successfully")
async def logout(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_app_settings)],
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
) -> LogoutResponse:
    """Revoke the current session when possible, then expire both auth cookies."""
    revoked = False
    try:
        access_token = get_optional_bearer_token(request)
    except ApiError:
        access_token = None
    if access_token:
        try:
            claims = decode_access_token(access_token, settings)
        except TokenValidationError:
            pass
        else:
            revoked = await revoke_session(registry, claims.tenant_id, claims.session_id)

    if not revoked:
        raw_refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
        if raw_refresh_token:
            await revoke_refresh_token(registry, raw_refresh_token)

    clear_auth_cookies(response, settings)
    return LogoutResponse()


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
