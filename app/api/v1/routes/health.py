"""Liveness and readiness probes.

Both are public and unauthenticated (plan section 27.8); the probes carry no
detail, while `/platform/health` exposes queue and provider state to platform
administrators.
"""

from __future__ import annotations

from fastapi import Depends
from redis.asyncio import Redis

from app.api.deps import get_redis, get_registry
from app.api.routing import ApiRouter, response_message
from app.core.errors import ApiError
from app.db import TenantConnectionRegistry
from app.schemas.health import LiveHealth, ReadyHealth
from app.services.health import check_dependencies

router = ApiRouter(prefix="/health", tags=["health"])


@router.get("/live")
@response_message("API is live")
async def live() -> dict[str, LiveHealth]:
    return {"health": LiveHealth()}


@router.get("/ready")
@response_message("Dependencies checked")
async def ready(
    registry: TenantConnectionRegistry = Depends(get_registry),
    redis: Redis = Depends(get_redis),
) -> dict[str, ReadyHealth]:
    health = await check_dependencies(registry, redis)
    if health.status != "ok":
        raise ApiError(503, "dependency_unavailable", "A dependency is unavailable")
    return {"health": health}
