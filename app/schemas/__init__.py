"""Pydantic models shared across modules: the response envelope and errors."""

from app.schemas.envelope import ErrorDetail, error_envelope, success_envelope

__all__ = ["ErrorDetail", "error_envelope", "success_envelope"]
