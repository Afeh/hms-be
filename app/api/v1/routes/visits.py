"""Visit creation, lookup, update and close (plan §20, sprint S002)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Query, Response

from app.api.deps import get_registry, require_permission
from app.api.routing import ApiRouter, response_message
from app.core.context import get_context
from app.core.errors import ApiError
from app.db import TenantConnectionRegistry
from app.schemas.visit import (
    VisitClose,
    VisitCreate,
    VisitListResponse,
    VisitResponse,
    VisitUpdate,
)
from app.services.visits import (
    close_visit,
    create_visit,
    get_visit_detail,
    list_visits_for_patient,
    update_visit,
)
from app.utils.etag import require_version, set_etag

router = ApiRouter(tags=["visits"])


def _require_tenant_id() -> str:
    tenant_id = get_context().tenant_id
    if tenant_id is None:
        raise ApiError(401, "unauthenticated", "Authentication is required")
    return tenant_id


@router.post(
    "/patients/{patient_id}/visits",
    status_code=201,
    dependencies=[Depends(require_permission("visit:create"))],
)
@response_message("Visit created successfully")
async def create_patient_visit(
    patient_id: str,
    body: VisitCreate,
    response: Response,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
) -> dict[str, VisitResponse]:
    """Open a visit for a patient in the caller's tenant."""
    tenant_id = _require_tenant_id()
    visit, clinician_name = await create_visit(registry, tenant_id, patient_id, body)
    set_etag(response, visit.version)
    return {
        "visit": VisitResponse.from_visit(visit, clinician_name=clinician_name),
    }


@router.get(
    "/patients/{patient_id}/visits",
    dependencies=[Depends(require_permission("visit:read"))],
)
@response_message("Visits retrieved successfully")
async def get_patient_visits(
    patient_id: str,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> VisitListResponse:
    """List a patient's visits newest first."""
    tenant_id = _require_tenant_id()
    visits, next_cursor = await list_visits_for_patient(
        registry,
        tenant_id,
        patient_id,
        limit=limit,
        cursor=cursor,
    )
    return VisitListResponse(visits=visits, next_cursor=next_cursor)


@router.get(
    "/visits/{visit_id}",
    dependencies=[Depends(require_permission("visit:read"))],
)
@response_message("Visit retrieved successfully")
async def get_visit(
    visit_id: str,
    response: Response,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
) -> dict[str, VisitResponse]:
    """Return one visit with its vital-signs history."""
    tenant_id = _require_tenant_id()
    visit = await get_visit_detail(registry, tenant_id, visit_id)
    set_etag(response, visit.version)
    return {"visit": visit}


@router.patch(
    "/visits/{visit_id}",
    dependencies=[Depends(require_permission("visit:update"))],
)
@response_message("Visit updated successfully")
async def patch_visit(
    visit_id: str,
    body: VisitUpdate,
    response: Response,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    if_match: Annotated[str | None, Header(alias="if-match")] = None,
) -> dict[str, VisitResponse]:
    """Update an open visit with an ``If-Match`` version check."""
    tenant_id = _require_tenant_id()
    expected_version = require_version(if_match)
    visit, clinician_name = await update_visit(
        registry,
        tenant_id,
        visit_id,
        body,
        expected_version,
    )
    set_etag(response, visit.version)
    return {
        "visit": VisitResponse.from_visit(visit, clinician_name=clinician_name),
    }


@router.post(
    "/visits/{visit_id}/close",
    dependencies=[Depends(require_permission("visit:close"))],
)
@response_message("Visit closed successfully")
async def close_patient_visit(
    visit_id: str,
    body: VisitClose,
    response: Response,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    if_match: Annotated[str | None, Header(alias="if-match")] = None,
) -> dict[str, VisitResponse]:
    """Close an open visit; the MVP has no unsigned drafts to block on."""
    del body  # ``force`` exists for the deferred draft workflow.
    tenant_id = _require_tenant_id()
    expected_version = require_version(if_match)
    visit, clinician_name = await close_visit(registry, tenant_id, visit_id, expected_version)
    set_etag(response, visit.version)
    return {"visit": VisitResponse.from_visit(visit, clinician_name=clinician_name)}


__all__ = ["router"]
