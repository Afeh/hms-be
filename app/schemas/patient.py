"""Patient request and response shapes (plan §19, sprint S001)."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.base import BaseSchema

if TYPE_CHECKING:
    from app.models.patient import Patient

PatientSort = Literal["registration_date", "-registration_date", "name", "-name"]
PatientSex = Literal["male", "female", "other", "unknown"]
PatientStatus = Literal["active", "inactive", "deceased"]


class PatientNameSchema(BaseModel):
    """A patient's name in requests and responses."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    given: list[str] = Field(min_length=1, max_length=5)
    family: str = Field(min_length=1, max_length=100)
    other: str | None = Field(default=None, max_length=100)

    @field_validator("given")
    @classmethod
    def _given_names_present(cls, value: list[str]) -> list[str]:
        names = [name.strip() for name in value if name.strip()]
        if not names:
            raise ValueError("at least one given name is required")
        return names


class PatientAddressSchema(BaseModel):
    """A patient's address."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    line1: str | None = Field(default=None, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=30)
    country: str | None = Field(default=None, max_length=2)


class NextOfKinSchema(BaseModel):
    """A next-of-kin contact."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=150)
    relationship: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=30)


class PayerSchema(BaseModel):
    """A patient's payer details."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str | None = Field(default=None, max_length=150)
    scheme: str | None = Field(default=None, max_length=100)
    member_number: str | None = Field(default=None, max_length=100)


class PatientCreate(BaseSchema):
    """Registration body for ``POST /patients``."""

    name: PatientNameSchema
    date_of_birth: date
    dob_approximate: bool = False
    sex: PatientSex = "unknown"
    phones: list[str] = Field(default_factory=list, max_length=5)
    address: PatientAddressSchema | None = None
    next_of_kin: list[NextOfKinSchema] = Field(default_factory=list, max_length=5)
    payer: PayerSchema | None = None
    legacy_number: str | None = Field(default=None, max_length=100)
    paper_file: bool = False

    @field_validator("phones")
    @classmethod
    def _phones_present(cls, value: list[str]) -> list[str]:
        phones = [phone.strip() for phone in value if phone.strip()]
        if any(len(phone) < 7 for phone in phones):
            raise ValueError("phone numbers must contain at least 7 characters")
        return phones

    @field_validator("date_of_birth")
    @classmethod
    def _date_not_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("date_of_birth cannot be in the future")
        return value


class PatientUpdate(BaseSchema):
    """Editable patient fields for ``PATCH /patients/:id``."""

    name: PatientNameSchema | None = None
    phones: list[str] | None = Field(default=None, max_length=5)
    address: PatientAddressSchema | None = None
    next_of_kin: list[NextOfKinSchema] | None = Field(default=None, max_length=5)
    payer: PayerSchema | None = None
    paper_file: bool | None = None

    @field_validator("phones")
    @classmethod
    def _phones_valid(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        phones = [phone.strip() for phone in value if phone.strip()]
        if any(len(phone) < 7 for phone in phones):
            raise ValueError("phone numbers must contain at least 7 characters")
        return phones

    @model_validator(mode="after")
    def _has_a_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class PatientResponse(BaseModel):
    """A full patient returned by create, get and update."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    tenant_id: str
    patient_number: str
    status: PatientStatus
    name: PatientNameSchema
    date_of_birth: date
    dob_approximate: bool
    sex: PatientSex
    phones: list[str]
    address: PatientAddressSchema | None = None
    next_of_kin: list[NextOfKinSchema]
    registration_date: datetime
    payer: PayerSchema | None = None
    care_team: list[str]
    legacy_number: str | None = None
    paper_file: bool
    display_name: str
    age_years: int
    last_visit_date: datetime | None = None
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_patient(cls, patient: Patient, age_years: int) -> PatientResponse:
        return cls(
            id=patient.id or "",
            tenant_id=patient.tenant_id,
            patient_number=patient.patient_number,
            status=patient.status,
            name=PatientNameSchema(
                given=patient.name.given,
                family=patient.name.family,
                other=patient.name.other,
            ),
            date_of_birth=patient.date_of_birth.date(),
            dob_approximate=patient.dob_approximate,
            sex=patient.sex,
            phones=patient.phones,
            address=(
                None
                if patient.address is None
                else PatientAddressSchema(**patient.address.model_dump())
            ),
            next_of_kin=[
                NextOfKinSchema(**contact.model_dump()) for contact in patient.next_of_kin
            ],
            registration_date=patient.registration_date,
            payer=None if patient.payer is None else PayerSchema(**patient.payer.model_dump()),
            care_team=patient.care_team,
            legacy_number=patient.legacy_number,
            paper_file=patient.paper_file,
            display_name=patient.display_name,
            age_years=age_years,
            last_visit_date=patient.last_visit_date,
            version=patient.version,
            created_at=patient.created_at,
            updated_at=patient.updated_at,
        )


class PatientListItem(BaseModel):
    """The compact patient shape returned by the list endpoint."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    patient_number: str
    display_name: str
    date_of_birth: date
    sex: PatientSex
    status: PatientStatus
    phones: list[str]
    registration_date: datetime
    last_visit_date: datetime | None = None
    version: int

    @classmethod
    def from_patient(cls, patient: Patient) -> PatientListItem:
        return cls(
            id=patient.id or "",
            patient_number=patient.patient_number,
            display_name=patient.display_name,
            date_of_birth=patient.date_of_birth.date(),
            sex=patient.sex,
            status=patient.status,
            phones=patient.phones,
            registration_date=patient.registration_date,
            last_visit_date=patient.last_visit_date,
            version=patient.version,
        )


class PatientListResponse(BaseModel):
    """One cursor page of patients."""

    model_config = ConfigDict(extra="ignore")

    patients: list[PatientListItem]
    next_cursor: str | None = None


__all__ = [
    "NextOfKinSchema",
    "PatientAddressSchema",
    "PatientCreate",
    "PatientListItem",
    "PatientListResponse",
    "PatientNameSchema",
    "PatientResponse",
    "PatientSex",
    "PatientSort",
    "PatientStatus",
    "PatientUpdate",
    "PayerSchema",
]
