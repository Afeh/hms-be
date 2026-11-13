"""Access layer for the `tenants` collection (plan §4.6, issue 0010).

This collection is TENANT-EXEMPT: it stores the tenant registry itself, so
it cannot be scoped by tenant.  Every method requires an explicit tenant ID
and uses ``run_as_system`` to bypass the guard.  No generic ``TenantRepository``
here — the access is intentionally narrow.
"""

from __future__ import annotations

import logging
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.core.errors import DuplicateEntityError
from app.db.client import TenantConnectionRegistry
from app.db.tenancy import run_as_system
from app.models.tenant import Tenant, TenantStatus

logger = logging.getLogger(__name__)

TENANTS_COLLECTION = "tenants"


def _id_query(val: str) -> dict[str, Any]:
    try:
        oid = ObjectId(val)
        return {"$or": [{"_id": oid}, {"_id": val}]}
    except (InvalidId, TypeError):
        return {"_id": val}


class TenantStore:
    """Read/write access to the tenant registry.

    This is *not* a TenantCollection — it uses the raw Motor collection under
    ``run_as_system`` because the registry pre-dates any tenant context.
    """

    def __init__(self, registry: TenantConnectionRegistry) -> None:
        self._col = registry.database[TENANTS_COLLECTION]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create(self, tenant: Tenant) -> Tenant:
        """Insert a new tenant record.

        Raises ``DuplicateEntityError`` when the slug is already taken.
        The returned model carries the new ``_id``.
        """
        doc = tenant.model_dump(by_alias=True, exclude_none=True)
        if "_id" not in doc and tenant.id is not None:
            doc["_id"] = tenant.id
        try:
            with run_as_system("TenantStore.create"):
                result = await self._col.insert_one(doc)
        except DuplicateKeyError as exc:
            raise DuplicateEntityError(str(exc)) from exc
        return tenant.model_copy(update={"id": str(result.inserted_id)})

    async def update_status(
        self,
        tenant_id: str,
        status: TenantStatus,
    ) -> bool:
        """Set a tenant's status. Returns True if the document was found and updated."""
        with run_as_system("TenantStore.update_status"):
            result = await self._col.update_one(
                _id_query(tenant_id),
                {"$set": {"status": status}},
            )
        return result.matched_count > 0

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_by_id(self, tenant_id: str) -> Tenant | None:
        """Fetch a tenant regardless of status (used by the guard bootstrap)."""
        with run_as_system("TenantStore.get_by_id"):
            doc = await self._col.find_one(_id_query(tenant_id))
        return _load(doc)

    async def get_active_by_id(self, tenant_id: str) -> Tenant | None:
        """Fetch an *active* tenant, returning None if suspended/deleted."""
        with run_as_system("TenantStore.get_active_by_id"):
            base = _id_query(tenant_id)
            query: dict[str, Any]
            if "$or" in base:
                query = {"$and": [base, {"status": "active"}]}
            else:
                query = {"_id": base["_id"], "status": "active"}
            doc = await self._col.find_one(query)
        return _load(doc)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        """Fetch by slug (used during signup / vanity routing)."""
        with run_as_system("TenantStore.get_by_slug"):
            doc = await self._col.find_one({"slug": slug})
        return _load(doc)

    async def list_active(self, limit: int = 100) -> list[Tenant]:
        """Return up to *limit* active tenants (used by the outbox sweeper)."""
        with run_as_system("TenantStore.list_active"):
            cursor = self._col.find({"status": "active"}).sort("slug", ASCENDING).limit(limit)
            docs = await cursor.to_list(length=limit)
        return [t for doc in docs if (t := _load(doc)) is not None]

    # ------------------------------------------------------------------
    # Ensure indexes (called from migration 0003)
    # ------------------------------------------------------------------

    @classmethod
    def ensure_indexes_sync(cls, db: Any) -> None:  # db: pymongo.Database
        """Create the unique slug index (sync, called from migrations only)."""
        db[TENANTS_COLLECTION].create_index(
            [("slug", ASCENDING)],
            unique=True,
            name="ix_tenants_slug_unique",
        )
        db[TENANTS_COLLECTION].create_index(
            [("status", ASCENDING)],
            name="ix_tenants_status",
        )


def _load(doc: dict[str, Any] | None) -> Tenant | None:
    if doc is None:
        return None
    return Tenant.model_validate(doc)


__all__ = ["TENANTS_COLLECTION", "TenantStore"]
