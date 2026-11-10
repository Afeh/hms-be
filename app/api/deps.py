"""Process-wide resources created once in the app lifespan, injected per request."""

from __future__ import annotations

from fastapi import Request
from redis.asyncio import Redis

from app.db import TenantConnectionRegistry


def get_registry(request: Request) -> TenantConnectionRegistry:
    registry: TenantConnectionRegistry = request.app.state.registry
    return registry


def get_redis(request: Request) -> Redis:
    client: Redis = request.app.state.redis
    return client
