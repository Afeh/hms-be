"""`TenantRepository`: safe CRUD primitives over one scoped collection.

Domain repositories subclass this and add their own queries; routers and services
never touch a collection directly (plan section 4.3). Optimistic concurrency
(`version` + `If-Match`), soft deletes and `_id` cursor paging live here once so
every resource repository inherits the same behaviour.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from app.core.context import MissingContextError, current_context, get_context
from app.core.errors import DuplicateEntityError, VersionConflictError
from app.db.tenancy import TenantCollection, TenantDocument
from app.utils.pagination import InvalidCursorError, decode_cursor, encode_cursor

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

#: Fields the caller may never set: ownership, identity and creation stamps.
IMMUTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "_id",
        "id",
        "tenant_id",
        "created_at",
        "created_by",
        "version",
        "deleted_at",
        "deleted_by",
    }
)


class TenantRepository[ModelT: TenantDocument]:
    """CRUD for one tenant-owned collection."""

    model: type[ModelT]

    def __init__(self, collection: TenantCollection) -> None:
        self._collection = collection

    async def get(self, entity_id: str, *, include_deleted: bool = False) -> ModelT | None:
        object_id = _object_id(entity_id)
        if object_id is None:
            return None
        query = self._visible({"_id": object_id}, include_deleted)
        document = await self._collection.find_one(query)
        return None if document is None else self.model.model_validate(document)

    async def find_one(
        self,
        query: Mapping[str, Any],
        *,
        projection: Mapping[str, Any] | None = None,
        include_deleted: bool = False,
    ) -> ModelT | None:
        """One matching document, excluding soft-deleted ones by default."""
        document = await self._collection.find_one(
            self._visible(query, include_deleted), projection=projection
        )
        return None if document is None else self.model.model_validate(document)

    async def list(
        self,
        *,
        filter: Mapping[str, Any] | None = None,
        sort: Sequence[tuple[str, int]] | None = None,
        projection: Mapping[str, Any] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[list[ModelT], str | None]:
        """Walk `_id` ascending by default, skipping soft-deleted rows."""
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = self._visible(filter or {}, include_deleted)
        if cursor:
            last_id = decode_cursor(cursor).get("id")
            object_id = _object_id(str(last_id)) if last_id else None
            if object_id is None:
                raise InvalidCursorError(cursor)
            query["_id"] = {"$gt": object_id}

        ordering = list(sort or [])
        if not any(field == "_id" for field, _ in ordering):
            ordering.append(("_id", 1))

        documents = [
            document
            async for document in self._collection.find(
                query, projection=projection, sort=ordering, limit=limit + 1
            )
        ]
        page = documents[:limit]
        next_cursor = (
            encode_cursor(id=str(page[-1]["_id"])) if len(documents) > limit and page else None
        )
        return [self.model.model_validate(document) for document in page], next_cursor

    async def create(self, model: ModelT) -> ModelT:
        """Insert with the context tenant, never the tenant the model claims."""
        tenant_id = get_context().tenant_id
        if tenant_id is None:
            raise MissingContextError("creating a document requires a tenant context")
        stored = model.model_copy(update={"tenant_id": tenant_id})
        try:
            result = await self._collection.insert_one(
                stored.model_dump(by_alias=True, exclude_none=True)
            )
        except DuplicateKeyError as exc:
            raise DuplicateEntityError(str(exc)) from exc
        return stored.model_copy(update={"id": str(result.inserted_id)})

    async def update_versioned(
        self, entity_id: str, changes: Mapping[str, Any], expected_version: int
    ) -> ModelT:
        """Compare-and-set `version` in one atomic update.

        Raises `VersionConflictError` when the document is missing, soft-deleted
        or no longer at `expected_version`, so a stale update never overwrites a
        newer one.
        """
        object_id = _object_id(entity_id)
        if object_id is None:
            raise VersionConflictError(entity_id, expected_version)

        immutable = IMMUTABLE_FIELDS.intersection(changes)
        if immutable:
            raise ValueError("these fields cannot be updated: " + ", ".join(sorted(immutable)))

        ctx = current_context()
        update = {
            "$set": {
                **changes,
                "updated_at": datetime.now(UTC),
                "updated_by": ctx.user_id if ctx else None,
            },
            "$inc": {"version": 1},
        }
        try:
            document = await self._collection.find_one_and_update(
                {"_id": object_id, "version": expected_version, "deleted_at": None},
                update,
                return_document=True,
            )
        except DuplicateKeyError as exc:
            raise DuplicateEntityError(str(exc)) from exc
        if document is None:
            raise VersionConflictError(entity_id, expected_version)
        return self.model.model_validate(document)

    async def soft_delete(self, entity_id: str) -> bool:
        """Stamp `deleted_at` / `deleted_by`; return whether this call changed it."""
        object_id = _object_id(entity_id)
        if object_id is None:
            return False
        ctx = current_context()
        document = await self._collection.find_one_and_update(
            {"_id": object_id, "deleted_at": None},
            {
                "$set": {
                    "deleted_at": datetime.now(UTC),
                    "deleted_by": ctx.user_id if ctx else None,
                }
            },
            return_document=True,
        )
        return document is not None

    def _visible(self, query: Mapping[str, Any], include_deleted: bool) -> dict[str, Any]:
        visible = dict(query)
        if not include_deleted:
            visible["deleted_at"] = None
        return visible


def _object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None
