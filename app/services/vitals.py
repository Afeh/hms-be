"""Vital-signs recording and corrections (plan §20, sprint S002)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.context import get_context
from app.core.errors import ApiError, VersionConflictError
from app.db.client import TenantConnectionRegistry
from app.db.visits import VISITS_COLLECTION, VisitRepository
from app.db.vital_signs import VITAL_SIGNS_COLLECTION, VitalSignsRepository
from app.models.vital_signs import VitalSigns, VitalWarning
from app.schemas.envelope import ErrorDetail
from app.schemas.vitals import VitalsCreate, VitalsResponse
from app.services.patients import get_patient
from app.utils.pagination import InvalidCursorError

_MEASUREMENT_FIELDS = (
    "temperature_c",
    "pulse_bpm",
    "systolic_mmhg",
    "diastolic_mmhg",
    "weight_kg",
    "height_cm",
)


@dataclass(frozen=True, slots=True)
class VitalRange:
    """Allowed and expected bounds for one measurement."""

    minimum: float
    maximum: float
    expected_minimum: float
    expected_maximum: float
    exclusive_minimum: bool = False

    def impossible(self, value: float) -> bool:
        below = value <= self.minimum if self.exclusive_minimum else value < self.minimum
        return below or value > self.maximum

    def unusual_low(self, value: float) -> bool:
        return value < self.expected_minimum

    def unusual_high(self, value: float) -> bool:
        return value > self.expected_maximum


VITAL_RANGES: dict[str, VitalRange] = {
    "temperature_c": VitalRange(25, 48, 34, 41),
    "pulse_bpm": VitalRange(10, 300, 40, 180),
    "systolic_mmhg": VitalRange(40, 300, 70, 200),
    "diastolic_mmhg": VitalRange(20, 200, 40, 130),
    "weight_kg": VitalRange(0, 500, 2, 300, exclusive_minimum=True),
    "height_cm": VitalRange(0, 300, 30, 250, exclusive_minimum=True),
}


def _vital_repository(registry: TenantConnectionRegistry, tenant_id: str) -> VitalSignsRepository:
    return VitalSignsRepository(registry.collection(tenant_id, VITAL_SIGNS_COLLECTION))


def _visit_repository(registry: TenantConnectionRegistry, tenant_id: str) -> VisitRepository:
    return VisitRepository(registry.collection(tenant_id, VISITS_COLLECTION))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _warning(field: str, value: float, message: str) -> dict[str, object]:
    return {"field": field, "value": value, "message": message}


def validate_measurements(
    payload: VitalsCreate,
) -> tuple[list[ErrorDetail], list[dict[str, object]]]:
    """Return impossible-value errors and plausible-but-unusual warnings."""
    errors: list[ErrorDetail] = []
    warnings: list[dict[str, object]] = []

    for field in _MEASUREMENT_FIELDS:
        value = getattr(payload, field)
        if value is None:
            continue
        numeric = float(value)
        bounds = VITAL_RANGES[field]
        if bounds.impossible(numeric):
            errors.append(
                ErrorDetail(
                    field=field,
                    message=(
                        f"Value must be between {bounds.minimum:g} and "
                        f"{bounds.maximum:g} for {field}."
                    ),
                )
            )
            continue
        if bounds.unusual_low(numeric):
            warnings.append(
                _warning(
                    field,
                    numeric,
                    f"Below expected range ({bounds.expected_minimum:.1f} to "
                    f"{bounds.expected_maximum:.1f}).",
                )
            )
        elif bounds.unusual_high(numeric):
            warnings.append(
                _warning(
                    field,
                    numeric,
                    f"Above expected range ({bounds.expected_minimum:.1f} to "
                    f"{bounds.expected_maximum:.1f}).",
                )
            )
    return errors, warnings


def _computed_bmi(weight_kg: float, height_cm: float) -> float:
    height_m = height_cm / 100
    return round(weight_kg / (height_m * height_m), 1)


async def create_vitals(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    visit_id: str,
    payload: VitalsCreate,
) -> VitalSigns:
    """Validate and store one set of measurements for an open visit."""
    visit = await _visit_repository(registry, tenant_id).get(visit_id)
    if visit is None:
        raise ApiError(404, "not_found", "Visit not found")
    if visit.status != "open":
        raise ApiError(409, "visit_not_open", "Vitals can only be recorded on an open visit")

    errors, warnings = validate_measurements(payload)
    if errors:
        raise ApiError(
            422,
            "vitals_invalid",
            "Vital signs are outside physically possible ranges",
            errors=errors,
        )
    if warnings and not payload.acknowledge_warnings:
        raise ApiError(
            409,
            "vitals_confirmation_required",
            "Please confirm unusual values",
            data={"warnings": warnings},
        )

    repo = _vital_repository(registry, tenant_id)
    height = payload.height_cm
    if payload.weight_kg is not None and height is None:
        height = await repo.latest_height(visit.patient_id)
    bmi = (
        _computed_bmi(payload.weight_kg, height)
        if payload.weight_kg is not None and height is not None
        else None
    )

    ctx = get_context()
    return await repo.create(
        VitalSigns(
            tenant_id=tenant_id,
            patient_id=visit.patient_id,
            visit_id=visit.id or visit_id,
            measured_at=_as_utc(payload.measured_at),
            recorded_by=ctx.user_id,
            temperature_c=payload.temperature_c,
            pulse_bpm=payload.pulse_bpm,
            systolic_mmhg=payload.systolic_mmhg,
            diastolic_mmhg=payload.diastolic_mmhg,
            weight_kg=payload.weight_kg,
            height_cm=height,
            bmi=bmi,
            warnings=[VitalWarning(**warning) for warning in warnings],
        )
    )


async def list_vitals_for_patient(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    patient_id: str,
    *,
    visit_id: str | None,
    measured_from: datetime | None,
    measured_to: datetime | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[VitalSigns], str | None]:
    await get_patient(registry, tenant_id, patient_id)
    try:
        return await _vital_repository(registry, tenant_id).list_for_patient(
            patient_id,
            visit_id=visit_id,
            measured_from=None if measured_from is None else _as_utc(measured_from),
            measured_to=None if measured_to is None else _as_utc(measured_to),
            limit=limit,
            cursor=cursor,
        )
    except InvalidCursorError as exc:
        raise ApiError(400, "invalid_cursor", "The pagination cursor is invalid") from exc


async def mark_vitals_entered_in_error(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    vitals_id: str,
    reason: str,
) -> VitalSigns:
    """Retain the record in history with an explicit correction reason."""
    repo = _vital_repository(registry, tenant_id)
    current = await repo.get(vitals_id)
    if current is None:
        raise ApiError(404, "not_found", "Vital signs not found")
    if current.status == "entered_in_error":
        raise ApiError(409, "already_entered_in_error", "Vital signs are already entered in error")
    try:
        return await repo.update_versioned(
            vitals_id,
            {
                "status": "entered_in_error",
                "entered_in_error_reason": reason,
            },
            current.version,
        )
    except VersionConflictError as exc:
        raise ApiError(
            409,
            "conflict",
            "Vital signs changed while the correction was being applied",
        ) from exc


def vitals_response(vitals: VitalSigns) -> VitalsResponse:
    return VitalsResponse.from_model(vitals)


__all__ = [
    "VITAL_RANGES",
    "create_vitals",
    "list_vitals_for_patient",
    "mark_vitals_entered_in_error",
    "validate_measurements",
    "vitals_response",
]
