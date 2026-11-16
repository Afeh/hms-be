"""Private image attachments on visits, stored in Cloudflare R2."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime

from app.core.context import get_context
from app.core.errors import ApiError, VersionConflictError
from app.db.client import TenantConnectionRegistry
from app.db.visit_attachments import VISIT_ATTACHMENTS_COLLECTION, VisitAttachmentRepository
from app.db.visits import VISITS_COLLECTION, VisitRepository
from app.models.visit import Visit
from app.models.visit_attachment import VisitAttachment
from app.schemas.attachment import (
    AttachmentCreate,
    AttachmentResponse,
    DownloadUrlSchema,
    PresignedUploadSchema,
)
from app.services.storage import (
    PresignedDownload,
    PresignedUpload,
    Storage,
    StorageUnavailableError,
)
from app.utils.pagination import InvalidCursorError

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/heic"}
)


def _attachment_repository(
    registry: TenantConnectionRegistry,
    tenant_id: str,
) -> VisitAttachmentRepository:
    return VisitAttachmentRepository(registry.collection(tenant_id, VISIT_ATTACHMENTS_COLLECTION))


def _visit_repository(
    registry: TenantConnectionRegistry,
    tenant_id: str,
) -> VisitRepository:
    return VisitRepository(registry.collection(tenant_id, VISITS_COLLECTION))


def _storage_unavailable() -> ApiError:
    return ApiError(503, "dependency_unavailable", "Object storage is unavailable")


def _storage_key(tenant_id: str, visit: Visit, visit_id: str) -> str:
    """A tenant-prefixed key that contains no user-supplied path segments."""
    random_part = secrets.token_urlsafe(24)
    return f"t/{tenant_id}/patients/{visit.patient_id}/visits/{visit_id}/attachments/{random_part}"


async def _require_open_visit(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    visit_id: str,
) -> Visit:
    visit = await _visit_repository(registry, tenant_id).get(visit_id)
    if visit is None:
        raise ApiError(404, "not_found", "Visit not found")
    if visit.status != "open":
        raise ApiError(409, "visit_not_open", "Images can only be added to an open visit")
    return visit


async def _get_attachment(
    repository: VisitAttachmentRepository,
    attachment_id: str,
    visit_id: str,
) -> VisitAttachment:
    attachment = await repository.get(attachment_id)
    if attachment is None or attachment.visit_id != visit_id:
        raise ApiError(404, "not_found", "Attachment not found")
    return attachment


async def _upload_instruction(
    storage: Storage,
    attachment: VisitAttachment,
) -> PresignedUploadSchema:
    try:
        presigned: PresignedUpload = await storage.presign_upload(
            key=attachment.storage_key,
            content_type=attachment.content_type,
        )
    except StorageUnavailableError as exc:
        raise _storage_unavailable() from exc
    return PresignedUploadSchema(
        url=presigned.url,
        headers=presigned.headers,
        expires_at=presigned.expires_at,
    )


async def _download_instruction(
    storage: Storage,
    attachment: VisitAttachment,
) -> PresignedDownload:
    try:
        return await storage.presign_download(key=attachment.storage_key)
    except StorageUnavailableError as exc:
        raise _storage_unavailable() from exc


async def attachment_response(
    attachment: VisitAttachment,
    storage: Storage,
) -> AttachmentResponse:
    """Return the metadata plus a usable URL for the attachment's current state."""
    if attachment.status == "pending":
        upload = await _upload_instruction(storage, attachment)
        return AttachmentResponse.from_model(attachment, upload=upload)

    download = await _download_instruction(storage, attachment)
    return AttachmentResponse.from_model(
        attachment,
        download_url=download.url,
        download_expires_at=download.expires_at,
    )


async def create_attachment(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    visit_id: str,
    payload: AttachmentCreate,
    storage: Storage,
    *,
    max_image_bytes: int,
) -> AttachmentResponse:
    """Create a pending record and return a direct-upload URL for R2."""
    visit = await _require_open_visit(registry, tenant_id, visit_id)
    if payload.content_type not in ALLOWED_IMAGE_TYPES:
        raise ApiError(
            415,
            "unsupported_media_type",
            "Only JPEG, PNG, WebP and HEIC images are supported",
        )
    if payload.size_bytes > max_image_bytes:
        raise ApiError(
            413,
            "image_too_large",
            "The image exceeds the configured size limit",
            data={"max_bytes": max_image_bytes},
        )

    ctx = get_context()
    repository = _attachment_repository(registry, tenant_id)
    attachment = await repository.create(
        VisitAttachment(
            tenant_id=tenant_id,
            visit_id=visit.id or visit_id,
            patient_id=visit.patient_id,
            filename=payload.filename,
            content_type=payload.content_type,
            size_bytes=payload.size_bytes,
            storage_key=_storage_key(tenant_id, visit, visit.id or visit_id),
            caption=payload.caption,
            created_at=datetime.now(UTC),
            created_by=ctx.user_id,
        )
    )
    try:
        return await attachment_response(attachment, storage)
    except ApiError as exc:
        if exc.status_code == 503 and attachment.id is not None:
            await repository.soft_delete(attachment.id)
        raise


async def complete_attachment(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    visit_id: str,
    attachment_id: str,
    storage: Storage,
) -> AttachmentResponse:
    """Verify the object in R2 and make the attachment readable."""
    repository = _attachment_repository(registry, tenant_id)
    attachment = await _get_attachment(repository, attachment_id, visit_id)
    if attachment.status == "uploaded":
        return await attachment_response(attachment, storage)

    try:
        stored = await storage.head_object(key=attachment.storage_key)
    except StorageUnavailableError as exc:
        raise _storage_unavailable() from exc
    if stored is None:
        raise ApiError(409, "object_not_found", "The image has not been uploaded yet")

    if stored.size_bytes != attachment.size_bytes:
        await _discard(repository, attachment, storage)
        raise ApiError(
            422,
            "size_mismatch",
            "The uploaded image size does not match the declared size",
        )
    if (
        stored.content_type is not None
        and stored.content_type.split(";", 1)[0].strip().lower() != attachment.content_type
    ):
        await _discard(repository, attachment, storage)
        raise ApiError(
            415,
            "unsupported_media_type",
            "The uploaded object's content type does not match the declared type",
        )

    ctx = get_context()
    try:
        updated = await repository.update_versioned(
            attachment_id,
            {
                "status": "uploaded",
                "uploaded_at": datetime.now(UTC),
                "uploaded_by": ctx.user_id,
                "etag": stored.etag,
            },
            attachment.version,
        )
    except VersionConflictError as exc:
        current = await repository.get(attachment_id)
        if current is not None and current.status == "uploaded":
            return await attachment_response(current, storage)
        raise ApiError(
            409,
            "conflict",
            "The attachment changed while the upload was being confirmed",
        ) from exc
    return await attachment_response(updated, storage)


async def _discard(
    repository: VisitAttachmentRepository,
    attachment: VisitAttachment,
    storage: Storage,
) -> None:
    """Delete an object that failed verification; leave the pending record intact."""
    try:
        await storage.delete_object(key=attachment.storage_key)
    except StorageUnavailableError:
        logger.warning(
            "could not delete rejected visit image",
            extra={"attachment_id": attachment.id, "storage_key": attachment.storage_key},
        )
    if attachment.id is not None:
        await repository.soft_delete(attachment.id)


async def list_attachments(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    visit_id: str,
    *,
    limit: int,
    cursor: str | None,
    storage: Storage,
) -> tuple[list[AttachmentResponse], str | None]:
    visit = await _visit_repository(registry, tenant_id).get(visit_id)
    if visit is None:
        raise ApiError(404, "not_found", "Visit not found")
    try:
        attachments, next_cursor = await _attachment_repository(registry, tenant_id).list_for_visit(
            visit_id, limit=limit, cursor=cursor
        )
    except InvalidCursorError as exc:
        raise ApiError(400, "invalid_cursor", "The pagination cursor is invalid") from exc
    return (
        [await attachment_response(attachment, storage) for attachment in attachments],
        next_cursor,
    )


async def attachment_download(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    visit_id: str,
    attachment_id: str,
    storage: Storage,
) -> DownloadUrlSchema:
    repository = _attachment_repository(registry, tenant_id)
    attachment = await _get_attachment(repository, attachment_id, visit_id)
    if attachment.status != "uploaded":
        raise ApiError(409, "object_not_found", "The image has not been uploaded yet")
    download = await _download_instruction(storage, attachment)
    return DownloadUrlSchema(url=download.url, expires_at=download.expires_at)


__all__ = [
    "ALLOWED_IMAGE_TYPES",
    "attachment_download",
    "attachment_response",
    "complete_attachment",
    "create_attachment",
    "list_attachments",
]
