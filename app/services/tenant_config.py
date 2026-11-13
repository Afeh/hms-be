"""Tenant configuration service with Redis cache (plan §4.7, issue 0011).

Cache strategy
--------------
Full config:    ``t:{tenant_id}:config``       TTL 60 s
Security block: ``t:{tenant_id}:config:sec``  TTL  5 s

On every write both keys are deleted and a Redis ``PUBLISH`` is sent on
``t:{tenant_id}:config:changed`` so other API instances drop their local copy.
The 5-second ceiling on the security block means stale security settings are
served for at most 5 seconds even after a cross-instance write.

Fail-open: a Redis error during cache reads causes the service to fetch from
MongoDB.  A Redis error during cache writes is logged but does not fail the
request.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from app.core.context import current_context
from app.core.errors import ApiError, VersionConflictError
from app.db.cache import tenant_key
from app.db.client import TenantConnectionRegistry
from app.db.tenancy import run_as_system
from app.db.tenant_configs import TenantConfigStore
from app.models.tenant_config import SecurityConfig, TenantConfig
from app.schemas.tenant_config import TenantConfigResponse

logger = logging.getLogger(__name__)

_CONFIG_TTL = 60  # seconds
_SECURITY_TTL = 5  # seconds
_CONFIG_KEY = "config"
_SECURITY_KEY = "config:sec"
_CHANGED_CHANNEL = "config:changed"


def _config_key(tenant_id: str) -> str:
    return tenant_key(tenant_id, _CONFIG_KEY)


def _security_key(tenant_id: str) -> str:
    return tenant_key(tenant_id, _SECURITY_KEY)


def _changed_channel(tenant_id: str) -> str:
    return tenant_key(tenant_id, _CHANGED_CHANNEL)


def config_to_response(config: TenantConfig) -> TenantConfigResponse:
    """Serialize a TenantConfig model to the response schema."""
    raw = config.model_dump(by_alias=False)
    return TenantConfigResponse(
        tenant_id=config.tenant_id,
        version=config.version,
        branding=raw["branding"],
        modules=raw["modules"],
        patient_id_format=raw["patient_id_format"],
        document_types=raw.get("document_types", []),
        visit_types=raw.get("visit_types", []),
        ai=raw["ai"],
        security=raw["security"],
        retention=raw["retention"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_config(
    tenant_id: str,
    store: TenantConfigStore,
    redis: Redis,
) -> TenantConfig:
    """Return the effective config, using the Redis cache when possible."""
    try:
        cached = await redis.get(_config_key(tenant_id))
        if cached:
            return TenantConfig.model_validate_json(cached)
    except Exception:
        logger.warning("tenant config cache read failed for %s", tenant_id, exc_info=True)

    config = await store.get_or_default(tenant_id)
    await _write_cache(tenant_id, config, redis)
    return config


async def get_security_config(
    tenant_id: str,
    store: TenantConfigStore,
    redis: Redis,
) -> SecurityConfig:
    """Return *only* the security block, with its own 5-second TTL."""
    try:
        cached = await redis.get(_security_key(tenant_id))
        if cached:
            return SecurityConfig.model_validate_json(cached)
    except Exception:
        logger.warning("tenant security-config cache read failed for %s", tenant_id, exc_info=True)

    config = await store.get_or_default(tenant_id)
    await _write_cache(tenant_id, config, redis)
    return config.security


async def update_config(
    tenant_id: str,
    patch: Mapping[str, Any],
    expected_version: int,
    store: TenantConfigStore,
    redis: Redis,
    registry: TenantConnectionRegistry | None = None,
) -> TenantConfig:
    """Apply a partial patch, invalidate the cache, audit the change, and return the updated config.

    Raises ``ApiError(412, "version_mismatch")`` for a stale version.
    Raises ``ApiError(422, "validation_failed")`` for an invalid patch.
    """
    old_config = await store.get_or_default(tenant_id)

    try:
        updated = await store.update(tenant_id, patch, expected_version)
    except VersionConflictError:
        raise ApiError(
            412,
            "version_mismatch",
            "The config changed since it was read; fetch it again",
        ) from None

    await _invalidate_cache(tenant_id, redis)

    if registry is not None:
        await _record_audit(registry, tenant_id, patch, old_config, updated)

    return updated


async def _record_audit(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    patch: Mapping[str, Any],
    old_config: TenantConfig,
    new_config: TenantConfig,
) -> None:
    try:
        ctx = current_context()
        audit_col = registry.system_collection("audit_logs")
        security_diff: dict[str, Any] | None = None
        if "security" in patch:
            security_diff = {
                "previous": old_config.security.model_dump(),
                "new": new_config.security.model_dump(),
            }
        audit_entry = {
            "tenant_id": tenant_id,
            "at": datetime.now(UTC),
            "action": "tenant_config.update",
            "resource_type": "tenant_config",
            "resource_id": tenant_id,
            "outcome": "success",
            "changed_fields": sorted(patch.keys()),
            "diff": security_diff,
            "actor_id": ctx.user_id if ctx else None,
            "session_id": ctx.session_id if ctx else None,
            "request_id": ctx.request_id if ctx else None,
            "ip": ctx.ip if ctx else None,
        }
        with run_as_system("audit_log.write"):
            await audit_col.insert_one(audit_entry)
    except Exception:
        logger.warning(
            "failed to write audit log for tenant %s config update", tenant_id, exc_info=True
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _write_cache(tenant_id: str, config: TenantConfig, redis: Redis) -> None:
    """Write the full config and the security block to Redis."""
    try:
        payload = config.model_dump_json()
        sec_payload = config.security.model_dump_json()
        await redis.set(_config_key(tenant_id), payload, ex=_CONFIG_TTL)
        await redis.set(_security_key(tenant_id), sec_payload, ex=_SECURITY_TTL)
    except Exception:
        logger.warning("tenant config cache write failed for %s", tenant_id, exc_info=True)


async def _invalidate_cache(tenant_id: str, redis: Redis) -> None:
    """Delete both cache entries and publish a change notification."""
    try:
        await redis.delete(_config_key(tenant_id), _security_key(tenant_id))
        await redis.publish(_changed_channel(tenant_id), "invalidated")
    except Exception:
        logger.warning("tenant config cache invalidation failed for %s", tenant_id, exc_info=True)


__all__ = [
    "config_to_response",
    "get_config",
    "get_security_config",
    "update_config",
]
