"""Motor client registry: one shared connection for Release 1.

Every repository obtains its database here, so moving a tenant to a dedicated
database later is a change in this class only (plan section 4.4).
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.db.tenancy import TenantCollection


class TenantConnectionRegistry:
    """Maps a tenant to its database. All tenants share one in Release 1."""

    def __init__(self, uri: str, database: str, *, timeout_ms: int = 5000) -> None:
        self._client: AsyncIOMotorClient[dict[str, Any]] = AsyncIOMotorClient(
            uri,
            tz_aware=True,
            serverSelectionTimeoutMS=timeout_ms,
        )
        self._database_name = database

    @property
    def client(self) -> AsyncIOMotorClient[dict[str, Any]]:
        return self._client

    def database_for(self, tenant_id: str) -> AsyncIOMotorDatabase[dict[str, Any]]:
        """Resolve the database for a tenant. Dedicated tenants land here later."""
        del tenant_id
        return self._client[self._database_name]

    def collection(self, tenant_id: str, name: str) -> TenantCollection:
        return TenantCollection(self.database_for(tenant_id)[name], name)

    def system_collection(self, name: str) -> TenantCollection:
        """Access a collection for platform tasks and migrations outside a tenant scope."""
        return TenantCollection(self._client[self._database_name][name], name)

    async def ping(self) -> None:
        await self._client.admin.command("ping")

    async def close(self) -> None:
        self._client.close()
