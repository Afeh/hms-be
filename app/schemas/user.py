"""Public membership shapes (plan §17)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.models.user import User


class ProfessionalProfileResponse(BaseModel):
    """Professional details in a staff response."""

    model_config = ConfigDict(extra="ignore")

    license_no: str | None = None
    cadre: str | None = None
    signature_image_key: str | None = None


class UserResponse(BaseModel):
    """A staff membership as returned by user endpoints."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    tenant_id: str
    identity_id: str | None = None
    role_ids: list[str]
    perm_version: int
    status: str
    professional: ProfessionalProfileResponse | None = None
    display_name: str | None = None
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    version: int

    @classmethod
    def from_user(cls, user: User) -> UserResponse:
        return cls(
            id=user.id or "",
            tenant_id=user.tenant_id,
            identity_id=user.identity_id,
            role_ids=user.role_ids,
            perm_version=user.perm_version,
            status=user.status,
            professional=(
                None
                if user.professional is None
                else ProfessionalProfileResponse(
                    license_no=user.professional.license_no,
                    cadre=user.professional.cadre,
                    signature_image_key=user.professional.signature_image_key,
                )
            ),
            display_name=user.display_name,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            version=user.version,
        )


__all__ = ["ProfessionalProfileResponse", "UserResponse"]
