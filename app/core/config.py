"""Typed configuration read from environment variables (and `.env` locally)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment schema.

    Field names map to upper-case variables: `mongodb_uri` is `MONGODB_URI`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        enable_decoding=False,
    )

    app_name: str = "HMS API"
    environment: Literal["local", "staging", "production"] = "local"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    api_host: str = "0.0.0.0"  # noqa: S104 - binds all interfaces inside the container
    api_port: int = 8000

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "hms"
    mongodb_server_selection_timeout_ms: int = 5000

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    cors_origins: list[str] = Field(default_factory=list)

    # Authentication (plan section 5.1, issues 0013/0017). The default is local
    # development only; staging and production must supply a long random secret.
    jwt_secret: str = "dev-only-insecure-jwt-secret-change-me-0001"  # noqa: S105
    jwt_issuer: str = "hms"
    jwt_audience: str = "hms-api"
    access_token_ttl_seconds: int = 600
    refresh_token_ttl_seconds: int = 30 * 24 * 60 * 60
    session_idle_timeout_seconds: int = 30 * 60
    session_absolute_timeout_seconds: int = 8 * 60 * 60
    mfa_secret_key: str = "dev-only-insecure-mfa-key-change-me-0001"  # noqa: S105
    mfa_challenge_ttl_seconds: int = 300

    # Browser session cookies. Tokens are also returned in the login body for
    # non-browser clients. Secure defaults to off locally and on everywhere else.
    auth_cookie_secure: bool | None = None
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    auth_cookie_domain: str | None = None

    @property
    def secure_auth_cookies(self) -> bool:
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.environment != "local"

    # HTTP hardening and rate limits (plan section 12, issue 0006)
    max_request_body_bytes: int = 1_048_576  # 1 MiB for JSON payloads
    rate_limit_window_seconds: int = 60
    rate_limit_login_ip: int = 10
    rate_limit_login_email: int = 5
    rate_limit_mfa: int = 5
    rate_limit_password_reset: int = 5
    rate_limit_invitation: int = 10
    rate_limit_signup: int = 5

    # Cloudflare R2 / S3-compatible object storage. Empty credentials keep the
    # API bootable; attachment endpoints return 503 until they are configured.
    storage_endpoint: str | None = None
    storage_bucket: str | None = None
    storage_access_key: str | None = None
    storage_secret_key: str | None = None
    storage_region: str = "auto"
    storage_presign_ttl_seconds: int = 300
    storage_max_image_bytes: int = 10 * 1024 * 1024

    @property
    def storage_configured(self) -> bool:
        return all(
            (
                self.storage_endpoint,
                self.storage_bucket,
                self.storage_access_key,
                self.storage_secret_key,
            )
        )

    @property
    def broker_url(self) -> str:
        """Celery broker, defaulting to the Redis instance already configured."""
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend_url(self) -> str:
        """Celery result backend, defaulting to the same Redis instance."""
        return self.celery_result_backend or self.redis_url

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept `CORS_ORIGINS=https://a.example,https://b.example` as well as JSON."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _validate_cookie_settings(self) -> Settings:
        """Reject a SameSite=None cookie that browsers would discard without HTTPS."""
        if self.auth_cookie_samesite == "none" and not self.secure_auth_cookies:
            raise ValueError("AUTH_COOKIE_SAMESITE=none requires secure auth cookies")
        return self

    @model_validator(mode="after")
    def _require_production_secrets(self) -> Settings:
        """Refuse to start non-locally with the development signing keys."""
        if self.environment == "local":
            return self
        secrets = {"JWT_SECRET": self.jwt_secret, "MFA_SECRET_KEY": self.mfa_secret_key}
        for name, value in secrets.items():
            if len(value) < 32 or "insecure" in value or "change-me" in value:
                raise ValueError(f"{name} must be a random value of at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
