"""Session and refresh-token lifecycle (plan §5.1, issue 0013).

A login creates a session and its first refresh token in one MongoDB transaction.
Refresh rotation marks the presented row used and inserts the replacement with the
same family; a replay revokes the family. Access tokens are issued here so login
and refresh return exactly the same token shape.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import (
    AccessTokenClaims,
    encode_access_token,
    generate_opaque_token,
    hash_opaque_token,
)
from app.db.client import TenantConnectionRegistry
from app.db.sessions import SessionStore
from app.db.users import MembershipStore
from app.models.refresh_token import RefreshToken
from app.models.session import RevokedReason, Session


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """The token bundle returned after login or refresh."""

    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    session_id: str
    tenant_id: str
    user_id: str


async def create_session(
    registry: TenantConnectionRegistry,
    settings: Settings,
    *,
    tenant_id: str,
    user_id: str,
    identity_id: str,
    perm_version: int,
    mfa: bool = False,
    device_label: str | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
    now: datetime | None = None,
) -> IssuedSession:
    """Create a session and its first refresh token atomically."""
    issued_at = now or datetime.now(UTC)
    idle_expires_at = issued_at + timedelta(seconds=settings.session_idle_timeout_seconds)
    absolute_expires_at = issued_at + timedelta(seconds=settings.session_absolute_timeout_seconds)
    refresh_expires_at = min(
        issued_at + timedelta(seconds=settings.refresh_token_ttl_seconds),
        absolute_expires_at,
    )
    raw_refresh_token = generate_opaque_token()

    session = Session(
        tenant_id=tenant_id,
        user_id=user_id,
        identity_id=identity_id,
        family_id=uuid.uuid4().hex,
        device_label=device_label,
        user_agent=user_agent,
        ip=ip,
        created_at=issued_at,
        last_seen_at=issued_at,
        idle_expires_at=idle_expires_at,
        absolute_expires_at=absolute_expires_at,
    )

    store = SessionStore(registry)
    async with (
        await registry.client.start_session() as db_session,
        db_session.start_transaction(),
    ):
        session_id = await store.insert_session(session, db_session=db_session)
        token = RefreshToken(
            tenant_id=tenant_id,
            session_id=session_id,
            family_id=session.family_id,
            token_hash=hash_opaque_token(raw_refresh_token),
            created_at=issued_at,
            expires_at=refresh_expires_at,
        )
        await store.insert_refresh_token(token, db_session=db_session)

    access_token, expires_in = encode_access_token(
        settings,
        AccessTokenClaims(
            identity_id=identity_id,
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            perm_version=perm_version,
            mfa=mfa,
        ),
    )
    return IssuedSession(
        access_token=access_token,
        expires_in=expires_in,
        refresh_token=raw_refresh_token,
        refresh_expires_at=refresh_expires_at,
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )


async def rotate_refresh_token(
    registry: TenantConnectionRegistry,
    settings: Settings,
    raw_refresh_token: str,
    *,
    now: datetime | None = None,
) -> IssuedSession:
    """Rotate a refresh token exactly once, or revoke its family on a replay."""
    rotated_at = now or datetime.now(UTC)
    store = SessionStore(registry)
    presented = await store.find_refresh_token(hash_opaque_token(raw_refresh_token))
    if presented is None:
        raise ApiError(401, "invalid_or_expired_token", "Refresh token is invalid or expired")
    if presented.used_at is not None:
        await store.revoke_family(
            presented.tenant_id,
            presented.family_id,
            reason="reuse_detected",
            now=rotated_at,
        )
        raise ApiError(401, "refresh_reuse_detected", "Refresh token reuse detected")
    if presented.expires_at <= rotated_at:
        raise ApiError(401, "invalid_or_expired_token", "Refresh token is invalid or expired")

    session = await store.get(presented.tenant_id, presented.session_id)
    if session is None or not session.is_active_at(rotated_at):
        raise ApiError(401, "session_expired", "Session has expired")

    user = await MembershipStore(registry).get(presented.tenant_id, session.user_id)
    if user is None or user.status != "active":
        raise ApiError(403, "account_deactivated", "This account is not active")

    replacement = generate_opaque_token()
    replacement_hash = hash_opaque_token(replacement)
    refresh_expires_at = min(
        rotated_at + timedelta(seconds=settings.refresh_token_ttl_seconds),
        session.absolute_expires_at,
    )
    replacement_row = RefreshToken(
        tenant_id=presented.tenant_id,
        session_id=session.id or presented.session_id,
        family_id=presented.family_id,
        token_hash=replacement_hash,
        created_at=rotated_at,
        expires_at=refresh_expires_at,
    )

    claimed = await store.rotate(presented, replacement_row, now=rotated_at)

    if not claimed:
        await store.revoke_family(
            presented.tenant_id,
            presented.family_id,
            reason="reuse_detected",
            now=rotated_at,
        )
        raise ApiError(401, "refresh_reuse_detected", "Refresh token reuse detected")

    access_token, expires_in = encode_access_token(
        settings,
        AccessTokenClaims(
            identity_id=session.identity_id,
            user_id=session.user_id,
            tenant_id=session.tenant_id,
            session_id=session.id or presented.session_id,
            perm_version=user.perm_version,
            mfa=False,
        ),
    )
    return IssuedSession(
        access_token=access_token,
        expires_in=expires_in,
        refresh_token=replacement,
        refresh_expires_at=refresh_expires_at,
        session_id=session.id or presented.session_id,
        tenant_id=presented.tenant_id,
        user_id=session.user_id,
    )


async def revoke_session(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    session_id: str,
    *,
    reason: RevokedReason = "logout",
    now: datetime | None = None,
) -> bool:
    return await SessionStore(registry).revoke(tenant_id, session_id, reason=reason, now=now)


async def revoke_all_for_identity(
    registry: TenantConnectionRegistry,
    identity_id: str,
    *,
    reason: RevokedReason = "password_change",
    now: datetime | None = None,
) -> int:
    return await SessionStore(registry).revoke_all_for_identity(identity_id, reason=reason, now=now)


async def touch_session_if_stale(
    registry: TenantConnectionRegistry,
    session: Session,
    *,
    now: datetime | None = None,
) -> None:
    """Write ``last_seen_at`` at most once a minute."""
    touched_at = now or datetime.now(UTC)
    if (touched_at - session.last_seen_at).total_seconds() < 60:
        return
    if session.id is None:
        return
    await SessionStore(registry).touch(session.tenant_id, session.id, now=touched_at)


__all__ = [
    "IssuedSession",
    "create_session",
    "revoke_all_for_identity",
    "revoke_session",
    "rotate_refresh_token",
    "touch_session_if_stale",
]
