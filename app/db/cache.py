"""Redis client factory. Keys are prefixed `t:{tenant_id}:` (plan section 4.5)."""

from __future__ import annotations

from redis.asyncio import Redis


def create_redis(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


def tenant_key(tenant_id: str, key: str) -> str:
    """Prefix a Redis key with the tenant scope: `t:{tenant_id}:{key}`."""
    return f"t:{tenant_id}:{key}"


__all__ = ["create_redis", "tenant_key"]
