"""Pydantic models shared across modules: the response envelope and errors."""

from app.schemas.base import BaseSchema, StrictBaseModel
from app.schemas.envelope import ErrorDetail, error_envelope, success_envelope
from app.schemas.identity import IdentityResponse, NameSchema
from app.schemas.role import RoleResponse
from app.schemas.session import SessionResponse
from app.schemas.tenant import TenantProfileResponse
from app.schemas.tenant_config import TenantConfigPatch, TenantConfigResponse
from app.schemas.user import UserResponse

__all__ = [
    "BaseSchema",
    "ErrorDetail",
    "IdentityResponse",
    "NameSchema",
    "RoleResponse",
    "SessionResponse",
    "StrictBaseModel",
    "TenantConfigPatch",
    "TenantConfigResponse",
    "TenantProfileResponse",
    "UserResponse",
    "error_envelope",
    "success_envelope",
]
