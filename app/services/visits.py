"""Visit creation, lookup and lifecycle (plan §20, sprint S002)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.context import get_context
from app.core.errors import ApiError
from app.db.client import TenantConnectionRegistry
from app.db.identities import IdentityRepository
from app.db.indexes import USERS_COLLECTION
from app.db.patients import PATIENTS_COLLECTION, PatientRepository
from app.db.tenant_configs import TenantConfigStore
from app.db.users import UserRepository
from app.db.visits import VISITS_COLLECTION, VisitRepository
from app.db.vital_signs import VITAL_SIGNS_COLLECTION, VitalSignsRepository
from app.models.visit import Visit
from app.schemas.visit import VisitCreate, VisitResponse, VisitUpdate, visit_datetime
from app.schemas.vitals import VitalsResponse
from app.services.patients import get_patient
from app.utils.pagination import InvalidCursorError


def _visits(registry: TenantConnectionRegistry, tenant_id: str) -> VisitRepository:
    return VisitRepository(registry.collection(tenant_id, VISITS_COLLECTION))


def _patients(registry: TenantConnectionRegistry, tenant_id: str) -> PatientRepository:
    return PatientRepository(registry.collection(tenant_id, PATIENTS_COLLECTION))


def _vitals(registry: TenantConnectionRegistry, tenant_id: str) -> VitalSignsRepository:
    return VitalSignsRepository(registry.collection(tenant_id, VITAL_SIGNS_COLLECTION))


async def _validate_visit_type(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    visit_type: str,
) -> str:
    config = await TenantConfigStore(registry).get_or_default(tenant_id)
    allowed = {item.key for item in config.visit_types if item.active and item.enabled}
    if visit_type not in allowed:
        raise ApiError(
            422,
            "unknown_visit_type",
            f"Visit type {visit_type!r} is not active in this hospital's configuration",
        )
    return visit_type


async def _clinician_name(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    clinician_id: str,
) -> str | None:
    user = await UserRepository(registry.collection(tenant_id, USERS_COLLECTION)).get(clinician_id)
    if user is None or user.status != "active":
        return None
    if user.display_name:
        return user.display_name
    identity = (
        None
        if user.identity_id is None
        else await IdentityRepository(registry).get(user.identity_id)
    )
    if identity is None:
        return "Clinician"
    given = " ".join(identity.name.given)
    return f"{given} {identity.name.family}".strip() or "Clinician"


async def _require_clinician(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    clinician_id: str,
) -> str:
    name = await _clinician_name(registry, tenant_id, clinician_id)
    if name is None:
        raise ApiError(
            422,
            "attending_not_clinician",
            "The attending clinician is not an active user in this hospital",
        )
    return name


async def _clinician_names(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    clinician_ids: set[str],
) -> dict[str, str]:
    names: dict[str, str] = {}
    for clinician_id in clinician_ids:
        names[clinician_id] = (
            await _clinician_name(registry, tenant_id, clinician_id) or "Unknown clinician"
        )
    return names


async def _get_visit(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    visit_id: str,
) -> Visit:
    visit = await _visits(registry, tenant_id).get(visit_id)
    if visit is None:
        raise ApiError(404, "not_found", "Visit not found")
    return visit


async def create_visit(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    patient_id: str,
    payload: VisitCreate,
) -> tuple[Visit, str]:
    """Create an open visit and advance the patient's last visit date."""
    patient = await get_patient(registry, tenant_id, patient_id)
    await _validate_visit_type(registry, tenant_id, payload.type)
    clinician_name = await _require_clinician(registry, tenant_id, payload.attending_clinician_id)

    visit = await _visits(registry, tenant_id).create(
        Visit(
            tenant_id=tenant_id,
            patient_id=patient.id or patient_id,
            type=payload.type,
            visit_date=visit_datetime(payload.visit_date),
            attending_clinician_id=payload.attending_clinician_id,
            reason_for_visit=payload.reason_for_visit,
        )
    )
    await _patients(registry, tenant_id).record_visit(patient.id or patient_id, visit.visit_date)
    return visit, clinician_name


async def list_visits_for_patient(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    patient_id: str,
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[VisitResponse], str | None]:
    await get_patient(registry, tenant_id, patient_id)
    try:
        visits, next_cursor = await _visits(registry, tenant_id).list_for_patient(
            patient_id,
            limit=limit,
            cursor=cursor,
        )
    except InvalidCursorError as exc:
        raise ApiError(400, "invalid_cursor", "The pagination cursor is invalid") from exc

    names = await _clinician_names(
        registry,
        tenant_id,
        {visit.attending_clinician_id for visit in visits},
    )
    return [
        VisitResponse.from_visit(
            visit,
            clinician_name=names.get(visit.attending_clinician_id, "Unknown clinician"),
        )
        for visit in visits
    ], next_cursor


async def get_visit_detail(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    visit_id: str,
) -> VisitResponse:
    visit = await _get_visit(registry, tenant_id, visit_id)
    name = (
        await _clinician_name(registry, tenant_id, visit.attending_clinician_id)
        or "Unknown clinician"
    )
    vitals = await _vitals(registry, tenant_id).list_for_visit(visit_id)
    return VisitResponse.from_visit(
        visit,
        clinician_name=name,
        vitals=[VitalsResponse.from_model(row) for row in vitals],
    )


async def update_visit(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    visit_id: str,
    payload: VisitUpdate,
    expected_version: int,
) -> tuple[Visit, str]:
    current = await _get_visit(registry, tenant_id, visit_id)
    if current.status != "open":
        raise ApiError(409, "visit_not_open", "Only an open visit can be edited")

    changes: dict[str, Any] = {}
    if payload.type is not None:
        changes["type"] = await _validate_visit_type(registry, tenant_id, payload.type)
    if payload.visit_date is not None:
        changes["visit_date"] = visit_datetime(payload.visit_date)
    if payload.attending_clinician_id is not None:
        changes["attending_clinician_id"] = payload.attending_clinician_id
        await _require_clinician(registry, tenant_id, payload.attending_clinician_id)
    if "reason_for_visit" in payload.model_fields_set:
        changes["reason_for_visit"] = payload.reason_for_visit

    updated = await _visits(registry, tenant_id).update_versioned(
        visit_id, changes, expected_version
    )
    name = (
        await _clinician_name(registry, tenant_id, updated.attending_clinician_id)
        or "Unknown clinician"
    )
    return updated, name


async def close_visit(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    visit_id: str,
    expected_version: int,
) -> tuple[Visit, str]:
    current = await _get_visit(registry, tenant_id, visit_id)
    if current.status != "open":
        raise ApiError(409, "visit_not_open", "This visit is not open")
    ctx = get_context()
    changed = await _visits(registry, tenant_id).update_versioned(
        visit_id,
        {
            "status": "closed",
            "closed_at": datetime.now(UTC),
            "closed_by": ctx.user_id,
        },
        expected_version,
    )
    name = (
        await _clinician_name(registry, tenant_id, changed.attending_clinician_id)
        or "Unknown clinician"
    )
    return changed, name


__all__ = [
    "close_visit",
    "create_visit",
    "get_visit_detail",
    "list_visits_for_patient",
    "update_visit",
]
