"""Tests for tenant configuration, cache, and validation (issue 0011)."""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.api.deps import get_redis, get_registry, get_tenant_config_store
from app.core import Settings
from app.core.context import RequestContext, reset_context, set_context
from app.db.tenant_configs import TenantConfigStore
from app.main import create_app
from app.models.tenant_config import (
    AiConfig,
    PatientIdFormat,
    SecurityConfig,
    default_config,
    generate_patient_id,
)
from app.services.tenant_config import (
    get_config,
    update_config,
)


def _make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mongodb_uri": "mongodb://127.0.0.1:1",
        "mongodb_server_selection_timeout_ms": 250,
        "redis_url": "redis://127.0.0.1:1/0",
    }
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Model validation unit tests
# ---------------------------------------------------------------------------


def test_default_config_is_valid() -> None:
    cfg = default_config("t_test")
    assert cfg.tenant_id == "t_test"
    assert cfg.version == 1
    assert cfg.security.idle_timeout_min == 30
    assert cfg.security.absolute_timeout_hours == 8
    assert cfg.security.password_min_length == 8
    assert cfg.patient_id_format.pattern == "{PREFIX}-{RANDOM:5}-{YYYY}"
    assert cfg.patient_id_format.prefix == "ACH"


def test_patient_id_format_valid_patterns_and_generation() -> None:
    # Default pattern: {PREFIX}-{RANDOM:5}-{YYYY}
    p_ach = PatientIdFormat(prefix="ACH")
    assert p_ach.prefix == "ACH"
    assert p_ach.pattern == "{PREFIX}-{RANDOM:5}-{YYYY}"

    # Prefix normalization to uppercase
    p_lower = PatientIdFormat(prefix="bsc")
    assert p_lower.prefix == "BSC"

    # Generation of patient ID for ACH
    id_ach = generate_patient_id(p_ach, registration_year=2026)
    assert re.match(r"^ACH-\d{5}-2026$", id_ach)

    # Generation of patient ID for BSC
    id_bsc = generate_patient_id(p_lower, registration_year=2026)
    assert re.match(r"^BSC-\d{5}-2026$", id_bsc)

    # Supports sequential tokens too
    p_seq = PatientIdFormat(prefix="PAT", pattern="{PREFIX}-{YYYY}-{SEQ:4}")
    assert p_seq.pattern == "{PREFIX}-{YYYY}-{SEQ:4}"
    assert generate_patient_id(p_seq, registration_year=2026, sequence=42) == "PAT-2026-0042"

    # Invalid patterns reject unknown tokens
    with pytest.raises(ValidationError):
        PatientIdFormat(pattern="INVALID-{UNKNOWN_TOKEN}")

    # Invalid prefix rejects non-alphanumeric or out-of-bounds length
    with pytest.raises(ValidationError):
        PatientIdFormat(prefix="X")
    with pytest.raises(ValidationError):
        PatientIdFormat(prefix="WAYTOOLONG")


def test_security_config_bounds() -> None:
    # Idle timeout bounds: 1 to 480
    SecurityConfig(idle_timeout_min=15)
    with pytest.raises(ValidationError):
        SecurityConfig(idle_timeout_min=0)
    with pytest.raises(ValidationError):
        SecurityConfig(idle_timeout_min=500)

    # Password length bounds: 6 to 128
    SecurityConfig(password_min_length=12)
    with pytest.raises(ValidationError):
        SecurityConfig(password_min_length=5)


def test_ai_config_enabled_requires_provider_and_model() -> None:
    # Enabled without model/provider fails
    with pytest.raises(ValidationError):
        AiConfig(enabled=True)

    # Enabled with provider and model succeeds
    ai = AiConfig(enabled=True, provider="anthropic", model="claude-3-5-sonnet")
    assert ai.enabled
    assert ai.provider == "anthropic"


# ---------------------------------------------------------------------------
# Cache strategy and invalidation tests
# ---------------------------------------------------------------------------


async def test_get_config_uses_cache() -> None:
    store = AsyncMock(spec=TenantConfigStore)
    fake_cfg = default_config("t_acme")
    store.get_or_default.return_value = fake_cfg

    redis = AsyncMock()
    # Cache miss first
    redis.get.return_value = None

    cfg = await get_config("t_acme", store, redis)
    assert cfg.tenant_id == "t_acme"
    store.get_or_default.assert_called_once_with("t_acme")
    redis.set.assert_called()

    # Cache hit next
    redis.get.return_value = fake_cfg.model_dump_json()
    store.get_or_default.reset_mock()

    cached_cfg = await get_config("t_acme", store, redis)
    assert cached_cfg.tenant_id == "t_acme"
    store.get_or_default.assert_not_called()


async def test_update_config_invalidates_cache_and_publishes() -> None:
    store = AsyncMock(spec=TenantConfigStore)
    old_cfg = default_config("t_acme")
    store.get_or_default.return_value = old_cfg

    updated_cfg = default_config("t_acme")
    updated_cfg.version = 2
    store.update.return_value = updated_cfg

    redis = AsyncMock()
    registry = MagicMock()
    audit_col = AsyncMock()
    registry.system_collection.return_value = audit_col

    res = await update_config(
        "t_acme",
        {"branding": {"display_name": "New Name"}},
        expected_version=1,
        store=store,
        redis=redis,
        registry=registry,
    )
    assert res.version == 2

    # Invalidation deleted keys and published change
    redis.delete.assert_called_once()
    redis.publish.assert_called_once()
    audit_col.insert_one.assert_called_once()


# ---------------------------------------------------------------------------
# API Endpoints: GET and PATCH /tenant/config
# ---------------------------------------------------------------------------


async def test_get_tenant_config_returns_defaults_and_etag() -> None:
    settings = _make_settings()
    app = create_app(settings)

    fake_cfg = default_config("t_acme")
    fake_store = AsyncMock(spec=TenantConfigStore)
    fake_store.get_or_default.return_value = fake_cfg

    fake_redis = AsyncMock()
    fake_redis.get.return_value = None

    app.dependency_overrides[get_tenant_config_store] = lambda: fake_store
    app.dependency_overrides[get_redis] = lambda: fake_redis

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
            r = await client.get("/api/v1/tenant/config")
            assert r.status_code == 200
            data = r.json()
            assert data["message"] == "Configuration retrieved successfully"
            config_data = data["data"]["config"]
            assert config_data["tenant_id"] == "t_acme"
            assert config_data["version"] == 1
            assert r.headers["etag"] == '"1"'
    finally:
        reset_context(token)


async def test_patch_tenant_config_missing_if_match_returns_412() -> None:
    settings = _make_settings()
    app = create_app(settings)

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
            r = await client.patch(
                "/api/v1/tenant/config",
                json={"branding": {"display_name": "Acme"}},
            )
            assert r.status_code == 412
            assert r.json()["data"]["code"] == "precondition_failed"
    finally:
        reset_context(token)


async def test_patch_tenant_config_stale_if_match_returns_412() -> None:
    settings = _make_settings()
    app = create_app(settings)

    fake_store = AsyncMock(spec=TenantConfigStore)
    # Stored version is 1, caller sends If-Match: "2"
    from app.core.errors import VersionConflictError

    fake_store.update.side_effect = VersionConflictError("t_acme", 2)
    fake_store.get_or_default.return_value = default_config("t_acme")

    fake_redis = AsyncMock()
    app.dependency_overrides[get_tenant_config_store] = lambda: fake_store
    app.dependency_overrides[get_redis] = lambda: fake_redis

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
            r = await client.patch(
                "/api/v1/tenant/config",
                json={"branding": {"display_name": "Acme"}},
                headers={"If-Match": '"2"'},
            )
            assert r.status_code == 412
            assert r.json()["data"]["code"] == "version_mismatch"
    finally:
        reset_context(token)


async def test_patch_tenant_config_invalid_field_returns_422() -> None:
    settings = _make_settings()
    app = create_app(settings)

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
            # Unknown top-level field rejected by BaseSchema (extra="forbid")
            r = await client.patch(
                "/api/v1/tenant/config",
                json={"unknown_field": "disallowed"},
                headers={"If-Match": '"1"'},
            )
            assert r.status_code == 422
            assert r.json()["data"]["code"] == "validation_failed"
    finally:
        reset_context(token)


async def test_patch_tenant_config_success_audited() -> None:
    settings = _make_settings()
    app = create_app(settings)

    fake_store = AsyncMock(spec=TenantConfigStore)
    old_cfg = default_config("t_acme")
    new_cfg = default_config("t_acme")
    new_cfg.version = 2
    new_cfg.security.idle_timeout_min = 20

    fake_store.get_or_default.return_value = old_cfg
    fake_store.update.return_value = new_cfg

    fake_redis = AsyncMock()
    fake_registry = MagicMock()
    fake_audit = AsyncMock()
    fake_registry.system_collection.return_value = fake_audit

    app.dependency_overrides[get_tenant_config_store] = lambda: fake_store
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_registry] = lambda: fake_registry

    token = set_context(
        RequestContext(
            tenant_id="t_acme",
            request_id="req_test",
            user_id="user_admin",
            permissions=frozenset(["tenant:configure"]),
        )
    )
    try:
        async with (
            LifespanManager(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            r = await client.patch(
                "/api/v1/tenant/config",
                json={"security": {"idle_timeout_min": 20}},
                headers={"If-Match": '"1"'},
            )
            assert r.status_code == 200
            data = r.json()
            assert data["message"] == "Configuration updated successfully"
            assert data["data"]["config"]["version"] == 2
            assert r.headers["etag"] == '"2"'

            # Audit record written
            fake_audit.insert_one.assert_called_once()
            audit_entry = fake_audit.insert_one.call_args[0][0]
            assert audit_entry["action"] == "tenant_config.update"
            assert audit_entry["tenant_id"] == "t_acme"
            assert "security" in audit_entry["changed_fields"]
            assert audit_entry["diff"] is not None
            assert audit_entry["diff"]["previous"]["idle_timeout_min"] == 30
            assert audit_entry["diff"]["new"]["idle_timeout_min"] == 20
    finally:
        reset_context(token)
