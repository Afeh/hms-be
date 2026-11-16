"""Login and profile routes (issues 0014/0016), against a replica-set MongoDB."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from asgi_lifespan import LifespanManager
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.core import Settings
from app.core.cookies import ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE
from app.core.security import hash_password
from app.db import TenantConnectionRegistry
from app.db.identities import IdentityRepository, normalize_email
from app.db.indexes import create_indexes
from app.main import create_app
from app.models.identity import Identity, PersonName

MONGODB_URI = os.environ.get("MONGODB_URI")
PASSWORD = "Password123!"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(MONGODB_URI is None, reason="set MONGODB_URI to run auth integration tests"),
]


@pytest.fixture
async def registry() -> AsyncIterator[TenantConnectionRegistry]:
    database = f"hms_test_login_{uuid.uuid4().hex[:8]}"
    connection = TenantConnectionRegistry(MONGODB_URI or "", database, timeout_ms=2000)
    await create_indexes(connection.database)
    try:
        yield connection
    finally:
        await connection.client.drop_database(database)
        await connection.close()


def _settings(database: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "mongodb_uri": MONGODB_URI or "",
        "mongodb_database": database,
        "mongodb_server_selection_timeout_ms": 2000,
        "redis_url": "redis://127.0.0.1:1/0",
    }
    values.update(overrides)
    return Settings(**values)


async def _account(
    registry: TenantConnectionRegistry,
    *,
    permissions: list[str] | None = None,
    tenant_status: str = "active",
    membership_status: str = "active",
    identity_status: str = "active",
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
            status=identity_status,
        )
    )
    role_result = await db["roles"].insert_one(
        {
            "tenant_id": str(tenant_id),
            "key": "test_role",
            "name": "Test Role",
            "permissions": permissions or ["patient:read", "patient:update"],
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
            "display_name": "Dr Ada",
            "created_at": now,
            "version": 1,
        }
    )
    return {
        "tenant_id": str(tenant_id),
        "identity_id": identity.id or "",
        "user_id": str(user_result.inserted_id),
        "role_id": str(role_result.inserted_id),
        "email": email,
    }


async def _run(
    registry: TenantConnectionRegistry,
    *,
    rate_limit_login_ip: int = 10,
) -> AsyncIterator[tuple[AsyncClient, Any]]:
    settings = _settings(registry.database.name, rate_limit_login_ip=rate_limit_login_ip)
    app = create_app(settings)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client, app


async def test_login_returns_tokens_and_me_returns_permissions(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client, _ in _run(registry):
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": account["email"].upper(),
                "password": PASSWORD,
                "device_label": "Pixel 8, Chrome",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["mfa_required"] is False
        assert data["user"]["_id"] == account["user_id"]
        assert "password_hash" not in data["user"]
        assert data["user"]["email"] == account["email"]
        assert data["user"]["tenant_id"] == account["tenant_id"]
        assert data["user"]["roles"][0]["key"] == "test_role"
        assert data["user"]["permissions"] == ["patient:read", "patient:update"]
        assert data["expires_in"] > 0
        assert data["refresh_token"]
        assert data["refresh_expires_at"]

        headers = {"Authorization": f"Bearer {data['access_token']}"}
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        profile = me.json()["data"]
        assert profile["user"]["_id"] == account["user_id"]
        assert profile["user"]["email"] == account["email"]
        assert profile["user"]["tenant_id"] == account["tenant_id"]
        assert profile["user"]["display_name"] == "Dr Ada"
        assert profile["permissions"] == ["patient:read", "patient:update"]
        assert profile["roles"][0]["_id"] == account["role_id"]
        assert profile["roles"][0]["mfa_required"] is False
        assert profile["tenant"]["_id"] == account["tenant_id"]
        assert profile["perm_version"] == 1


async def test_login_sets_http_only_cookies_and_cookie_authentication_works(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client, _ in _run(registry):
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": PASSWORD},
        )
        assert login.status_code == 200, login.text

        cookies = login.headers.get_list("set-cookie")
        assert len(cookies) == 2
        access_cookie = next(c for c in cookies if c.startswith(f"{ACCESS_TOKEN_COOKIE}="))
        refresh_cookie = next(c for c in cookies if c.startswith(f"{REFRESH_TOKEN_COOKIE}="))
        for cookie in (access_cookie, refresh_cookie):
            lowered = cookie.lower()
            assert "httponly" in lowered
            assert "path=/" in lowered
            assert "samesite=lax" in lowered
        assert "max-age=600" in access_cookie.lower()
        lowered_refresh = refresh_cookie.lower()
        assert "expires=" in lowered_refresh
        refresh_max_age = int(lowered_refresh.split("max-age=")[1].split(";")[0])
        assert refresh_max_age > 600
        assert client.cookies.get(ACCESS_TOKEN_COOKIE)
        assert client.cookies.get(REFRESH_TOKEN_COOKIE)

        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["data"]["user"]["_id"] == account["user_id"]


async def test_refresh_rotates_the_cookie_pair_and_rejects_the_old_token(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client, _ in _run(registry):
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        original = login.json()["data"]

        refreshed = await client.post("/api/v1/auth/refresh")
        assert refreshed.status_code == 200, refreshed.text
        rotated = refreshed.json()["data"]
        assert rotated["refresh_token"] != original["refresh_token"]
        assert len(refreshed.headers.get_list("set-cookie")) == 2

        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200, me.text

        replay = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": original["refresh_token"]},
        )
        assert replay.status_code == 401
        assert replay.json()["data"]["code"] == "refresh_reuse_detected"

        revoked = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {rotated['access_token']}"},
        )
        assert revoked.status_code == 401
        assert revoked.json()["data"]["code"] == "session_expired"


async def test_logout_revokes_the_session_and_clears_both_cookies(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client, _ in _run(registry):
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        access_token = login.json()["data"]["access_token"]

        logout = await client.post("/api/v1/auth/logout")
        assert logout.status_code == 200, logout.text
        assert logout.json()["data"]["signed_out"] is True
        cleared = logout.headers.get_list("set-cookie")
        assert len(cleared) == 2
        assert all("max-age=0" in cookie.lower() for cookie in cleared)
        assert client.cookies.get(ACCESS_TOKEN_COOKIE) is None
        assert client.cookies.get(REFRESH_TOKEN_COOKIE) is None

        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 401
        bearer = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert bearer.status_code == 401
        assert bearer.json()["data"]["code"] == "session_expired"


async def test_me_requires_authentication(
    registry: TenantConnectionRegistry,
) -> None:
    async for client, _ in _run(registry):
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert response.json()["data"]["code"] == "unauthenticated"


async def test_login_uses_one_generic_error_for_unknown_email_and_wrong_password(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client, _ in _run(registry):
        wrong = await client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": "WrongPassword123!"},
        )
        unknown = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": PASSWORD},
        )

        assert wrong.status_code == 401
        assert unknown.status_code == 401
        assert wrong.json()["data"]["code"] == "invalid_credentials"
        assert unknown.json()["data"]["code"] == "invalid_credentials"
        assert wrong.json()["message"] == unknown.json()["message"]


async def test_login_blocks_suspended_tenant_and_deactivated_membership(
    registry: TenantConnectionRegistry,
) -> None:
    suspended = await _account(registry, tenant_status="suspended")
    deactivated = await _account(registry, membership_status="deactivated")
    disabled = await _account(registry, identity_status="disabled")
    async for client, _ in _run(registry):
        for account, status, code in (
            (suspended, 403, "tenant_suspended"),
            (deactivated, 403, "account_deactivated"),
            (disabled, 401, "invalid_credentials"),
        ):
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": account["email"], "password": PASSWORD},
            )
            assert response.status_code == status, response.text
            assert response.json()["data"]["code"] == code


async def test_login_is_rate_limited_per_ip(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client, app in _run(registry, rate_limit_login_ip=1):
        calls = 0

        async def fake_incr(key: str) -> int:
            nonlocal calls
            calls += 1
            return calls

        fake_redis = AsyncMock()
        fake_redis.incr = fake_incr
        fake_redis.expire = AsyncMock()
        original = app.state.redis
        app.state.redis = fake_redis
        try:
            first = await client.post(
                "/api/v1/auth/login",
                json={"email": account["email"], "password": PASSWORD},
            )
            second = await client.post(
                "/api/v1/auth/login",
                json={"email": account["email"], "password": PASSWORD},
            )
        finally:
            app.state.redis = original

        assert first.status_code == 200, first.text
        assert second.status_code == 429
        assert second.json()["data"]["code"] == "rate_limited"
        assert "retry-after" in second.headers


async def test_seeded_admin_can_login_and_read_profile(
    registry: TenantConnectionRegistry,
) -> None:
    from scripts.seed import seed

    settings = _settings(registry.database.name)
    with patch("scripts.seed.get_settings", return_value=settings):
        assert seed() == 0

    async for client, _ in _run(registry):
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@acme.example", "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        data = login.json()["data"]
        assert data["user"]["roles"][0]["key"] == "hospital_admin"

        me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert me.status_code == 200, me.text
        profile = me.json()["data"]
        assert profile["tenant"]["name"] == "Acme Hospital"
        assert {
            "patient:create",
            "patient:read",
            "visit:create",
            "vitals:write",
            "visit:close",
        }.issubset(profile["permissions"])


async def test_login_records_last_login(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client, _ in _run(registry):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": PASSWORD},
        )
        assert response.status_code == 200, response.text

    identity = await registry.database["identities"].find_one(
        {"_id": ObjectId(account["identity_id"])}
    )
    membership = await registry.database["users"].find_one({"_id": ObjectId(account["user_id"])})
    assert identity is not None
    assert identity["last_login_at"] is not None
    assert membership is not None
    assert membership["last_login_at"] is not None
