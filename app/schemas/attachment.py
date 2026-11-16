"""Visit-image attachment request and response shapes."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.models.visit_attachment import VisitAttachment

AttachmentStatus = Literal["pending", "uploaded"]


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
    "AttachmentListResponse",
    "AttachmentResponse",
    "AttachmentStatus",
    "DownloadUrlSchema",
]
