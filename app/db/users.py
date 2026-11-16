"""Access to tenant memberships in ``users`` (plan §6.3, issue 0012).

``UserRepository`` is the normal tenant-scoped repository. ``MembershipStore`` is
the small bootstrap surface login needs before a tenant is known: it resolves the
single active membership for an identity under an explicit ``run_as_system``
reason, and always filters by the identity ID.
"""

from __future__ import annotations

from datetime import UTC, datetime

from bson import ObjectId
from bson.errors import InvalidId

from app.db.client import TenantConnectionRegistry
from app.db.indexes import USERS_COLLECTION
from app.db.repository import TenantRepository
from app.db.tenancy import run_as_system
from app.models.user import User


class UserRepository(TenantRepository[User]):
    """A tenant's membership collection."""

    model = User

    async def find_by_identity(self, identity_id: str) -> User | None:
        return await self.find_one({"identity_id": identity_id})


class MembershipStore:
    """Cross-tenant membership resolution for authentication bootstrap."""

    def __init__(self, registry: TenantConnectionRegistry) -> None:
        self._collection = registry.database[USERS_COLLECTION]

    async def get(self, tenant_id: str, user_id: str) -> User | None:
        object_id = _object_id(user_id)
        if object_id is None:
            return None
        document = await self._collection.find_one(
            {"_id": object_id, "tenant_id": tenant_id, "deleted_at": None}
        )
        return None if document is None else User.model_validate(document)

    async def find_active_by_identity(self, identity_id: str) -> User | None:
        """The identity's one active membership, if any.

        Login runs before a tenant exists in context, so this is the one deliberate
        system-access read. The result always carries its own ``tenant_id``.
        """
        with run_as_system("auth: resolve the active membership for an identity"):
            documents = [
                document
                async for document in self._collection.find(
                    {
                        "identity_id": identity_id,
                        "status": "active",
                        "deleted_at": None,
                    }
                ).limit(2)
            ]
        if not documents:
            return None
        return User.model_validate(documents[0])

    async def find_by_identity_in_tenant(self, tenant_id: str, identity_id: str) -> User | None:
        document = await self._collection.find_one(
            {"tenant_id": tenant_id, "identity_id": identity_id, "deleted_at": None}
        )
        return None if document is None else User.model_validate(document)

    async def mark_login(
        self, tenant_id: str, user_id: str, *, now: datetime | None = None
    ) -> None:
        object_id = _object_id(user_id)
        if object_id is None:
            return
        await self._collection.update_one(
            {"_id": object_id, "tenant_id": tenant_id},
            {"$set": {"last_login_at": now or datetime.now(UTC), "updated_at": datetime.now(UTC)}},
        )


def _object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


__all__: list[str] = ["MembershipStore", "UserRepository"]
