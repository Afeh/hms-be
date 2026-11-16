"""Vital-signs recording, listing and correction (plan §20, sprint S002)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, Query, Response

from app.api.deps import get_registry, require_permission
from app.api.routing import ApiRouter, response_message
from app.core.context import get_context
from app.core.errors import ApiError
from app.db import TenantConnectionRegistry
from app.schemas.vitals import (
    VitalsCreate,
    VitalsEnteredInError,
    VitalsListResponse,
    VitalsResponse,
)
from app.services.vitals import (
    create_vitals,
    list_vitals_for_patient,
    mark_vitals_entered_in_error,
    vitals_response,
)
from app.utils.etag import set_etag

router = ApiRouter(tags=["vitals"])


def _require_tenant_id() -> str:
    tenant_id = get_context().tenant_id
    if tenant_id is None:
        raise ApiError(401, "unauthenticated", "Authentication is required")
    return tenant_id


@router.post(
    "/visits/{visit_id}/vitals",
    status_code=201,
    dependencies=[Depends(require_permission("vitals:write"))],
)
@response_message("Vitals recorded successfully")
async def record_vitals(
    visit_id: str,
    body: VitalsCreate,
    response: Response,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
) -> dict[str, VitalsResponse]:
    """Record measurements against an open visit."""
    tenant_id = _require_tenant_id()
    vitals = await create_vitals(registry, tenant_id, visit_id, body)
    set_etag(response, vitals.version)
    return {"vitals": vitals_response(vitals)}


@router.get(
    "/patients/{patient_id}/vitals",
    dependencies=[Depends(require_permission("visit:read"))],
)
@response_message("Vitals retrieved successfully")
async def get_patient_vitals(
    patient_id: str,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    visit_id: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> VitalsListResponse:
    """List a patient's measurements newest first."""
    tenant_id = _require_tenant_id()
    vitals, next_cursor = await list_vitals_for_patient(
        registry,
        tenant_id,
        patient_id,
        visit_id=visit_id,
        measured_from=from_,
        measured_to=to,
        limit=limit,
        cursor=cursor,
    )
    return VitalsListResponse(
        vitals=[vitals_response(row) for row in vitals],
        next_cursor=next_cursor,
    )


@router.patch(
    "/vitals/{vitals_id}",
    dependencies=[Depends(require_permission("vitals:write"))],
)
@response_message("Vitals marked as entered in error")
async def patch_vitals(
    vitals_id: str,
    body: VitalsEnteredInError,
    response: Response,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
) -> dict[str, VitalsResponse]:
    """Retain a vital-signs row in history with a correction reason."""
    tenant_id = _require_tenant_id()
    vitals = await mark_vitals_entered_in_error(
        registry,
        tenant_id,
        vitals_id,
        body.reason,
    )
    set_etag(response, vitals.version)
    return {"vitals": vitals_response(vitals)}


__all__ = ["router"]
