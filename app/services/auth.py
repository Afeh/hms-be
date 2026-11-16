"""Login orchestration (plan §5.1, issue 0014).

The sprint deliberately trims MFA and account lockout: login resolves the global
identity, verifies argon2, resolves the identity's single active membership and
issues a session. Failures before a membership is known are intentionally
indistinguishable to avoid revealing whether an email exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import (
    dummy_password_hash,
    hash_password,
    verify_password,
)
from app.db import (
    IdentityRepository,
    MembershipStore,
    RoleStore,
    TenantConnectionRegistry,
    TenantStore,
)
from app.db.identities import normalize_email
from app.models.identity import Identity
from app.models.role import Role
from app.models.tenant import Tenant
from app.models.user import User
from app.services.audit import record
from app.services.sessions import IssuedSession, create_session

INVALID_CREDENTIALS = "Invalid email or password"


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Everything the route needs to build the login response."""

    issued: IssuedSession
    identity: Identity
    user: User
    tenant: Tenant
    roles: list[Role]


async def login(
    registry: TenantConnectionRegistry,
    settings: Settings,
    *,
    email: str,
    password: str,
    device_label: str | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
    now: datetime | None = None,
) -> LoginResult:
    """Authenticate credentials and create a session."""
    identities = IdentityRepository(registry)
    identity = await identities.find_by_email(normalize_email(email))

    if identity is None:
        # Burn an argon2 verification so an unknown email takes roughly the same
        # time as a known email with a wrong password.
        verify_password(password, dummy_password_hash())
        record("auth.login", outcome="failure", reason="invalid_credentials")
        raise ApiError(401, "invalid_credentials", INVALID_CREDENTIALS)

    password_check = verify_password(password, identity.password_hash or dummy_password_hash())
    if not password_check.valid or identity.status != "active":
        record(
            "auth.login",
            outcome="failure",
            actor_id=identity.id,
            reason="invalid_credentials",
        )
        raise ApiError(401, "invalid_credentials", INVALID_CREDENTIALS)

    identity_id = identity.id
    if identity_id is None:
        raise ApiError(401, "invalid_credentials", INVALID_CREDENTIALS)

    if password_check.needs_rehash:
        updated = await identities.update(identity_id, {"password_hash": hash_password(password)})
        if updated is not None:
            identity = updated

    membership = await MembershipStore(registry).find_active_by_identity(identity_id)
    if membership is None:
        record(
            "auth.login",
            outcome="failure",
            actor_id=identity.id,
            reason="account_deactivated",
        )
        raise ApiError(403, "account_deactivated", "This account is not active")

    tenant = await TenantStore(registry).get_by_id(membership.tenant_id)
    if tenant is None:
        record(
            "auth.login",
            outcome="failure",
            tenant_id=membership.tenant_id,
            actor_id=membership.id or identity.id,
            reason="account_deactivated",
        )
        raise ApiError(403, "account_deactivated", "This account is not active")
    if tenant.status != "active":
        record(
            "auth.login",
            outcome="failure",
            tenant_id=tenant.id,
            actor_id=membership.id or identity.id,
            reason="tenant_suspended",
        )
        raise ApiError(403, "tenant_suspended", "This hospital is not active")

    roles = await RoleStore(registry).list_by_ids(membership.tenant_id, membership.role_ids)
    issued = await create_session(
        registry,
        settings,
        tenant_id=membership.tenant_id,
        user_id=membership.id or "",
        identity_id=identity_id,
        perm_version=membership.perm_version,
        mfa=False,
        device_label=device_label,
        user_agent=user_agent,
        ip=ip,
        now=now,
    )

    await identities.mark_login(identity_id, now=now)
    await MembershipStore(registry).mark_login(membership.tenant_id, membership.id or "", now=now)
    record(
        "auth.login",
        tenant_id=membership.tenant_id,
        actor_id=membership.id,
        reason="success",
    )

    return LoginResult(
        issued=issued,
        identity=identity,
        user=membership,
        tenant=tenant,
        roles=roles,
    )


__all__ = ["INVALID_CREDENTIALS", "LoginResult", "login"]
