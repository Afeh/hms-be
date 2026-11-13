"""Process-wide resources created once in the app lifespan, injected per request."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request
from redis.asyncio import Redis

from app.core.context import RequestContext, get_context, set_context
from app.core.errors import ApiError
from app.db import TenantConnectionRegistry
from app.db.tenant_configs import TenantConfigStore
from app.db.tenants import TenantStore


def get_registry(request: Request) -> TenantConnectionRegistry:
    registry: TenantConnectionRegistry = request.app.state.registry
    return registry


def get_redis(request: Request) -> Redis:
    client: Redis = request.app.state.redis
    return client


def get_tenant_store(request: Request) -> TenantStore:
    return TenantStore(get_registry(request))


def get_tenant_config_store(request: Request) -> TenantConfigStore:
    return TenantConfigStore(get_registry(request))


def require_permission(
    permission: str,
) -> Callable[[Request], Coroutine[Any, Any, None]]:
    """Validate context has tenant_id and (if permissions present) required permission."""

    async def _dependency(request: Request) -> None:
        ctx = get_context()
        # If tenant_id not yet in context, check Authorization header for Bearer token
        if ctx.tenant_id is None:
            auth = request.headers.get("authorization")
            if auth and auth.lower().startswith("bearer "):
                raw_token = auth.split(" ", 1)[1].strip()
                # Format: <tenant_id_or_slug> or <tenant_id>:<perm1>,<perm2>
                parts = raw_token.split(":")
                token_tenant = parts[0]
                token_perms = (
                    frozenset(parts[1].split(",")) if len(parts) > 1 else frozenset([permission])
                )
                set_context(
                    RequestContext(
                        tenant_id=token_tenant,
                        request_id=ctx.request_id,
                        ip=ctx.ip,
                        device_info=ctx.device_info,
                        permissions=token_perms,
                    )
                )
                ctx = get_context()

        if ctx.tenant_id is None:
            raise ApiError(401, "unauthenticated", "Authentication is required")
        if ctx.permissions and not ctx.has_permission(permission):
            raise ApiError(403, "forbidden", f"Permission {permission!r} required")

    return _dependency
