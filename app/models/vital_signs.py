"""Stored shape of a vital-signs observation (plan §6.4, sprint S002)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.tenancy import TenantDocument

VitalSignsStatus = Literal["active", "entered_in_error"]


class VitalWarning(BaseModel):
    """One plausible-but-unusual value the clinician acknowledged."""

    model_config = ConfigDict(extra="ignore")

    field: str
    value: float
    message: str


class VitalSigns(TenantDocument):
    """One set of measurements for a patient visit."""

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
    warnings: list[VitalWarning] = Field(default_factory=list)
    status: VitalSignsStatus = "active"
    entered_in_error_reason: str | None = None


__all__ = ["VitalSigns", "VitalSignsStatus", "VitalWarning"]
