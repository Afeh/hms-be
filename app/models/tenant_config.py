"""Tenant configuration document model (plan §6.2 TenantConfig, issue 0011).

One document per tenant in the ``tenant_configs`` collection.
All fields carry defaults so ``GET /tenant/config`` works for a freshly
seeded tenant that has no config document yet.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.tenancy import TenantDocument

# ---------------------------------------------------------------------------
# Patient ID format
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{(PREFIX|TENANT|YYYY|YY|(?:SEQ|RAND|RANDOM):\d+)\}")
_VALID_TOKENS = {"PREFIX", "TENANT", "YYYY", "YY"}


def _validate_patient_id_pattern(pattern: str) -> str:
    """Check the pattern uses only known placeholder tokens."""
    for ph in re.findall(r"\{([^}]+)\}", pattern):
        if ph in _VALID_TOKENS:
            continue
        if re.fullmatch(r"(?:SEQ|RAND|RANDOM):\d+", ph):
            continue
        raise ValueError(
            f"unknown placeholder {{{ph!r}}} in patient_id_format.pattern; "
            "valid tokens are {PREFIX}, {TENANT}, {YYYY}, {YY}, {RANDOM:<n>}, {RAND:<n>}, {SEQ:<n>}"
        )
    return pattern


class PatientIdFormat(BaseModel):
    """How patient IDs are generated for this tenant.

    Default format: XXX-random-digits-year, e.g. ACH-48291-2026.
    Hospital code XXX (prefix) is chosen when setting up the hospital tenant.
    """

    model_config = ConfigDict(extra="ignore")

    prefix: str = "ACH"
    pattern: str = "{PREFIX}-{RANDOM:5}-{YYYY}"  # e.g. ACH-48291-2026
    sequence_start: int = 1
    reset_sequence: Literal["never", "yearly"] = "never"

    @field_validator("prefix")
    @classmethod
    def _check_prefix(cls, v: str) -> str:
        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,6}", v):
            raise ValueError(
                "patient_id_format.prefix must be 2 to 6 uppercase alphanumeric characters "
                "(e.g. 'ACH', 'BSC')"
            )
        return v

    @field_validator("pattern")
    @classmethod
    def _check_pattern(cls, v: str) -> str:
        return _validate_patient_id_pattern(v)


def generate_patient_id(
    format_config: PatientIdFormat,
    *,
    tenant_slug: str = "",
    registration_year: int | None = None,
    sequence: int | None = None,
) -> str:
    """Generate a formatted patient ID based on tenant configuration.

    Grammar placeholders:
      {PREFIX}    - Tenant hospital code (e.g. 'ACH', 'BSC')
      {TENANT}    - Tenant slug (upper-cased)
      {YYYY}      - 4-digit registration year (e.g. '2026')
      {YY}        - 2-digit registration year (e.g. '26')
      {RANDOM:n}  - n cryptographically secure random digits (e.g. RANDOM:5 -> '48291')
      {RAND:n}    - Alias for RANDOM:n
      {SEQ:n}     - n-digit zero-padded sequential counter (e.g. SEQ:6 -> '000001')
    """
    year = registration_year if registration_year is not None else datetime.now(UTC).year

    def _replace_token(match: re.Match[str]) -> str:
        ph = match.group(1)
        if ph == "PREFIX":
            return format_config.prefix
        if ph == "TENANT":
            return tenant_slug.upper()
        if ph == "YYYY":
            return f"{year:04d}"
        if ph == "YY":
            return f"{year % 100:02d}"

        if rand_match := re.fullmatch(r"(?:RANDOM|RAND):(\d+)", ph):
            length = int(rand_match.group(1))
            return "".join(secrets.choice("0123456789") for _ in range(length))

        if seq_match := re.fullmatch(r"SEQ:(\d+)", ph):
            width = int(seq_match.group(1))
            seq_val = sequence if sequence is not None else format_config.sequence_start
            return f"{seq_val:0{width}d}"

        return match.group(0)

    return re.sub(r"\{([^}]+)\}", _replace_token, format_config.pattern)


# ---------------------------------------------------------------------------
# Security block
# ---------------------------------------------------------------------------

_MAX_IDLE_TIMEOUT_MIN = 480  # 8 hours
_MAX_ABSOLUTE_TIMEOUT_HOURS = 24
_MAX_BREAK_GLASS_MINUTES = 60


class SecurityConfig(BaseModel):
    """Authentication and session security settings."""

    model_config = ConfigDict(extra="ignore")

    idle_timeout_min: int = 30
    absolute_timeout_hours: int = 8
    password_min_length: int = 8
    break_glass_minutes: int = 15

    @field_validator("idle_timeout_min")
    @classmethod
    def _idle_positive(cls, v: int) -> int:
        if v <= 0 or v > _MAX_IDLE_TIMEOUT_MIN:
            raise ValueError(f"idle_timeout_min must be between 1 and {_MAX_IDLE_TIMEOUT_MIN}")
        return v

    @field_validator("absolute_timeout_hours")
    @classmethod
    def _absolute_positive(cls, v: int) -> int:
        if v <= 0 or v > _MAX_ABSOLUTE_TIMEOUT_HOURS:
            raise ValueError(
                f"absolute_timeout_hours must be between 1 and {_MAX_ABSOLUTE_TIMEOUT_HOURS}"
            )
        return v

    @field_validator("break_glass_minutes")
    @classmethod
    def _break_glass_positive(cls, v: int) -> int:
        if v <= 0 or v > _MAX_BREAK_GLASS_MINUTES:
            raise ValueError(
                f"break_glass_minutes must be between 1 and {_MAX_BREAK_GLASS_MINUTES}"
            )
        return v

    @field_validator("password_min_length")
    @classmethod
    def _pwd_len(cls, v: int) -> int:
        if v < 6 or v > 128:
            raise ValueError("password_min_length must be between 6 and 128")
        return v


# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------


class BrandingConfig(BaseModel):
    """UI branding overrides."""

    model_config = ConfigDict(extra="ignore")

    display_name: str | None = None
    logo_key: str | None = None
    primary_color: str | None = None
    primary_colour: str | None = None
    logo_url: str | None = None
    favicon_url: str | None = None
    support_email: str | None = None


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------

AiProvider = Literal["openai", "anthropic", "gemini"]


class AiConfig(BaseModel):
    """AI assistant settings."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    provider: AiProvider | None = None
    model: str | None = None
    model_name: str | None = None
    notice_version: str | None = None
    max_pages_per_document: int = 10
    monthly_page_cap: int | None = None

    @model_validator(mode="after")
    def _enabled_requires_provider_and_model(self) -> AiConfig:
        model = self.model or self.model_name
        if self.enabled and (self.provider is None or model is None):
            raise ValueError("ai.provider and ai.model are required when ai.enabled is true")
        return self


# ---------------------------------------------------------------------------
# Document / visit types
# ---------------------------------------------------------------------------


class DocumentTypeConfig(BaseModel):
    """A configurable document type (e.g. discharge summary, referral letter)."""

    model_config = ConfigDict(extra="ignore")

    key: str  # unique slug, immutable after creation
    label: str
    default_handwritten: bool = False
    active: bool = True
    enabled: bool = True


class VisitTypeConfig(BaseModel):
    """A configurable visit type (e.g. inpatient, outpatient, emergency)."""

    model_config = ConfigDict(extra="ignore")

    key: str  # unique slug
    label: str
    active: bool = True
    enabled: bool = True


# ---------------------------------------------------------------------------
# Modules
# ---------------------------------------------------------------------------


class ModulesConfig(BaseModel):
    """Optional feature modules that can be enabled per tenant."""

    model_config = ConfigDict(extra="ignore")

    ai_extraction: bool = False
    icd_coding: bool = False
    insurance: bool = False
    billing: bool = False
    pharmacy: bool = False
    laboratory: bool = False
    radiology: bool = False
    telemedicine: bool = False


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


class RetentionConfig(BaseModel):
    """Data retention policy (durations in days or years; None means keep forever)."""

    model_config = ConfigDict(extra="ignore")

    clinical_years: int = 15
    audit_years: int = 7
    document_years: int = 15
    audit_logs_days: int | None = None
    outbox_days: int = 30
    patient_records_years: int | None = None


# ---------------------------------------------------------------------------
# Top-level config document
# ---------------------------------------------------------------------------


class TenantConfig(TenantDocument):
    """One configuration document per tenant.

    Stored in the ``tenant_configs`` collection (a TenantCollection so the
    tenant guard keeps data isolated).  Defaults are valid with no document
    present, so ``get_or_default`` can synthesise a live config from the model
    class without touching the database.
    """

    branding: BrandingConfig = Field(default_factory=BrandingConfig)
    modules: ModulesConfig = Field(default_factory=ModulesConfig)
    patient_id_format: PatientIdFormat = Field(default_factory=PatientIdFormat)
    document_types: list[DocumentTypeConfig] = Field(default_factory=list)
    visit_types: list[VisitTypeConfig] = Field(default_factory=list)
    ai: AiConfig = Field(default_factory=AiConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)

    @field_validator("document_types")
    @classmethod
    def _unique_doc_keys(cls, v: list[DocumentTypeConfig]) -> list[DocumentTypeConfig]:
        keys = [dt.key for dt in v]
        if len(keys) != len(set(keys)):
            raise ValueError("document_types keys must be unique")
        return v

    @field_validator("visit_types")
    @classmethod
    def _unique_visit_keys(cls, v: list[VisitTypeConfig]) -> list[VisitTypeConfig]:
        keys = [vt.key for vt in v]
        if len(keys) != len(set(keys)):
            raise ValueError("visit_types keys must be unique")
        return v


def default_config(tenant_id: str) -> TenantConfig:
    """Return a fresh default config for a tenant, without touching the database."""
    return TenantConfig(tenant_id=tenant_id)


__all__ = [
    "AiConfig",
    "AiProvider",
    "BrandingConfig",
    "DocumentTypeConfig",
    "ModulesConfig",
    "PatientIdFormat",
    "RetentionConfig",
    "SecurityConfig",
    "TenantConfig",
    "VisitTypeConfig",
    "default_config",
    "generate_patient_id",
]
