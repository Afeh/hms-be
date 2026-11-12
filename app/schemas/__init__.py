"""Pydantic models shared across modules: the response envelope and errors."""

from app.schemas.base import BaseSchema, StrictBaseModel
from app.schemas.envelope import ErrorDetail, error_envelope, success_envelope

__all__ = ["BaseSchema", "ErrorDetail", "StrictBaseModel", "error_envelope", "success_envelope"]
