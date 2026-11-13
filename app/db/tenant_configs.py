"""Access layer for the `tenant_configs` collection (issue 0011).

One config document per tenant, versioned for optimistic concurrency.
``get_or_default`` synthesises the default from the model when no document
exists, so callers never see a None.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.core.errors import VersionConflictError
from app.db.client import TenantConnectionRegistry
from app.db.tenancy import TenantCollection, run_as_system
from app.models.tenant_config import TenantConfig, default_config

logger = logging.getLogger(__name__)

TENANT_CONFIGS_COLLECTION = "tenant_configs"


class TenantConfigStore:
    """Read/write access to tenant configuration documents.

    Uses a TenantCollection so the tenant guard keeps documents isolated —
    but the methods bypass the context guard via ``run_as_system`` when called
    from the service layer with an explicit tenant ID, keeping the guard intact
    for route-level calls.
    """

    def __init__(self, registry: TenantConnectionRegistry) -> None:
        self._collection: TenantCollection = registry.system_collection(TENANT_CONFIGS_COLLECTION)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_or_default(self, tenant_id: str) -> TenantConfig:
        """Return the stored config or the defaults when no document exists."""
        with run_as_system(f"TenantConfigStore.get_or_default({tenant_id})"):
            raw = await self._collection.find_one({"tenant_id": tenant_id})
        if raw is None:
            return default_config(tenant_id)
        return TenantConfig.model_validate(raw)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def upsert_default(self, tenant_id: str) -> TenantConfig:
        """Create a default config if one does not already exist.

        Safe to call repeatedly — returns whatever is current.
        """
        with run_as_system(f"TenantConfigStore.upsert_default({tenant_id})"):
            existing = await self._collection.find_one({"tenant_id": tenant_id})
            if existing is not None:
                return TenantConfig.model_validate(existing)
            cfg = default_config(tenant_id)
            doc = cfg.model_dump(by_alias=True, exclude_none=True)
            try:
                await self._collection.insert_one(doc)
            except DuplicateKeyError:
                # Race: another process inserted between our find and insert.
                raw = await self._collection.find_one({"tenant_id": tenant_id})
                return TenantConfig.model_validate(raw) if raw else cfg
        return cfg

    async def update(
        self,
        tenant_id: str,
        patch: Mapping[str, Any],
        expected_version: int,
    ) -> TenantConfig:
        """Deep-merge *patch* into the stored config and bump the version.

        Raises ``VersionConflictError`` when the stored version does not match
        ``expected_version``.
        """
        with run_as_system(f"TenantConfigStore.update({tenant_id})"):
            existing = await self._collection.find_one({"tenant_id": tenant_id})

        if existing is None:
            # First write — only valid when expected_version is 0.
            if expected_version != 0:
                raise VersionConflictError(tenant_id, expected_version)
            current = default_config(tenant_id)
        else:
            current = TenantConfig.model_validate(existing)

        if current.version != expected_version:
            raise VersionConflictError(tenant_id, expected_version)

        # Deep-merge: overlay the patch onto the current dump.
        current_dict = current.model_dump(by_alias=False)
        merged = _deep_merge(current_dict, patch)
        merged["version"] = current.version + 1
        merged["tenant_id"] = tenant_id

        updated = TenantConfig.model_validate(merged)
        updated_doc = updated.model_dump(by_alias=True, exclude_none=True)

        with run_as_system(f"TenantConfigStore.update({tenant_id}) write"):
            doc_to_set = {k: v for k, v in updated_doc.items() if k != "_id"}
            await self._collection.find_one_and_update(
                {"tenant_id": tenant_id},
                {"$set": doc_to_set},
                upsert=True,
                return_document=True,
            )

        return updated

    # ------------------------------------------------------------------
    # Ensure indexes (called from migration 0004)
    # ------------------------------------------------------------------

    @classmethod
    def ensure_indexes_sync(cls, db: Any) -> None:  # db: pymongo.Database
        """Create the unique tenant_id index (sync, for migrations)."""
        db[TENANT_CONFIGS_COLLECTION].create_index(
            [("tenant_id", ASCENDING)],
            unique=True,
            name="ix_tenant_configs_tenant_id_unique",
        )


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively overlay *override* onto *base*. Returns a new dict."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


__all__ = ["TENANT_CONFIGS_COLLECTION", "TenantConfigStore"]
