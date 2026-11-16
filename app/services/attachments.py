"""Private image attachments on visits, stored in Cloudflare R2."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime

from app.core.context import get_context
from app.core.errors import ApiError
from app.db.client import TenantConnectionRegistry
from app.db.visit_attachments import VISIT_ATTACHMENTS_COLLECTION, VisitAttachmentRepository
from app.db.visits import VISITS_COLLECTION, VisitRepository
from app.models.visit import Visit
from app.models.visit_attachment import VisitAttachment
from app.schemas.attachment import AttachmentResponse, DownloadUrlSchema
from app.services.storage import (
    PresignedDownload,
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


def _safe_filename(value: str | None) -> str:
    """Keep only a basename; the storage key never uses it, but metadata does."""
    name = (value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."} or "\x00" in name:
        raise ApiError(422, "invalid_filename", "An image filename is required")
    if len(name) > 255:
        raise ApiError(422, "invalid_filename", "The image filename is too long")
    return name


def _normalise_content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


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
    """Return the metadata plus a usable download URL for a stored attachment."""
    if attachment.status != "uploaded":
        return AttachmentResponse.from_model(attachment)

    download = await _download_instruction(storage, attachment)
    return AttachmentResponse.from_model(
        attachment,
        download_url=download.url,
        download_expires_at=download.expires_at,
    )


async def _delete_quietly(storage: Storage, key: str) -> None:
    """Best-effort cleanup when metadata could not be saved after an upload."""
    try:
        await storage.delete_object(key=key)
    except StorageUnavailableError:
        logger.warning("could not delete orphaned visit image", extra={"storage_key": key})


async def upload_attachment(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    visit_id: str,
    *,
    filename: str | None,
    content_type: str | None,
    data: bytes,
    caption: str | None,
    storage: Storage,
    max_image_bytes: int,
) -> AttachmentResponse:
    """Store one uploaded image and create its metadata in a single request."""
    visit = await _require_open_visit(registry, tenant_id, visit_id)
    safe_filename = _safe_filename(filename)
    normalised_type = _normalise_content_type(content_type)
    if normalised_type not in ALLOWED_IMAGE_TYPES:
        raise ApiError(
            415,
            "unsupported_media_type",
            "Only JPEG, PNG, WebP and HEIC images are supported",
        )
    if not data:
        raise ApiError(422, "empty_image", "The uploaded image is empty")
    if len(data) > max_image_bytes:
        raise ApiError(
            413,
            "image_too_large",
            "The image exceeds the configured size limit",
            data={"max_bytes": max_image_bytes},
        )

    ctx = get_context()
    key = _storage_key(tenant_id, visit, visit.id or visit_id)
    try:
        etag = await storage.upload_object(
            key=key,
            content_type=normalised_type,
            data=data,
        )
    except StorageUnavailableError as exc:
        raise _storage_unavailable() from exc

    repository = _attachment_repository(registry, tenant_id)
    now = datetime.now(UTC)
    try:
        attachment = await repository.create(
            VisitAttachment(
                tenant_id=tenant_id,
                visit_id=visit.id or visit_id,
                patient_id=visit.patient_id,
                filename=safe_filename,
                content_type=normalised_type,
                size_bytes=len(data),
                storage_key=key,
                status="uploaded",
                caption=caption,
                uploaded_at=now,
                uploaded_by=ctx.user_id,
                etag=etag,
                created_at=now,
                created_by=ctx.user_id,
            )
        )
    except Exception:
        await _delete_quietly(storage, key)
        raise
    return await attachment_response(attachment, storage)


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
    "list_attachments",
    "upload_attachment",
]
