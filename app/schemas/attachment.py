"""Visit-image attachment request and response shapes."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.base import BaseSchema

if TYPE_CHECKING:
    from app.models.visit_attachment import VisitAttachment

AttachmentStatus = Literal["pending", "uploaded"]


class AttachmentCreate(BaseSchema):
    """Ask the API for a direct-upload URL for one image."""

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(gt=0)
    caption: str | None = Field(default=None, max_length=500)

    @field_validator("filename")
    @classmethod
    def _filename_basename(cls, value: str) -> str:
        name = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not name or name in {".", ".."} or "\x00" in name:
            raise ValueError("filename is required")
        return name

    @field_validator("content_type")
    @classmethod
    def _normalise_content_type(cls, value: str) -> str:
        return value.split(";", 1)[0].strip().lower()


class PresignedUploadSchema(BaseModel):
    """Everything the client needs to PUT the image directly to R2."""

    model_config = ConfigDict(extra="ignore")

    url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    expires_at: datetime


class DownloadUrlSchema(BaseModel):
    """A fresh short-lived direct-download URL."""

    model_config = ConfigDict(extra="ignore")

    url: str
    expires_at: datetime


class AttachmentResponse(BaseModel):
    """One visit image, including a usable URL for the current state."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    visit_id: str
    patient_id: str
    filename: str
    content_type: str
    size_bytes: int
    status: AttachmentStatus
    caption: str | None = None
    upload: PresignedUploadSchema | None = None
    download_url: str | None = None
    download_expires_at: datetime | None = None
    uploaded_at: datetime | None = None
    uploaded_by: str | None = None
    version: int
    created_at: datetime | None = None

    @classmethod
    def from_model(
        cls,
        attachment: VisitAttachment,
        *,
        upload: PresignedUploadSchema | None = None,
        download_url: str | None = None,
        download_expires_at: datetime | None = None,
    ) -> AttachmentResponse:
        return cls(
            id=attachment.id or "",
            visit_id=attachment.visit_id,
            patient_id=attachment.patient_id,
            filename=attachment.filename,
            content_type=attachment.content_type,
            size_bytes=attachment.size_bytes,
            status=attachment.status,
            caption=attachment.caption,
            upload=upload,
            download_url=download_url,
            download_expires_at=download_expires_at,
            uploaded_at=attachment.uploaded_at,
            uploaded_by=attachment.uploaded_by,
            version=attachment.version,
            created_at=attachment.created_at,
        )


class AttachmentListResponse(BaseModel):
    """One cursor page of images attached to a visit."""

    model_config = ConfigDict(extra="ignore")

    attachments: list[AttachmentResponse]
    next_cursor: str | None = None


__all__ = [
    "AttachmentCreate",
    "AttachmentListResponse",
    "AttachmentResponse",
    "AttachmentStatus",
    "DownloadUrlSchema",
    "PresignedUploadSchema",
]
