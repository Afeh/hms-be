"""Access to tenant vital-signs observations (plan §6.4, sprint S002)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.db.repository import TenantRepository
from app.models.vital_signs import VitalSigns
from app.utils.pagination import InvalidCursorError, decode_cursor, encode_cursor

VITAL_SIGNS_COLLECTION = "vital_signs"
MAX_PAGE_SIZE = 100


class VitalSignsRepository(TenantRepository[VitalSigns]):
    """A tenant's vital-signs collection."""

    model = VitalSigns

    async def list_for_visit(self, visit_id: str) -> list[VitalSigns]:
        """Every active measurement for a visit, oldest first."""
        documents = [
            document
            async for document in self._collection.find(
                {"deleted_at": None, "visit_id": visit_id, "status": "active"},
                sort=[("measured_at", 1), ("_id", 1)],
            )
        ]
        return [VitalSigns.model_validate(document) for document in documents]

    async def list_for_patient(
        self,
        patient_id: str,
        *,
        visit_id: str | None = None,
        measured_from: datetime | None = None,
        measured_to: datetime | None = None,
        limit: int = 25,
        cursor: str | None = None,
    ) -> tuple[list[VitalSigns], str | None]:
        """Newest measurement first, with keyset paging."""
        query: dict[str, object] = {
            "deleted_at": None,
            "patient_id": patient_id,
        }
        if visit_id is not None:
            query["visit_id"] = visit_id

        measured_range: dict[str, datetime] = {}
        if measured_from is not None:
            measured_range["$gte"] = measured_from
        if measured_to is not None:
            measured_range["$lte"] = measured_to
        if measured_range:
            query["measured_at"] = measured_range

        limit = max(1, min(limit, MAX_PAGE_SIZE))
        if cursor is not None:
            query.update(self._cursor_filter(cursor))

        documents = [
            document
            async for document in self._collection.find(
                query,
                sort=[("measured_at", -1), ("_id", -1)],
                limit=limit + 1,
            )
        ]
        page = documents[:limit]
        next_cursor = None
        if len(documents) > limit and page:
            last = page[-1]
            next_cursor = encode_cursor(
                id=str(last["_id"]),
                value=last["measured_at"].isoformat(),
            )
        return [VitalSigns.model_validate(document) for document in page], next_cursor

    async def latest_height(self, patient_id: str) -> float | None:
        """The most recently recorded height, for BMI when only weight is sent."""
        cursor = self._collection.find(
            {"deleted_at": None, "patient_id": patient_id, "status": "active"}
        ).sort([("measured_at", -1)])
        async for document in cursor.limit(50):
            height = document.get("height_cm")
            if height is not None:
                return float(height)
        return None

    @staticmethod
    def _cursor_filter(cursor: str) -> dict[str, Any]:
        payload = decode_cursor(cursor)
        last_id = payload.get("id")
        raw_date = payload.get("value")
        if not last_id or raw_date is None:
            raise InvalidCursorError(cursor)
        try:
            object_id = ObjectId(str(last_id))
            measured_at = datetime.fromisoformat(str(raw_date))
        except (InvalidId, TypeError, ValueError) as exc:
            raise InvalidCursorError(cursor) from exc
        return {
            "$or": [
                {"measured_at": {"$lt": measured_at}},
                {"measured_at": measured_at, "_id": {"$lt": object_id}},
            ]
        }


__all__ = ["VITAL_SIGNS_COLLECTION", "VitalSignsRepository"]
