"""Access to tenant visits (plan §6.4, sprint S002)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.db.repository import TenantRepository
from app.models.visit import Visit
from app.utils.pagination import InvalidCursorError, decode_cursor, encode_cursor

VISITS_COLLECTION = "visits"
MAX_PAGE_SIZE = 100


class VisitRepository(TenantRepository[Visit]):
    """A tenant's visit collection."""

    model = Visit

    async def list_for_patient(
        self,
        patient_id: str,
        *,
        limit: int = 25,
        cursor: str | None = None,
    ) -> tuple[list[Visit], str | None]:
        """Newest visit first, with keyset paging on visit date and ``_id``."""
        query: dict[str, object] = {
            "deleted_at": None,
            "patient_id": patient_id,
        }
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        if cursor is not None:
            query.update(self._cursor_filter(cursor))

        documents = [
            document
            async for document in self._collection.find(
                query,
                sort=[("visit_date", -1), ("_id", -1)],
                limit=limit + 1,
            )
        ]
        page = documents[:limit]
        next_cursor = None
        if len(documents) > limit and page:
            last = page[-1]
            next_cursor = encode_cursor(
                id=str(last["_id"]),
                value=last["visit_date"].isoformat(),
            )
        return [Visit.model_validate(document) for document in page], next_cursor

    @staticmethod
    def _cursor_filter(cursor: str) -> dict[str, Any]:
        payload = decode_cursor(cursor)
        last_id = payload.get("id")
        raw_date = payload.get("value")
        if not last_id or raw_date is None:
            raise InvalidCursorError(cursor)
        try:
            object_id = ObjectId(str(last_id))
            visit_date = datetime.fromisoformat(str(raw_date))
        except (InvalidId, TypeError, ValueError) as exc:
            raise InvalidCursorError(cursor) from exc
        return {
            "$or": [
                {"visit_date": {"$lt": visit_date}},
                {"visit_date": visit_date, "_id": {"$lt": object_id}},
            ]
        }


__all__ = ["VISITS_COLLECTION", "VisitRepository"]
