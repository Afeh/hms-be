"""Tests for tenant records, profile endpoint, and seed script (issue 0010)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from pymongo.errors import DuplicateKeyError
from scripts.seed import seed

from app.core import Settings
from app.core.context import RequestContext, reset_context, set_context
from app.core.errors import DuplicateEntityError
from app.db.tenants import TenantStore
from app.main import create_app
from app.models.tenant import Tenant
from app.services.tenants import tenant_to_profile


def _make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mongodb_uri": "mongodb://127.0.0.1:1",
        "mongodb_server_selection_timeout_ms": 250,
        "redis_url": "redis://127.0.0.1:1/0",
    }
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Tenant model
# ---------------------------------------------------------------------------


def test_tenant_model_defaults() -> None:
    t = Tenant(name="Acme Hospital", slug="acme")
    assert t.name == "Acme Hospital"
    assert t.slug == "acme"
    assert t.status == "active"
    assert t.plan == "starter"
    assert t.timezone == "UTC"
    assert t.database.mode == "shared"
    assert not t.legal_hold


def test_tenant_to_profile() -> None:
    t = Tenant(
        id="t_123",
        name="St Mary's",
        slug="st-marys",
        code="STM",
        status="active",
        plan="enterprise",
        country="NG",
        timezone="Africa/Lagos",
        legal_hold=False,
    )
    p = tenant_to_profile(t)
    assert p.id == "t_123"
    assert p.name == "St Mary's"
    assert p.slug == "st-marys"
    assert p.code == "STM"
    assert p.country == "NG"
    assert p.timezone == "Africa/Lagos"


# ---------------------------------------------------------------------------
# TenantStore unit tests (mocked db)
# ---------------------------------------------------------------------------


async def test_tenant_store_create_success() -> None:
    registry = MagicMock()
    col = AsyncMock()
    registry.database = {"tenants": col}

    col.insert_one.return_value = MagicMock(inserted_id="66a01234567890abcdef1234")

    store = TenantStore(registry)
    tenant = Tenant(name="Acme", slug="acme")
    created = await store.create(tenant)

    assert created.id == "66a01234567890abcdef1234"
    col.insert_one.assert_called_once()


async def test_tenant_store_create_duplicate_slug_raises_duplicate_entity_error() -> None:
    registry = MagicMock()
    col = AsyncMock()
    registry.database = {"tenants": col}

    col.insert_one.side_effect = DuplicateKeyError("E11000 duplicate key error: slug_1")

    store = TenantStore(registry)
    tenant = Tenant(name="Acme", slug="acme")

    with pytest.raises(DuplicateEntityError):
        await store.create(tenant)


async def test_tenant_store_get_by_id_and_slug() -> None:
    registry = MagicMock()
    col = AsyncMock()
    registry.database = {"tenants": col}

    col.find_one.return_value = {
        "_id": "t_abc",
        "name": "Acme",
        "slug": "acme",
        "status": "active",
        "plan": "starter",
        "timezone": "UTC",
        "legal_hold": False,
    }

    store = TenantStore(registry)
    t = await store.get_by_id("t_abc")
    assert t is not None
    assert t.id == "t_abc"
    assert t.name == "Acme"

    s = await store.get_by_slug("acme")
    assert s is not None
    assert s.slug == "acme"


async def test_tenant_store_get_active_by_id() -> None:
    registry = MagicMock()
    col = AsyncMock()
    registry.database = {"tenants": col}

    # Suspended tenant returns None for get_active_by_id
    col.find_one.return_value = None
    store = TenantStore(registry)
    t = await store.get_active_by_id("t_suspended")
    assert t is None


# ---------------------------------------------------------------------------
# GET /tenant endpoint
# ---------------------------------------------------------------------------


async def test_get_tenant_unauthenticated_returns_401() -> None:
    settings = _make_settings()
    app = create_app(settings)

    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.get("/api/v1/tenant")
        assert r.status_code == 401
        assert r.json()["data"]["code"] == "unauthenticated"


async def test_get_tenant_forbidden_without_permission() -> None:
    settings = _make_settings()
    app = create_app(settings)

    token = set_context(
        RequestContext(
            tenant_id="t_acme",
            request_id="req_test",
            permissions=frozenset(["patient:read"]),  # missing tenant:configure
        )
    )
    try:
        async with (
            LifespanManager(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            r = await client.get("/api/v1/tenant")
            assert r.status_code == 403
            assert r.json()["data"]["code"] == "forbidden"
    finally:
        reset_context(token)


async def test_get_tenant_returns_current_tenant_profile() -> None:
    settings = _make_settings()
    app = create_app(settings)

    fake_tenant = Tenant(
        id="t_acme",
        name="Acme Hospital",
        slug="acme",
        status="active",
        plan="enterprise",
        country="US",
        timezone="America/New_York",
        legal_hold=False,
    )

    fake_store = AsyncMock()
    fake_store.get_by_id.return_value = fake_tenant
    fake_store.get_by_slug.return_value = fake_tenant
    from app.api.deps import get_tenant_store

    app.dependency_overrides[get_tenant_store] = lambda: fake_store

    token = set_context(
        RequestContext(
            tenant_id="t_acme",
            request_id="req_test",
            permissions=frozenset(["tenant:configure"]),
        )
    )
    try:
        async with (
            LifespanManager(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            r = await client.get("/api/v1/tenant")
            assert r.status_code == 200
            data = r.json()
            assert data["message"] == "Hospital retrieved successfully"
            tenant_data = data["data"]["tenant"]
            assert tenant_data["_id"] == "t_acme"
            assert tenant_data["slug"] == "acme"
            assert tenant_data["name"] == "Acme Hospital"
            assert tenant_data["timezone"] == "America/New_York"
    finally:
        reset_context(token)


async def test_get_tenant_returns_beta_profile_for_beta_token() -> None:
    settings = _make_settings()
    app = create_app(settings)

    fake_beta = Tenant(
        id="t_beta",
        name="Beta Clinic",
        slug="beta",
        status="active",
        plan="standard",
        country="GB",
        timezone="Europe/London",
        legal_hold=False,
    )

    fake_store = AsyncMock()
    fake_store.get_by_id.return_value = fake_beta
    fake_store.get_by_slug.return_value = fake_beta
    from app.api.deps import get_tenant_store

    app.dependency_overrides[get_tenant_store] = lambda: fake_store

    # Test via Authorization Bearer token as required by acceptance criteria
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.get("/api/v1/tenant", headers={"Authorization": "Bearer beta"})
        assert r.status_code == 200
        tenant_data = r.json()["data"]["tenant"]
        assert tenant_data["_id"] == "t_beta"
        assert tenant_data["slug"] == "beta"


# ---------------------------------------------------------------------------
# Seed script safety
# ---------------------------------------------------------------------------


def test_seed_refuses_to_run_in_production() -> None:
    with patch("scripts.seed.get_settings", return_value=Settings(environment="production")):
        code = seed()
        assert code == 1
