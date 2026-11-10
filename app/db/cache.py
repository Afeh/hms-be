"""Redis client factory. Keys are prefixed `t:{tenant_id}:` (plan section 4.5)."""

from __future__ import annotations

from redis.asyncio import Redis


def create_redis(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)
