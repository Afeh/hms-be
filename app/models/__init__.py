"""MongoDB document models, subclassing `app.db.tenancy.TenantDocument`.

Response bodies belong in `app.schemas`; these are the stored shapes.
"""

from app.models.counter import Counter
from app.models.identity import Identity, MfaState, PersonName
from app.models.outbox import OutboxEvent
from app.models.patient import (
    NextOfKin,
    Patient,
    PatientAddress,
    PatientName,
    Payer,
)
from app.models.refresh_token import RefreshToken
from app.models.role import Role
from app.models.session import Session
from app.models.tenant import Tenant
from app.models.tenant_config import TenantConfig
from app.models.user import ProfessionalProfile, User

__all__ = [
    "Counter",
    "Identity",
    "MfaState",
    "NextOfKin",
    "OutboxEvent",
    "Patient",
    "PatientAddress",
    "PatientName",
    "Payer",
    "PersonName",
    "ProfessionalProfile",
    "RefreshToken",
    "Role",
    "Session",
    "Tenant",
    "TenantConfig",
    "User",
]
