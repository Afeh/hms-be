"""``GET /tenant/config`` and ``PATCH /tenant/config`` (plan §18, issue 0011).

Permission required: ``tenant:configure``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Response
from redis.asyncio import Redis

from app.api.deps import (
    get_redis,
    get_registry,
    get_tenant_config_store,
    require_permission,
)
from app.api.routing import ApiRouter, response_message
from app.core.context import get_context
from app.core.errors import ApiError
from app.db import TenantConnectionRegistry
from app.db.tenant_configs import TenantConfigStore
from app.schemas.tenant_config import TenantConfigPatch, TenantConfigResponse
from app.services.tenant_config import config_to_response, get_config, update_config
from app.utils.etag import require_version, set_etag

router = ApiRouter(prefix="/tenant/config", tags=["tenant-config"])


def _require_tenant_id() -> str:
    ctx = get_context()
    if ctx.tenant_id is None:
        raise ApiError(401, "unauthenticated", "Authentication is required")
    return ctx.tenant_id


@router.get("", dependencies=[Depends(require_permission("tenant:configure"))])
@response_message("Configuration retrieved successfully")
async def get_tenant_config(
    response: Response,
    store: Annotated[TenantConfigStore, Depends(get_tenant_config_store)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> dict[str, TenantConfigResponse]:
    """Return the effective tenant configuration.

    Works even when no config document exists (defaults are returned).
    Sets an ``ETag`` header with the config version for optimistic concurrency.
    """
    tenant_id = _require_tenant_id()
    config = await get_config(tenant_id, store, redis)
    set_etag(response, config.version)
    return {"config": config_to_response(config)}


@router.patch("", dependencies=[Depends(require_permission("tenant:configure"))])
@response_message("Configuration updated successfully")
async def patch_tenant_config(
    body: TenantConfigPatch,
    response: Response,
    store: Annotated[TenantConfigStore, Depends(get_tenant_config_store)],
    redis: Annotated[Redis, Depends(get_redis)],
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    if_match: Annotated[str | None, Header(alias="if-match")] = None,
) -> dict[str, TenantConfigResponse]:
    """Apply a partial update to the tenant configuration.

    Requires ``If-Match: "<version>"`` for optimistic concurrency.
    Returns ``412 version_mismatch`` for a stale version.
    Returns ``422 validation_failed`` for an invalid patch.
    """
    tenant_id = _require_tenant_id()
    expected_version = require_version(if_match)

    # Build the patch dict from non-None fields only.
    patch = {k: v for k, v in body.model_dump().items() if v is not None}

    updated = await update_config(
        tenant_id, patch, expected_version, store, redis, registry=registry
    )
    set_etag(response, updated.version)
    return {"config": config_to_response(updated)}
