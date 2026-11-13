"""Response schemas for tenant config endpoints (plan §18, issue 0011)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.base import BaseSchema


class PatientIdFormatResponse(BaseModel):
    """Patient ID format settings."""

    model_config = ConfigDict(extra="ignore")

    prefix: str = "ACH"
    pattern: str = "{PREFIX}-{RANDOM:5}-{YYYY}"
    sequence_start: int = 1
    reset_sequence: str = "never"


class SecurityConfigResponse(BaseModel):
    """Security / session timeout settings."""

    model_config = ConfigDict(extra="ignore")

    idle_timeout_min: int = 30
    absolute_timeout_hours: int = 8
    password_min_length: int = 8
    break_glass_minutes: int = 15


class BrandingConfigResponse(BaseModel):
    """Branding overrides."""

    model_config = ConfigDict(extra="ignore")

    display_name: str | None = None
    logo_key: str | None = None
    primary_color: str | None = None
    primary_colour: str | None = None
    logo_url: str | None = None
    favicon_url: str | None = None
    support_email: str | None = None


class AiConfigResponse(BaseModel):
    """AI assistant settings."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    provider: str | None = None
    model: str | None = None
    model_name: str | None = None
    notice_version: str | None = None
    max_pages_per_document: int = 10
    monthly_page_cap: int | None = None


class ModulesConfigResponse(BaseModel):
    """Enabled feature modules."""

    model_config = ConfigDict(extra="ignore")

    ai_extraction: bool = False
    icd_coding: bool = False
    insurance: bool = False
    billing: bool = False
    pharmacy: bool = False
    laboratory: bool = False
    radiology: bool = False
    telemedicine: bool = False


class RetentionConfigResponse(BaseModel):
    """Data retention policy."""

    model_config = ConfigDict(extra="ignore")

    clinical_years: int = 15
    audit_years: int = 7
    document_years: int = 15
    audit_logs_days: int | None = None
    outbox_days: int = 30
    patient_records_years: int | None = None


class TenantConfigResponse(BaseModel):
    """The full effective configuration for a tenant.

    Returned by ``GET /tenant/config`` and ``PATCH /tenant/config``.
    Response models remain permissive (extra fields ignored).
    """

    model_config = ConfigDict(extra="ignore")

    tenant_id: str
    version: int
    branding: BrandingConfigResponse
    modules: ModulesConfigResponse
    patient_id_format: PatientIdFormatResponse
    document_types: list[dict[str, Any]]
    visit_types: list[dict[str, Any]]
    ai: AiConfigResponse
    security: SecurityConfigResponse
    retention: RetentionConfigResponse


class TenantConfigPatch(BaseSchema):
    """Partial patch body for ``PATCH /tenant/config``.

    All fields are optional; unknown top-level fields are rejected (extra=forbid
    inherited from BaseSchema). Sub-object patches deep-merge into the stored doc.
    """

    branding: dict[str, Any] | None = None
    modules: dict[str, Any] | None = None
    patient_id_format: dict[str, Any] | None = None
    document_types: list[dict[str, Any]] | None = None
    visit_types: list[dict[str, Any]] | None = None
    ai: dict[str, Any] | None = None
    security: dict[str, Any] | None = None
    retention: dict[str, Any] | None = None


__all__ = [
    "AiConfigResponse",
    "BrandingConfigResponse",
    "ModulesConfigResponse",
    "PatientIdFormatResponse",
    "RetentionConfigResponse",
    "SecurityConfigResponse",
    "TenantConfigPatch",
    "TenantConfigResponse",
]
