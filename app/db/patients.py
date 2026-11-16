"""Access to tenant patients (plan §6.4, sprint S001)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from bson import ObjectId
from bson.errors import InvalidId

from app.db.repository import TenantRepository
from app.models.patient import Patient, PatientStatus
from app.utils.pagination import InvalidCursorError, decode_cursor, encode_cursor

PATIENTS_COLLECTION = "patients"
MAX_PAGE_SIZE = 100

PatientSort = Literal["registration_date", "-registration_date", "name", "-name"]


class PatientRepository(TenantRepository[Patient]):
    """A tenant's patient collection."""

    model = Patient

    async def find_by_number(self, patient_number: str) -> Patient | None:
        return await self.find_one({"patient_number": patient_number})

    async def record_visit(self, patient_id: str, visited_at: datetime) -> None:
        """Advance ``last_visit_date`` without bumping the demographic version."""
        try:
            object_id = ObjectId(patient_id)
        except (InvalidId, TypeError):
            return
        await self._collection.update_one(
            {"_id": object_id, "deleted_at": None},
            {
                "$max": {"last_visit_date": visited_at},
                "$set": {"updated_at": datetime.now(UTC)},
            },
        )

    async def list_patients(
        self,
        *,
        status: PatientStatus | None = None,
        registered_from: datetime | None = None,
        registered_to: datetime | None = None,
        sort: PatientSort = "-registration_date",
        limit: int = 25,
        cursor: str | None = None,
    ) -> tuple[list[Patient], str | None]:
        """List patients with the demo filters and cursor paging."""
        query: dict[str, object] = {"deleted_at": None}
        if status is not None:
            query["status"] = status

        registration_range: dict[str, datetime] = {}
        if registered_from is not None:
            registration_range["$gte"] = registered_from
        if registered_to is not None:
            registration_range["$lt"] = registered_to
        if registration_range:
            query["registration_date"] = registration_range

        direction = -1 if sort.startswith("-") else 1
        field = "display_name" if sort.endswith("name") else "registration_date"
        limit = max(1, min(limit, MAX_PAGE_SIZE))

        if cursor is not None:
            query.update(self._cursor_filter(cursor, field, direction))

        documents = [
            document
            async for document in self._collection.find(
                query,
                sort=[(field, direction), ("_id", direction)],
                limit=limit + 1,
            )
        ]
        page = documents[:limit]
        next_cursor = None
        if len(documents) > limit and page:
            last = page[-1]
            value = last[field]
            next_cursor = encode_cursor(
                id=str(last["_id"]),
                value=value.isoformat() if isinstance(value, datetime) else str(value),
            )
        return [Patient.model_validate(document) for document in page], next_cursor

    @staticmethod
    def _cursor_filter(cursor: str, field: str, direction: int) -> dict[str, Any]:
        payload = decode_cursor(cursor)
        last_id = payload.get("id")
        raw_value = payload.get("value")
        if not last_id or raw_value is None:
            raise InvalidCursorError(cursor)
        try:
            object_id = ObjectId(str(last_id))
        except (InvalidId, TypeError) as exc:
            raise InvalidCursorError(cursor) from exc

        value: object = str(raw_value)
        if field == "registration_date":
            try:
                value = datetime.fromisoformat(str(raw_value))
            except ValueError as exc:
                raise InvalidCursorError(cursor) from exc

        comparison = "$lt" if direction < 0 else "$gt"
        return {
            "$or": [
                {field: {comparison: value}},
                {field: value, "_id": {comparison: object_id}},
            ]
        }


__all__ = ["PATIENTS_COLLECTION", "PatientRepository", "PatientSort"]
