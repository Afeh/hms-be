"""Visit request and response shapes (plan §20, sprint S002)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.base import BaseSchema
from app.schemas.vitals import VitalsResponse

if TYPE_CHECKING:
    from app.models.visit import Visit

VisitStatus = Literal["open", "closed", "cancelled"]


class VisitCreate(BaseSchema):
    """Create a visit for a patient."""

    type: str = Field(min_length=1, max_length=50)
    visit_date: date = Field(default_factory=date.today)
    attending_clinician_id: str = Field(min_length=1)
    reason_for_visit: str | None = Field(default=None, max_length=500)

    @field_validator("type")
    @classmethod
    def _type_present(cls, value: str) -> str:
        visit_type = value.strip()
        if not visit_type:
            raise ValueError("visit type is required")
        return visit_type


class VisitUpdate(BaseSchema):
    """Editable visit fields; closing is a separate endpoint."""

    type: str | None = Field(default=None, min_length=1, max_length=50)
    visit_date: date | None = None
    attending_clinician_id: str | None = Field(default=None, min_length=1)
    reason_for_visit: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _has_a_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class VisitClose(BaseSchema):
    """Close a visit, optionally overriding unsigned drafts."""

    force: bool = False


class ClinicianRef(BaseModel):
    """The clinician shown on a visit response."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    name: str


class VisitResponse(BaseModel):
    """A visit with its attending clinician and, on detail reads, vitals."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    patient_id: str
    type: str
    visit_date: date
    status: VisitStatus
    attending_clinician: ClinicianRef
    reason_for_visit: str | None = None
    vitals: list[VitalsResponse] = Field(default_factory=list)
    closed_at: datetime | None = None
    closed_by: str | None = None
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_visit(
        cls,
        visit: Visit,
        *,
        clinician_name: str,
        vitals: list[VitalsResponse] | None = None,
    ) -> VisitResponse:
        return cls(
            id=visit.id or "",
            patient_id=visit.patient_id,
            type=visit.type,
            visit_date=visit.visit_date.date(),
            status=visit.status,
            attending_clinician=ClinicianRef(
                id=visit.attending_clinician_id,
                name=clinician_name,
            ),
            reason_for_visit=visit.reason_for_visit,
            vitals=vitals or [],
            closed_at=visit.closed_at,
            closed_by=visit.closed_by,
            version=visit.version,
            created_at=visit.created_at,
            updated_at=visit.updated_at,
        )


class VisitListResponse(BaseModel):
    """One cursor page of visits."""

    model_config = ConfigDict(extra="ignore")

    visits: list[VisitResponse]
    next_cursor: str | None = None


def visit_datetime(value: date) -> datetime:
    """Store a visit date as UTC midnight, matching the Mongo model."""
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


__all__ = [
    "ClinicianRef",
    "VisitClose",
    "VisitCreate",
    "VisitListResponse",
    "VisitResponse",
    "VisitStatus",
    "VisitUpdate",
    "visit_datetime",
]
