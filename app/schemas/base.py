"""Strict base schema for request models (plan section 12, issue 0006).

`extra="forbid"` ensures unknown fields are rejected with 422 rather than silently ignored.
`str_strip_whitespace=True` trims string inputs automatically. Response models remain permissive.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    """Base for request schemas with strict validation."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class StrictBaseModel(BaseSchema):
    """Alias for BaseSchema."""


__all__ = ["BaseSchema", "StrictBaseModel"]
