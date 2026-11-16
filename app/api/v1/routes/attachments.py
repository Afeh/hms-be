"""Image attachments on visits, uploaded directly to Cloudflare R2."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query

from app.api.deps import get_app_settings, get_registry, get_storage, require_permission
from app.api.routing import ApiRouter, response_message
from app.core.config import Settings
from app.core.context import get_context
from app.core.errors import ApiError
from app.db import TenantConnectionRegistry
from app.schemas.attachment import (
    AttachmentCreate,
    AttachmentListResponse,
    AttachmentResponse,
    DownloadUrlSchema,
)
from app.services.attachments import (
    attachment_download,
    complete_attachment,
    create_attachment,
    list_attachments,
)
from app.services.storage import Storage

router = ApiRouter(tags=["visit attachments"])


def _require_tenant_id() -> str:
    tenant_id = get_context().tenant_id
    if tenant_id is None:
        raise ApiError(401, "unauthenticated", "Authentication is required")
    return tenant_id


@router.post(
    "/visits/{visit_id}/attachments",
    status_code=201,
    dependencies=[Depends(require_permission("document:capture"))],
)
@response_message("Image upload URL generated successfully")
async def create_visit_attachment(
    visit_id: str,
    body: AttachmentCreate,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    storage: Annotated[Storage, Depends(get_storage)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> dict[str, AttachmentResponse]:
    """Create a pending attachment and return its short-lived R2 upload URL."""
    tenant_id = _require_tenant_id()
    attachment = await create_attachment(
        registry,
        tenant_id,
        visit_id,
        body,
        storage,
        max_image_bytes=settings.storage_max_image_bytes,
    )
    return {"attachment": attachment}


@router.post(
    "/visits/{visit_id}/attachments/{attachment_id}/complete",
    dependencies=[Depends(require_permission("document:capture"))],
)
@response_message("Image attached successfully")
async def complete_visit_attachment(
    visit_id: str,
    attachment_id: str,
    registry: Annotated[TenantConnectionRegistry, Depends(get_registry)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> dict[str, AttachmentResponse]:
    """Verify the uploaded object and mark the attachment as readable."""
    tenant_id = _require_tenant_id()
    attachment = await complete_attachment(
        registry,
        tenant_id,
        visit_id,
        attachment_id,
        storage,
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
    """Return a fresh short-lived download URL for a completed upload."""
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
