"""Stored shape of a patient visit (plan §6.4, sprint S002)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.db.tenancy import TenantDocument

VisitStatus = Literal["open", "closed", "cancelled"]


class Visit(TenantDocument):
    """One encounter for a patient in a tenant."""

    patient_id: str
    type: str
    visit_date: datetime
    attending_clinician_id: str
    status: VisitStatus = "open"
    reason_for_visit: str | None = None
    closed_at: datetime | None = None
    closed_by: str | None = None


__all__ = ["Visit", "VisitStatus"]
