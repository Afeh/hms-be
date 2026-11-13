"""Public role shapes (plan §17)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.models.role import Role


class RoleResponse(BaseModel):
    """A tenant role and its permissions."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    key: str
    name: str
    permissions: list[str]
    is_system: bool
    mfa_required: bool

    @classmethod
    def from_role(cls, role: Role) -> RoleResponse:
        return cls(
            id=role.id or "",
            key=role.key,
            name=role.name,
            permissions=role.permissions,
            is_system=role.is_system,
            mfa_required=role.mfa_required,
        )


__all__ = ["RoleResponse"]
