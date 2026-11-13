"""Session and refresh-token access (plan §6.3, issue 0012).

Session creation and refresh rotation happen before or outside a tenant request
context, so ``SessionStore`` takes the tenant explicitly and uses system access
with a fixed reason. Reads made while serving an authenticated request go through
``SessionRepository``, which is tenant-scoped by the collection guard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import OperationFailure

from app.db.client import TenantConnectionRegistry
from app.db.indexes import REFRESH_TOKENS_COLLECTION, SESSIONS_COLLECTION
from app.db.repository import TenantRepository
from app.db.tenancy import run_as_system
from app.models.refresh_token import RefreshToken
from app.models.session import RevokedReason, Session

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorClientSession

WRITE_CONFLICT_CODES = frozenset({112, 251})


class SessionRepository(TenantRepository[Session]):
    """The current tenant's sessions."""

    model = Session


class SessionStore:
    """Explicit-tenant session and refresh-token access for authentication."""

    def __init__(self, registry: TenantConnectionRegistry) -> None:
        self._sessions = registry.database[SESSIONS_COLLECTION]
        self._tokens = registry.database[REFRESH_TOKENS_COLLECTION]

    async def get(self, tenant_id: str, session_id: str) -> Session | None:
        object_id = _object_id(session_id)
        if object_id is None:
            return None
        document = await self._sessions.find_one(
            {"_id": object_id, "tenant_id": tenant_id, "deleted_at": None}
        )
        return None if document is None else Session.model_validate(document)

    async def insert_session(
        self,
        session: Session,
        *,
        db_session: AsyncIOMotorClientSession | None = None,
    ) -> str:
        """Insert a session and return its ID (inside the caller's transaction)."""
        document = session.model_dump(by_alias=True, exclude_none=True)
        document["tenant_id"] = session.tenant_id
        result = await self._sessions.insert_one(document, session=db_session)
        return str(result.inserted_id)

    async def insert_refresh_token(
        self,
        token: RefreshToken,
        *,
        db_session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        document = token.model_dump(by_alias=True, exclude_none=True)
        document["tenant_id"] = token.tenant_id
        await self._tokens.insert_one(document, session=db_session)

    async def find_refresh_token(self, token_hash: str) -> RefreshToken | None:
        document = await self._tokens.find_one({"token_hash": token_hash})
        return None if document is None else RefreshToken.model_validate(document)

    async def claim_refresh_token(
        self,
        token: RefreshToken,
        *,
        now: datetime,
        replaced_by_hash: str,
        db_session: AsyncIOMotorClientSession | None = None,
    ) -> bool:
        """Mark a token used exactly once. A second claimant gets ``False``."""
        object_id = _object_id(token.id or "")
        if object_id is None:
            return False
        result = await self._tokens.update_one(
            {"_id": object_id, "used_at": None},
            {"$set": {"used_at": now, "replaced_by_hash": replaced_by_hash}},
            session=db_session,
        )
        return result.modified_count == 1

    async def rotate(
        self,
        presented: RefreshToken,
        replacement: RefreshToken,
        *,
        now: datetime,
    ) -> bool:
        """Claim the presented token and insert its replacement atomically.

        Returns ``False`` when another refresh won the race (or a write conflict
        aborted this attempt), which the caller treats as reuse.
        """
        try:
            async with (
                await self._sessions.database.client.start_session() as db_session,
                db_session.start_transaction(),
            ):
                claimed = await self.claim_refresh_token(
                    presented,
                    now=now,
                    replaced_by_hash=replacement.token_hash,
                    db_session=db_session,
                )
                if claimed:
                    await self.insert_refresh_token(replacement, db_session=db_session)
                return claimed
        except OperationFailure as exc:
            if exc.code in WRITE_CONFLICT_CODES:
                return False
            raise

    async def revoke(
        self,
        tenant_id: str,
        session_id: str,
        *,
        reason: RevokedReason,
        now: datetime | None = None,
    ) -> bool:
        object_id = _object_id(session_id)
        if object_id is None:
            return False
        result = await self._sessions.update_one(
            {"_id": object_id, "tenant_id": tenant_id, "revoked_at": None},
            {"$set": {"revoked_at": now or datetime.now(UTC), "revoked_reason": reason}},
        )
        return result.modified_count == 1

    async def revoke_family(
        self,
        tenant_id: str,
        family_id: str,
        *,
        reason: RevokedReason,
        now: datetime | None = None,
    ) -> int:
        result = await self._sessions.update_many(
            {"tenant_id": tenant_id, "family_id": family_id, "revoked_at": None},
            {"$set": {"revoked_at": now or datetime.now(UTC), "revoked_reason": reason}},
        )
        return result.modified_count

    async def revoke_all_for_identity(
        self, identity_id: str, *, reason: RevokedReason, now: datetime | None = None
    ) -> int:
        with run_as_system("auth: revoke every session for an identity"):
            result = await self._sessions.update_many(
                {"identity_id": identity_id, "revoked_at": None},
                {"$set": {"revoked_at": now or datetime.now(UTC), "revoked_reason": reason}},
            )
        return result.modified_count

    async def touch(self, tenant_id: str, session_id: str, *, now: datetime | None = None) -> None:
        """Refresh ``last_seen_at``; callers throttle this to once a minute."""
        object_id = _object_id(session_id)
        if object_id is None:
            return
        await self._sessions.update_one(
            {"_id": object_id, "tenant_id": tenant_id},
            {"$set": {"last_seen_at": now or datetime.now(UTC)}},
        )


def _object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


__all__ = ["SessionRepository", "SessionStore"]
