"""Typed configuration read from environment variables (and `.env` locally)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
