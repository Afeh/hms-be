"""`TenantRepository`: safe CRUD primitives over one scoped collection.

Domain repositories subclass this and add their own queries; routers and services
never touch a collection directly (plan section 4.3). Versioned updates and soft
deletes arrive with the first resource module, since both are driven by the
`If-Match` and deletion rules of that resource.
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.core.context import MissingContextError, get_context
from app.db.tenancy import TenantCollection, TenantDocument
from app.utils.pagination import InvalidCursorError, decode_cursor, encode_cursor

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class TenantRepository[ModelT: TenantDocument]:
    """CRUD for one tenant-owned collection."""

    model: type[ModelT]

    def __init__(self, collection: TenantCollection) -> None:
        self._collection = collection

    async def get(self, entity_id: str) -> ModelT | None:
        object_id = _object_id(entity_id)
        if object_id is None:
            return None
        document = await self._collection.find_one({"_id": object_id})
        return None if document is None else self.model.model_validate(document)

    async def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> tuple[list[ModelT], str | None]:
        """Walk `_id` ascending; ordering by a domain field is the caller's query."""
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query: dict[str, Any] = {}
        if cursor:
            last_id = decode_cursor(cursor).get("id")
            object_id = _object_id(str(last_id)) if last_id else None
            if object_id is None:
                raise InvalidCursorError(cursor)
            query["_id"] = {"$gt": object_id}

        documents = [
            document
            async for document in self._collection.find(query, sort=[("_id", 1)], limit=limit + 1)
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
        result = await self._collection.insert_one(
            stored.model_dump(by_alias=True, exclude_none=True)
        )
        return stored.model_copy(update={"id": str(result.inserted_id)})


def _object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None
