"""Pydantic models shared across modules: the response envelope and errors."""

from app.schemas.attachment import (
    AttachmentListResponse,
    AttachmentResponse,
    DownloadUrlSchema,
)
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LoginUser,
    LogoutResponse,
    MeResponse,
    MeUser,
    RefreshRequest,
    RefreshResponse,
)
from app.schemas.base import BaseSchema, StrictBaseModel
from app.schemas.envelope import ErrorDetail, error_envelope, success_envelope
from app.schemas.identity import IdentityResponse, NameSchema
from app.schemas.patient import (
    PatientCreate,
    PatientListItem,
    PatientListResponse,
    PatientResponse,
    PatientUpdate,
)
from app.schemas.role import RoleResponse
from app.schemas.session import SessionResponse
from app.schemas.tenant import TenantProfileResponse
from app.schemas.tenant_config import TenantConfigPatch, TenantConfigResponse
from app.schemas.user import UserResponse
from app.schemas.visit import (
    VisitClose,
    VisitCreate,
    VisitListResponse,
    VisitResponse,
    VisitUpdate,
)
from app.schemas.vitals import (
    VitalsCreate,
    VitalsEnteredInError,
    VitalsListResponse,
    VitalsResponse,
)

__all__ = [
    "AttachmentListResponse",
    "AttachmentResponse",
    "BaseSchema",
    "DownloadUrlSchema",
    "ErrorDetail",
    "IdentityResponse",
    "LoginRequest",
    "LoginResponse",
    "LoginUser",
    "LogoutResponse",
    "MeResponse",
    "MeUser",
    "NameSchema",
    "PatientCreate",
    "PatientListItem",
    "PatientListResponse",
    "PatientResponse",
    "PatientUpdate",
    "RefreshRequest",
    "RefreshResponse",
    "RoleResponse",
    "SessionResponse",
    "StrictBaseModel",
    "TenantConfigPatch",
    "TenantConfigResponse",
    "TenantProfileResponse",
    "UserResponse",
    "VisitClose",
    "VisitCreate",
    "VisitListResponse",
    "VisitResponse",
    "VisitUpdate",
    "VitalsCreate",
    "VitalsEnteredInError",
    "VitalsListResponse",
    "VitalsResponse",
    "error_envelope",
    "success_envelope",
]
