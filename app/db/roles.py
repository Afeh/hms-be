"""Access to tenant roles (plan §6.3, issue 0012)."""

from __future__ import annotations

from bson import ObjectId
from bson.errors import InvalidId

from app.db.client import TenantConnectionRegistry
from app.db.indexes import ROLES_COLLECTION
from app.db.repository import TenantRepository
from app.models.role import Role


class RoleRepository(TenantRepository[Role]):
    """A tenant's role collection."""

    model = Role


class RoleStore:
    """Explicit-tenant role reads used while resolving a bearer token."""

    def __init__(self, registry: TenantConnectionRegistry) -> None:
        self._collection = registry.database[ROLES_COLLECTION]

    async def list_by_ids(self, tenant_id: str, role_ids: list[str]) -> list[Role]:
        object_ids = [oid for role_id in role_ids if (oid := _object_id(role_id)) is not None]
        if not object_ids:
            return []
        documents = [
            document
            async for document in self._collection.find(
                {"tenant_id": tenant_id, "_id": {"$in": object_ids}, "deleted_at": None}
            )
        ]
        return [Role.model_validate(document) for document in documents]


def _object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


__all__ = ["RoleRepository", "RoleStore"]
