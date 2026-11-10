"""Dependency probes behind `GET /health/ready`."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis

from app.db import TenantConnectionRegistry
from app.schemas.health import Dependencies, ReadyHealth, State

logger = logging.getLogger(__name__)


async def check_dependencies(registry: TenantConnectionRegistry, redis: Redis) -> ReadyHealth:
    """Probe MongoDB and Redis; report degraded rather than raising."""
    mongo = await _probe(registry.ping)
    redis_state = await _probe(redis.ping)
    healthy = mongo == "ok" and redis_state == "ok"
    return ReadyHealth(
        status="ok" if healthy else "degraded",
        dependencies=Dependencies(mongo=mongo, redis=redis_state),
    )


async def _probe(check: Callable[[], Awaitable[Any]]) -> State:
    try:
        await check()
    except Exception:  # a probe reports failure, it does not raise
        logger.warning("dependency probe failed", exc_info=True)
        return "unavailable"
    return "ok"
