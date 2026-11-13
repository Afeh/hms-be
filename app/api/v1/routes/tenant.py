"""``GET /tenant`` — tenant profile endpoint (plan §18, issue 0010).

Permission required: ``tenant:configure``.
A suspended tenant cannot reach this handler (enforcement in 0022).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.api.deps import get_tenant_store, require_permission
from app.api.routing import ApiRouter, response_message
from app.core.context import get_context
from app.core.errors import ApiError
from app.db.tenants import TenantStore
from app.schemas.tenant import TenantProfileResponse
from app.services.tenants import get_tenant_profile

router = ApiRouter(prefix="/tenant", tags=["tenant"])


@router.get("", dependencies=[Depends(require_permission("tenant:configure"))])
@response_message("Hospital retrieved successfully")
async def tenant_profile(
    store: Annotated[TenantStore, Depends(get_tenant_store)],
) -> dict[str, TenantProfileResponse]:
    """Return the calling tenant's profile.

    The tenant ID comes from the verified request context (set by authentication
    middleware or by ``run_as_system`` in tests).  A missing context means the
    request was not authenticated and returns 401.
    """
    ctx = get_context()
    if ctx.tenant_id is None:
        raise ApiError(401, "unauthenticated", "Authentication is required")

    profile = await get_tenant_profile(ctx.tenant_id, store)
    return {"tenant": profile}
