"""Stored shape of a patient (plan §6.4, sprint S001)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.tenancy import TenantDocument

PatientStatus = Literal["active", "inactive", "deceased"]
PatientSex = Literal["male", "female", "other", "unknown"]


class PatientName(BaseModel):
    """A patient's name. ``given`` is always a list, even with one given name."""

    model_config = ConfigDict(extra="ignore")

    given: list[str] = Field(min_length=1)
    family: str
    other: str | None = None

    @field_validator("given", mode="before")
    @classmethod
    def _given_to_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [value]
        return value


class PatientAddress(BaseModel):
    """A patient's postal address."""

    model_config = ConfigDict(extra="ignore")

    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class NextOfKin(BaseModel):
    """One emergency or next-of-kin contact."""

    model_config = ConfigDict(extra="ignore")

    name: str
    relationship: str | None = None
    phone: str | None = None


class Payer(BaseModel):
    """The patient's payment arrangement."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    scheme: str | None = None
    member_number: str | None = None


class Patient(TenantDocument):
    """A patient registered in one tenant."""

    patient_number: str
    status: PatientStatus = "active"
    name: PatientName
    date_of_birth: datetime
    dob_approximate: bool = False
    sex: PatientSex = "unknown"
    phones: list[str] = Field(default_factory=list)
    address: PatientAddress | None = None
    next_of_kin: list[NextOfKin] = Field(default_factory=list)
    registration_date: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payer: Payer | None = None
    care_team: list[str] = Field(default_factory=list)
    legacy_number: str | None = None
    paper_file: bool = False
    display_name: str
    last_visit_date: datetime | None = None


__all__ = [
    "NextOfKin",
    "Patient",
    "PatientAddress",
    "PatientName",
    "PatientSex",
    "PatientStatus",
    "Payer",
]
