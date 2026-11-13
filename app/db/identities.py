"""Access to the global ``identities`` collection (plan §6.3, issue 0012).

The collection is exempt from the tenant guard. Every method here uses an explicit
identity ID or the normalised email; there is no ambient tenant scope to rely on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from app.core.errors import DuplicateEntityError
from app.db.client import TenantConnectionRegistry
from app.db.indexes import IDENTITIES_COLLECTION
from app.models.identity import Identity


def normalize_email(email: str) -> str:
    """Case-fold and trim an email for the global unique index."""
    return email.strip().lower()


class IdentityRepository:
    """Explicit lookups over global identities."""

    def __init__(self, registry: TenantConnectionRegistry) -> None:
        self._collection = registry.database[IDENTITIES_COLLECTION]

    async def get(self, identity_id: str) -> Identity | None:
        object_id = _object_id(identity_id)
        if object_id is None:
            return None
        document = await self._collection.find_one({"_id": object_id})
        return None if document is None else Identity.model_validate(document)

    async def find_by_email(self, email: str) -> Identity | None:
        """The single query login uses, keyed by the normalised email."""
        document = await self._collection.find_one({"email_normalized": normalize_email(email)})
        return None if document is None else Identity.model_validate(document)

    async def create(self, identity: Identity) -> Identity:
        try:
            result = await self._collection.insert_one(
                identity.model_dump(by_alias=True, exclude_none=True)
            )
        except DuplicateKeyError as exc:
            raise DuplicateEntityError("an identity with this email already exists") from exc
        return identity.model_copy(update={"id": str(result.inserted_id)})

    async def update(self, identity_id: str, changes: dict[str, Any]) -> Identity | None:
        object_id = _object_id(identity_id)
        if object_id is None:
            return None
        try:
            document = await self._collection.find_one_and_update(
                {"_id": object_id},
                {"$set": {**changes, "updated_at": datetime.now(UTC)}},
                return_document=True,
            )
        except DuplicateKeyError as exc:
            raise DuplicateEntityError("an identity with this email already exists") from exc
        return None if document is None else Identity.model_validate(document)

    async def mark_login(self, identity_id: str, *, now: datetime | None = None) -> None:
        """Record a successful login and clear stale failure counters."""
        object_id = _object_id(identity_id)
        if object_id is None:
            return
        await self._collection.update_one(
            {"_id": object_id},
            {
                "$set": {
                    "last_login_at": now or datetime.now(UTC),
                    "failed_login_count": 0,
                    "updated_at": datetime.now(UTC),
                },
                "$unset": {"locked_until": ""},
            },
        )


def _object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


__all__ = ["IdentityRepository", "normalize_email"]
