"""The only gateway to a MongoDB collection.

PyMongo has no schema middleware, so tenancy is enforced here instead: every
filter is rewritten with the `tenant_id` from the request context, every inserted
document is stamped with it, and a caller that supplies a different tenant fails
loudly. Aggregations get a leading `$match`, or a filter inside `$search` and
`$geoNear` when those must be the first stage.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from motor.motor_asyncio import (
    AsyncIOMotorCollection,
    AsyncIOMotorCommandCursor,
    AsyncIOMotorCursor,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.results import DeleteResult, InsertManyResult, InsertOneResult, UpdateResult

from app.core.context import MissingContextError, get_context
from app.core.errors import QuerySanitizationError

logger = logging.getLogger(__name__)

TENANT_FIELD = "tenant_id"

#: Collections with no tenant: global identities, platform users, the tenant
#: registry itself and the platform audit trail. The isolation suite asserts
#: that this list is the whole of the exemptions (plan section 4.6).
TENANT_EXEMPT_COLLECTIONS: frozenset[str] = frozenset(
    {"identities", "platform_users", "tenants", "platform_audit_logs"}
)

_system_reason: ContextVar[str | None] = ContextVar("system_access_reason", default=None)


class TenantIsolationError(RuntimeError):
    """Raised when a caller tries to read or write another tenant's documents."""


def sanitize_query(query: Mapping[str, Any] | None) -> dict[str, Any]:
    """Reject $-prefixed keys and dotted keys to prevent operator injection."""
    if not query:
        return {}

    def _check_mapping(mapping: Mapping[str, Any]) -> None:
        for k, v in mapping.items():
            if k.startswith("$") or "." in k:
                raise QuerySanitizationError(k)
            _check_value(v)

    def _check_value(val: Any) -> None:
        if isinstance(val, Mapping):
            _check_mapping(val)
        elif isinstance(val, (list, tuple, set)):
            for item in val:
                _check_value(item)

    _check_mapping(query)
    return dict(query)


@contextlib.contextmanager
def run_as_system(reason: str) -> Iterator[None]:
    """Bypass the guard for migrations and platform tasks. Always logged."""
    if not reason:
        raise ValueError("run_as_system requires a non-empty reason")
    token = _system_reason.set(reason)
    logger.warning("tenant guard bypassed", extra={"system_reason": reason})
    try:
        yield
    finally:
        _system_reason.reset(token)


class TenantDocument(BaseModel):
    """Base for every tenant-owned document.

    `_id` is exposed as `id` so Pydantic field names stay valid Python while the
    stored and serialized key is `_id`.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str | None = Field(default=None, alias="_id")
    tenant_id: str
    created_at: datetime | None = None
    created_by: str | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    version: int = 1

    @field_validator("id", mode="before")
    @classmethod
    def _id_to_str(cls, value: object) -> object:
        return str(value) if value is not None else None


class TenantCollection:
    """A Motor collection that cannot be used without a tenant scope."""

    def __init__(
        self, collection: AsyncIOMotorCollection[dict[str, Any]], name: str | None = None
    ) -> None:
        self._collection = collection
        self._name = name or str(collection.name)

    @property
    def name(self) -> str:
        return self._name

    def sanitize(self, query: Mapping[str, Any] | None) -> dict[str, Any]:
        """Sanitize a query from untrusted input before driver evaluation."""
        return sanitize_query(query)

    # Reads -----------------------------------------------------------------

    def find(
        self,
        filter: Mapping[str, Any] | None = None,
        *,
        projection: Mapping[str, Any] | None = None,
        sort: Sequence[tuple[str, int]] | None = None,
        limit: int = 0,
        skip: int = 0,
        sanitize: bool = False,
        **kwargs: Any,
    ) -> AsyncIOMotorCursor[dict[str, Any]]:
        if sanitize and filter:
            sanitize_query(filter)
        cursor = self._collection.find(self._scoped(filter), projection, **kwargs)
        if sort:
            cursor = cursor.sort(list(sort))
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit)
        return cursor

    async def find_one(
        self,
        filter: Mapping[str, Any] | None = None,
        *,
        projection: Mapping[str, Any] | None = None,
        sanitize: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        if sanitize and filter:
            sanitize_query(filter)
        return await self._collection.find_one(self._scoped(filter), projection, **kwargs)

    async def count_documents(
        self,
        filter: Mapping[str, Any] | None = None,
        *,
        sanitize: bool = False,
        **kwargs: Any,
    ) -> int:
        if sanitize and filter:
            sanitize_query(filter)
        return await self._collection.count_documents(self._scoped(filter), **kwargs)

    def aggregate(
        self, pipeline: Sequence[Mapping[str, Any]], **kwargs: Any
    ) -> AsyncIOMotorCommandCursor[dict[str, Any]]:
        return self._collection.aggregate(self._scoped_pipeline(pipeline), **kwargs)

    # Writes ----------------------------------------------------------------

    async def insert_one(self, document: Mapping[str, Any], **kwargs: Any) -> InsertOneResult:
        return await self._collection.insert_one(self._stamped(document), **kwargs)

    async def insert_many(
        self, documents: Sequence[Mapping[str, Any]], **kwargs: Any
    ) -> InsertManyResult:
        stamped = [self._stamped(doc) for doc in documents]
        return await self._collection.insert_many(stamped, **kwargs)

    async def update_one(
        self,
        filter: Mapping[str, Any],
        update: Mapping[str, Any],
        *,
        upsert: bool = False,
        **kwargs: Any,
    ) -> UpdateResult:
        return await self._collection.update_one(
            self._scoped(filter), update, upsert=upsert, **kwargs
        )

    async def update_many(
        self,
        filter: Mapping[str, Any],
        update: Mapping[str, Any],
        *,
        upsert: bool = False,
        **kwargs: Any,
    ) -> UpdateResult:
        return await self._collection.update_many(
            self._scoped(filter), update, upsert=upsert, **kwargs
        )

    async def find_one_and_update(
        self,
        filter: Mapping[str, Any],
        update: Mapping[str, Any],
        *,
        upsert: bool = False,
        return_document: bool = True,  # ReturnDocument.AFTER; pymongo types it as bool
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        return await self._collection.find_one_and_update(
            self._scoped(filter),
            update,
            upsert=upsert,
            return_document=return_document,
            **kwargs,
        )

    async def delete_one(self, filter: Mapping[str, Any], **kwargs: Any) -> DeleteResult:
        return await self._collection.delete_one(self._scoped(filter), **kwargs)

    async def delete_many(self, filter: Mapping[str, Any], **kwargs: Any) -> DeleteResult:
        return await self._collection.delete_many(self._scoped(filter), **kwargs)

    # Scoping ---------------------------------------------------------------

    def _scope(self) -> dict[str, Any] | None:
        """Return the filter fragment to enforce, or `None` for unscoped access."""
        if self._name in TENANT_EXEMPT_COLLECTIONS:
            return None
        if _system_reason.get() is not None:
            return None
        ctx = get_context()
        if ctx.tenant_id is None:
            raise MissingContextError(f"no tenant in context for collection {self._name!r}")
        return {TENANT_FIELD: ctx.tenant_id}

    def _scoped(self, filter: Mapping[str, Any] | None) -> dict[str, Any]:
        scoped = dict(filter or {})
        scope = self._scope()
        if scope is None:
            return scoped
        claimed = scoped.get(TENANT_FIELD)
        if claimed is not None and claimed != scope[TENANT_FIELD]:
            raise TenantIsolationError(
                f"filter claims {TENANT_FIELD}={claimed!r}, context is {scope[TENANT_FIELD]!r}"
            )
        scoped.update(scope)
        return scoped

    def _stamped(self, document: Mapping[str, Any]) -> dict[str, Any]:
        stamped = dict(document)
        scope = self._scope()
        if scope is None:
            return stamped
        claimed = stamped.get(TENANT_FIELD)
        if claimed is not None and claimed != scope[TENANT_FIELD]:
            raise TenantIsolationError(
                f"document claims {TENANT_FIELD}={claimed!r}, context is {scope[TENANT_FIELD]!r}"
            )
        stamped.update(scope)
        return stamped

    def _scoped_pipeline(self, pipeline: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        stages = [dict(stage) for stage in pipeline]
        scope = self._scope()
        if scope is None:
            return stages

        if not stages:
            return [{"$match": scope}]

        first = stages[0]
        if "$search" in first:
            stages[0] = {"$search": _with_tenant_filter(first["$search"], scope)}
        elif "$geoNear" in first:
            geo_near = dict(first["$geoNear"])
            query = dict(geo_near.get("query") or {})
            _assert_same_tenant(query, scope)
            geo_near["query"] = {**query, **scope}
            stages[0] = {"$geoNear": geo_near}
        elif "$match" in first:
            match = dict(first["$match"])
            _assert_same_tenant(match, scope)
            stages[0] = {"$match": {**match, **scope}}
        else:
            stages.insert(0, {"$match": scope})
        return stages


def _assert_same_tenant(fragment: Mapping[str, Any], scope: Mapping[str, Any]) -> None:
    claimed = fragment.get(TENANT_FIELD)
    if claimed is not None and claimed != scope[TENANT_FIELD]:
        raise TenantIsolationError(
            f"aggregation claims {TENANT_FIELD}={claimed!r}, context is {scope[TENANT_FIELD]!r}"
        )


def _with_tenant_filter(search: Mapping[str, Any], scope: Mapping[str, Any]) -> dict[str, Any]:
    """Add the tenant to an Atlas Search stage without losing an existing filter."""
    merged = dict(search)
    existing = merged.get("filter")
    if existing is not None and not isinstance(existing, Mapping):
        raise TenantIsolationError("$search filter must be an object to add the tenant clause")
    filter_ = dict(existing or {})
    _assert_same_tenant(filter_, scope)
    merged["filter"] = {**filter_, **scope}
    return merged
