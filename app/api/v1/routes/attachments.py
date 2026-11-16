"""Image attachments on visits, uploaded through the API to Cloudflare R2."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, File, Form, Query, UploadFile

from app.api.deps import get_app_settings, get_registry, get_storage, require_permission
from app.api.routing import ApiRouter, response_message
from app.core.config import Settings
from app.core.context import get_context
from app.core.errors import ApiError
from app.db import TenantConnectionRegistry
from app.schemas.attachment import AttachmentListResponse, AttachmentResponse, DownloadUrlSchema
from app.services.attachments import attachment_download, list_attachments, upload_attachment
from app.services.storage import Storage

router = ApiRouter(tags=["visit attachments"])


def _require_tenant_id() -> str:
    tenant_id = get_context().tenant_id
    if tenant_id is None:
        raise ApiError(401, "unauthenticated", "Authentication is required")
    return tenant_id


async def _read_image(file: UploadFile, max_bytes: int) -> bytes:
    """Read at most one byte over the limit so oversized files fail fast."""
    try:
        data = await file.read(max_bytes + 1)
    finally:
        await file.close()
    if len(data) > max_bytes:
        raise ApiError(
            413,
            "image_too_large",
            "The image exceeds the configured size limit",
            data={"max_bytes": max_bytes},
        )
    return data


@router.post(
    "/visits/{visit_id}/attachments",
    status_code=201,
    dependencies=[Depends(require_permission("document:capture"))],
)
@response_message("Image uploaded successfully")
async def upload_visit_attachment(
    visit_id: str,
    file: Annotated[UploadFile, File(description="Image file to attach to the visit")],
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    storage: Annotated[Storage, Depends(get_storage)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    caption: Annotated[str | None, Form(max_length=500)] = None,
) -> dict[str, AttachmentResponse]:
    """Upload an image through the API and attach it to an open visit."""
    tenant_id = _require_tenant_id()
    data = await _read_image(file, settings.storage_max_image_bytes)
    attachment = await upload_attachment(
        registry,
        tenant_id,
        visit_id,
        filename=file.filename,
        content_type=file.content_type,
        data=data,
        caption=caption,
        storage=storage,
        max_image_bytes=settings.storage_max_image_bytes,
    )
    return {"attachment": attachment}


@router.get(
    "/visits/{visit_id}/attachments",
    dependencies=[Depends(require_permission("document:read"))],
)
@response_message("Visit images retrieved successfully")
async def get_visit_attachments(
    visit_id: str,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    storage: Annotated[Storage, Depends(get_storage)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> AttachmentListResponse:
    """List a visit's images oldest first, each with a usable URL."""
    tenant_id = _require_tenant_id()
    attachments, next_cursor = await list_attachments(
        registry,
        tenant_id,
        visit_id,
        limit=limit,
        cursor=cursor,
        storage=storage,
    )
    return AttachmentListResponse(attachments=attachments, next_cursor=next_cursor)


@router.get(
    "/visits/{visit_id}/attachments/{attachment_id}/url",
    dependencies=[Depends(require_permission("document:read"))],
)
@response_message("Image download URL generated successfully")
async def get_visit_attachment_url(
    visit_id: str,
    attachment_id: str,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> dict[str, DownloadUrlSchema]:
    """Return a fresh short-lived download URL for an uploaded image."""
    tenant_id = _require_tenant_id()
    download = await attachment_download(
        registry,
        tenant_id,
        visit_id,
        attachment_id,
        storage,
    )
    return {"download": download}


__all__ = ["router"]
