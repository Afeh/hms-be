"""Vital-sign request and response shapes (plan §20, sprint S002)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.base import BaseSchema

if TYPE_CHECKING:
    from app.models.vital_signs import VitalSigns

VitalSignsStatus = Literal["active", "entered_in_error"]


class VitalWarningSchema(BaseModel):
    """A value outside the usual clinical range, stored after acknowledgement."""

    model_config = ConfigDict(extra="ignore")

    field: str
    value: float
    message: str


class VitalsCreate(BaseSchema):
    """Measurements recorded against one visit."""

    measured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    temperature_c: float | None = None
    pulse_bpm: int | None = None
    systolic_mmhg: int | None = None
    diastolic_mmhg: int | None = None
    weight_kg: float | None = None
    height_cm: float | None = None
    acknowledge_warnings: bool = False

    @model_validator(mode="after")
    def _one_measurement(self) -> Self:
        measurements = (
            self.temperature_c,
            self.pulse_bpm,
            self.systolic_mmhg,
            self.diastolic_mmhg,
            self.weight_kg,
            self.height_cm,
        )
        if all(value is None for value in measurements):
            raise ValueError("at least one vital sign is required")
        return self


class VitalsEnteredInError(BaseSchema):
    """Mark a vital-signs row as recorded in error."""

    status: Literal["entered_in_error"]
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason_present(cls, value: str) -> str:
        reason = value.strip()
        if not reason:
            raise ValueError("reason is required")
        return reason


class VitalsResponse(BaseModel):
    """One vital-signs record returned by the API."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    patient_id: str
    visit_id: str
    measured_at: datetime
    recorded_by: str | None = None
    temperature_c: float | None = None
    pulse_bpm: int | None = None
    systolic_mmhg: int | None = None
    diastolic_mmhg: int | None = None
    weight_kg: float | None = None
    height_cm: float | None = None
    bmi: float | None = None
    warnings: list[VitalWarningSchema]
    status: VitalSignsStatus
    entered_in_error_reason: str | None = None
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_model(cls, vitals: VitalSigns) -> VitalsResponse:
        return cls(
            id=vitals.id or "",
            patient_id=vitals.patient_id,
            visit_id=vitals.visit_id,
            measured_at=vitals.measured_at,
            recorded_by=vitals.recorded_by,
            temperature_c=vitals.temperature_c,
            pulse_bpm=vitals.pulse_bpm,
            systolic_mmhg=vitals.systolic_mmhg,
            diastolic_mmhg=vitals.diastolic_mmhg,
            weight_kg=vitals.weight_kg,
            height_cm=vitals.height_cm,
            bmi=vitals.bmi,
            warnings=[
                VitalWarningSchema(
                    field=warning.field,
                    value=warning.value,
                    message=warning.message,
                )
                for warning in vitals.warnings
            ],
            status=vitals.status,
            entered_in_error_reason=vitals.entered_in_error_reason,
            version=vitals.version,
            created_at=vitals.created_at,
            updated_at=vitals.updated_at,
        )


class VitalsListResponse(BaseModel):
    """One cursor page of vital-signs records."""

    model_config = ConfigDict(extra="ignore")

    vitals: list[VitalsResponse]
    next_cursor: str | None = None


__all__ = [
    "VitalSignsStatus",
    "VitalWarningSchema",
    "VitalsCreate",
    "VitalsEnteredInError",
    "VitalsListResponse",
    "VitalsResponse",
]
