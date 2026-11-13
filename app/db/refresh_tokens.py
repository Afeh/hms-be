"""Tenant-scoped access to refresh-token history (plan §6.3, issue 0012).

Authentication bootstrap reads tokens through ``SessionStore``; this repository is
the normal tenant-scoped surface for future session administration.
"""

from __future__ import annotations

from app.db.repository import TenantRepository
from app.models.refresh_token import RefreshToken


class RefreshTokenRepository(TenantRepository[RefreshToken]):
    """The current tenant's refresh tokens."""

    model = RefreshToken

    async def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        return await self.find_one({"token_hash": token_hash})


__all__ = ["RefreshTokenRepository"]
