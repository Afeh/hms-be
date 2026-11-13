"""Identity/access integration: indexes, rotation and bearer-token resolution.

Needs a MongoDB replica set because login-style session creation uses a
transaction. Run with ``MONGODB_URI=mongodb://127.0.0.1:27018/?replicaSet=rs0``.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from unittest.mock import patch

import pytest
from asgi_lifespan import LifespanManager
from bson import ObjectId
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from pymongo.errors import DuplicateKeyError

from app.api.deps import CurrentUser, require_permission
from app.core import Settings
from app.core.errors import ApiError, DuplicateEntityError
from app.core.security import (
    decode_access_token,
    hash_opaque_token,
    hash_password,
    verify_password,
)
from app.db import TenantConnectionRegistry
from app.db.identities import IdentityRepository, normalize_email
from app.db.indexes import create_indexes
from app.main import create_app
from app.models.identity import Identity, PersonName
from app.models.user import User
from app.services.sessions import create_session, revoke_session, rotate_refresh_token

MONGODB_URI = os.environ.get("MONGODB_URI")
PASSWORD = "Password123!"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(MONGODB_URI is None, reason="set MONGODB_URI to run auth integration tests"),
]


@pytest.fixture
async def registry() -> AsyncIterator[TenantConnectionRegistry]:
    database = f"hms_test_auth_{uuid.uuid4().hex[:8]}"
    connection = TenantConnectionRegistry(MONGODB_URI or "", database, timeout_ms=2000)
    await create_indexes(connection.database)
    try:
        yield connection
    finally:
        await connection.client.drop_database(database)
        await connection.close()


async def _account(
    registry: TenantConnectionRegistry,
    *,
    permissions: list[str] | None = None,
    tenant_status: str = "active",
    membership_status: str = "active",
) -> dict[str, str]:
    now = datetime.now(UTC)
    db = registry.database
    tenant_id = ObjectId()
    await db["tenants"].insert_one(
        {
            "_id": tenant_id,
            "name": "Acme Hospital",
            "slug": f"acme-{uuid.uuid4().hex[:6]}",
            "status": tenant_status,
            "plan": "starter",
            "timezone": "UTC",
            "legal_hold": False,
            "created_at": now,
            "version": 1,
        }
    )
    email = f"ada-{uuid.uuid4().hex[:8]}@example.com"
    identity = await IdentityRepository(registry).create(
        Identity(
            email=email,
            email_normalized=normalize_email(email),
            name=PersonName(given=["Ada"], family="Okafor"),
            password_hash=hash_password(PASSWORD),
            email_verified_at=now,
        )
    )
    role_result = await db["roles"].insert_one(
        {
            "tenant_id": str(tenant_id),
            "key": "test_role",
            "name": "Test Role",
            "permissions": permissions or ["patient:read"],
            "is_system": False,
            "mfa_required": False,
            "created_at": now,
            "version": 1,
        }
    )
    user_result = await db["users"].insert_one(
        {
            "tenant_id": str(tenant_id),
            "identity_id": identity.id,
            "role_ids": [str(role_result.inserted_id)],
            "status": membership_status,
            "perm_version": 1,
            "created_at": now,
            "version": 1,
        }
    )
    return {
        "tenant_id": str(tenant_id),
        "identity_id": identity.id or "",
        "user_id": str(user_result.inserted_id),
        "role_id": str(role_result.inserted_id),
    }


def _settings(database: str) -> Settings:
    return Settings(
        mongodb_uri=MONGODB_URI or "",
        mongodb_database=database,
        mongodb_server_selection_timeout_ms=2000,
        redis_url="redis://127.0.0.1:1/0",
    )


async def test_unique_indexes_and_model_round_trip(
    registry: TenantConnectionRegistry,
) -> None:
    now = datetime.now(UTC)
    db = registry.database
    first = Identity(
        email="ada@example.com",
        email_normalized="ada@example.com",
        name=PersonName(given=["Ada"], family="Okafor"),
        password_hash=hash_password(PASSWORD),
    )
    stored = await IdentityRepository(registry).create(first)

    with pytest.raises(DuplicateEntityError):
        await IdentityRepository(registry).create(
            first.model_copy(update={"id": None, "email": "ADA@example.com"})
        )

    tenant_a, tenant_b = str(ObjectId()), str(ObjectId())
    for tenant_id in (tenant_a, tenant_b):
        await db["users"].insert_one(
            {
                "tenant_id": tenant_id,
                "identity_id": stored.id,
                "role_ids": [str(ObjectId())],
                "status": "active",
                "perm_version": 1,
                "created_at": now,
                "version": 1,
            }
        )

    with pytest.raises(DuplicateKeyError):
        await db["users"].insert_one(
            {
                "tenant_id": tenant_a,
                "identity_id": stored.id,
                "role_ids": [],
                "status": "active",
                "perm_version": 1,
                "created_at": now,
                "version": 1,
            }
        )

    await db["roles"].insert_one(
        {"tenant_id": tenant_a, "key": "doctor", "name": "Doctor", "permissions": []}
    )
    with pytest.raises(DuplicateKeyError):
        await db["roles"].insert_one(
            {"tenant_id": tenant_a, "key": "doctor", "name": "Doctor", "permissions": []}
        )

    raw_user = await db["users"].find_one({"tenant_id": tenant_a})
    assert raw_user is not None
    user = User.model_validate(raw_user)
    assert isinstance(user.role_ids[0], str)
    assert user.identity_id == stored.id

    session_indexes = await db["sessions"].index_information()
    token_indexes = await db["refresh_tokens"].index_information()
    assert session_indexes["ttl_sessions_absolute_expiry"]["expireAfterSeconds"] == 86_400
    assert token_indexes["ttl_refresh_tokens_expiry"]["expireAfterSeconds"] == 86_400


async def test_refresh_rotation_and_replay_revokes_the_family(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    settings = _settings(registry.database.name)
    issued = await create_session(
        registry,
        settings,
        tenant_id=account["tenant_id"],
        user_id=account["user_id"],
        identity_id=account["identity_id"],
        perm_version=1,
    )

    rotated = await rotate_refresh_token(registry, settings, issued.refresh_token)

    assert rotated.refresh_token != issued.refresh_token
    claims = decode_access_token(rotated.access_token, settings)
    assert claims.session_id == issued.session_id
    assert claims.tenant_id == account["tenant_id"]

    token_hashes = [
        document["token_hash"] async for document in registry.database["refresh_tokens"].find({})
    ]
    assert hash_opaque_token(issued.refresh_token) in token_hashes
    assert hash_opaque_token(rotated.refresh_token) in token_hashes

    with pytest.raises(ApiError) as raised:
        await rotate_refresh_token(registry, settings, issued.refresh_token)
    assert raised.value.code == "refresh_reuse_detected"

    session = await registry.database["sessions"].find_one({"_id": ObjectId(issued.session_id)})
    assert session is not None
    assert session["revoked_reason"] == "reuse_detected"

    with pytest.raises(ApiError):
        await rotate_refresh_token(registry, settings, rotated.refresh_token)


async def test_concurrent_refresh_has_one_winner_and_revokes_the_family(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    settings = _settings(registry.database.name)
    issued = await create_session(
        registry,
        settings,
        tenant_id=account["tenant_id"],
        user_id=account["user_id"],
        identity_id=account["identity_id"],
        perm_version=1,
    )

    results = await asyncio.gather(
        rotate_refresh_token(registry, settings, issued.refresh_token),
        rotate_refresh_token(registry, settings, issued.refresh_token),
        return_exceptions=True,
    )
    successes = [result for result in results if not isinstance(result, BaseException)]
    failures = [result for result in results if isinstance(result, ApiError)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code == "refresh_reuse_detected"
    session = await registry.database["sessions"].find_one({"_id": ObjectId(issued.session_id)})
    assert session is not None
    assert session["revoked_reason"] == "reuse_detected"


async def test_bearer_token_populates_context_and_checks_permission(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    settings = _settings(registry.database.name)
    issued = await create_session(
        registry,
        settings,
        tenant_id=account["tenant_id"],
        user_id=account["user_id"],
        identity_id=account["identity_id"],
        perm_version=1,
    )
    app = create_app(settings)

    @app.get("/test/patient")
    async def patient_route(
        user: Annotated[CurrentUser, Depends(require_permission("patient:read"))],
    ) -> dict[str, Any]:
        return {"user_id": user.user.id, "tenant_id": user.tenant.id}

    @app.get("/test/admin")
    async def admin_route(
        user: Annotated[CurrentUser, Depends(require_permission("tenant:configure"))],
    ) -> dict[str, Any]:
        return {"user_id": user.user.id}

    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/test/patient")).status_code == 401

        headers = {"Authorization": f"Bearer {issued.access_token}"}
        allowed = await client.get("/test/patient", headers=headers)
        assert allowed.status_code == 200
        assert allowed.json()["user_id"] == account["user_id"]

        denied = await client.get("/test/admin", headers=headers)
        assert denied.status_code == 403
        assert denied.json()["data"]["code"] == "forbidden"

        await revoke_session(registry, account["tenant_id"], issued.session_id)
        revoked = await client.get("/test/patient", headers=headers)
        assert revoked.status_code == 401
        assert revoked.json()["data"]["code"] == "session_expired"


async def test_suspended_tenant_and_deactivated_membership_are_blocked(
    registry: TenantConnectionRegistry,
) -> None:
    suspended = await _account(registry, tenant_status="suspended")
    deactivated = await _account(registry, membership_status="deactivated")
    settings = _settings(registry.database.name)
    app = create_app(settings)

    @app.get("/test/patient")
    async def patient_route(
        user: Annotated[CurrentUser, Depends(require_permission("patient:read"))],
    ) -> dict[str, Any]:
        return {"user_id": user.user.id}

    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        for account, expected in (
            (suspended, "tenant_suspended"),
            (deactivated, "account_deactivated"),
        ):
            issued = await create_session(
                registry,
                settings,
                tenant_id=account["tenant_id"],
                user_id=account["user_id"],
                identity_id=account["identity_id"],
                perm_version=1,
            )
            response = await client.get(
                "/test/patient", headers={"Authorization": f"Bearer {issued.access_token}"}
            )
            assert response.status_code == 403
            assert response.json()["data"]["code"] == expected


async def test_seed_stores_an_argon2_password(
    registry: TenantConnectionRegistry,
) -> None:
    from scripts.seed import seed

    settings = _settings(registry.database.name)
    with patch("scripts.seed.get_settings", return_value=settings):
        assert seed() == 0

    identity = await IdentityRepository(registry).find_by_email("admin@acme.example")
    assert identity is not None
    assert identity.password_hash is not None
    assert identity.password_hash.startswith("$argon2id$")
    assert verify_password(PASSWORD, identity.password_hash).valid is True

    admin_role = await registry.database["roles"].find_one({"key": "hospital_admin"})
    assert admin_role is not None
    assert "patient:create" in admin_role["permissions"]
    assert "visit:close" in admin_role["permissions"]


def test_expired_session_is_not_active() -> None:
    from app.models.session import Session

    now = datetime.now(UTC)
    session = Session(
        tenant_id="t",
        user_id="u",
        identity_id="i",
        family_id="f",
        idle_expires_at=now - timedelta(seconds=1),
        absolute_expires_at=now + timedelta(hours=1),
    )
    assert session.is_active is False
