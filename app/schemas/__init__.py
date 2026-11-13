"""Pydantic models shared across modules: the response envelope and errors."""

from app.schemas.base import BaseSchema, StrictBaseModel
from app.schemas.envelope import ErrorDetail, error_envelope, success_envelope
from app.schemas.tenant import TenantProfileResponse
from app.schemas.tenant_config import TenantConfigPatch, TenantConfigResponse

__all__ = [
    "BaseSchema",
    "ErrorDetail",
    "StrictBaseModel",
    "TenantConfigPatch",
    "TenantConfigResponse",
    "TenantProfileResponse",
    "error_envelope",
    "success_envelope",
]
