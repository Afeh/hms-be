"""Access to a visit's uploaded images."""

from __future__ import annotations

from app.db.indexes import VISIT_ATTACHMENTS_COLLECTION
from app.db.repository import TenantRepository
from app.models.visit_attachment import VisitAttachment


class VisitAttachmentRepository(TenantRepository[VisitAttachment]):
    """A tenant's visit-attachment collection."""

    model = VisitAttachment

    async def list_for_visit(
        self,
        visit_id: str,
        *,
        limit: int = 25,
        cursor: str | None = None,
    ) -> tuple[list[VisitAttachment], str | None]:
        return await self.list(
            filter={"visit_id": visit_id},
            sort=[("created_at", 1)],
            limit=limit,
            cursor=cursor,
        )


__all__ = ["VISIT_ATTACHMENTS_COLLECTION", "VisitAttachmentRepository"]
