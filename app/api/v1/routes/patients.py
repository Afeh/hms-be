"""Patient registration, lookup and update (plan §19, sprint S001)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Depends, Header, Query, Response

from app.api.deps import get_registry, require_permission
from app.api.routing import ApiRouter, response_message
from app.core.context import get_context
from app.core.errors import ApiError
from app.db import TenantConnectionRegistry
from app.models.patient import PatientStatus
from app.schemas.patient import (
    PatientCreate,
    PatientListResponse,
    PatientResponse,
    PatientSort,
    PatientUpdate,
)
from app.services.patients import (
    get_patient,
    list_patients,
    patient_list_item,
    patient_response,
    register_patient,
    update_patient,
)
from app.utils.etag import require_version, set_etag

router = ApiRouter(prefix="/patients", tags=["patients"])


def _require_tenant_id() -> str:
    tenant_id = get_context().tenant_id
    if tenant_id is None:
        raise ApiError(401, "unauthenticated", "Authentication is required")
    return tenant_id


@router.post(
    "",
    status_code=201,
    dependencies=[Depends(require_permission("patient:create"))],
)
@response_message("Patient registered successfully")
async def create_patient(
    body: PatientCreate,
    response: Response,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
) -> dict[str, PatientResponse]:
    """Register a patient and allocate the tenant's next patient number."""
    tenant_id = _require_tenant_id()
    patient = await register_patient(body, registry, tenant_id)
    set_etag(response, patient.version)
    return {"patient": patient_response(patient)}


@router.get(
    "",
    dependencies=[Depends(require_permission("patient:read"))],
)
@response_message("Patients retrieved successfully")
async def get_patients(
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    status: PatientStatus | None = None,
    registered_from: date | None = None,
    registered_to: date | None = None,
    sort: PatientSort = "-registration_date",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> PatientListResponse:
    """List the caller tenant's patients with status/date filters."""
    tenant_id = _require_tenant_id()
    patients, next_cursor = await list_patients(
        registry,
        tenant_id,
        status=status,
        registered_from=registered_from,
        registered_to=registered_to,
        sort=sort,
        limit=limit,
        cursor=cursor,
    )
    return PatientListResponse(
        patients=[patient_list_item(patient) for patient in patients],
        next_cursor=next_cursor,
    )


@router.get(
    "/{patient_id}",
    dependencies=[Depends(require_permission("patient:read"))],
)
@response_message("Patient retrieved successfully")
async def get_patient_by_id(
    patient_id: str,
    response: Response,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
) -> dict[str, PatientResponse]:
    """Return one patient from the caller's tenant."""
    tenant_id = _require_tenant_id()
    patient = await get_patient(registry, tenant_id, patient_id)
    set_etag(response, patient.version)
    return {"patient": patient_response(patient)}


@router.patch(
    "/{patient_id}",
    dependencies=[Depends(require_permission("patient:update"))],
)
@response_message("Patient updated successfully")
async def patch_patient(
    patient_id: str,
    body: PatientUpdate,
    response: Response,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    if_match: Annotated[str | None, Header(alias="if-match")] = None,
) -> dict[str, PatientResponse]:
    """Update editable demographics with an ``If-Match`` version check."""
    tenant_id = _require_tenant_id()
    expected_version = require_version(if_match)
    patient = await update_patient(
        registry,
        tenant_id,
        patient_id,
        body,
        expected_version,
    )
    set_etag(response, patient.version)
    return {"patient": patient_response(patient)}


__all__ = ["router"]
