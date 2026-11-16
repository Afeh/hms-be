"""Stored metadata for an image attached to a visit."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.db.tenancy import TenantDocument

AttachmentStatus = Literal["pending", "uploaded"]


class VisitAttachment(TenantDocument):
    """One private image stored in R2 and linked to a visit."""

    visit_id: str
    patient_id: str
    filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    status: AttachmentStatus = "pending"
    caption: str | None = None
    uploaded_at: datetime | None = None
    uploaded_by: str | None = None
    etag: str | None = None


__all__ = ["AttachmentStatus", "VisitAttachment"]
