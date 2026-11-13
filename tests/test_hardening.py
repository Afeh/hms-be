"""HTTP hardening tests (issue 0006).

Covers:
- Security headers on success and error responses
- HSTS only in non-local environments
- Max body size (413 on oversized body)
- NoSQL injection guard (422 on $-prefixed / dotted keys)
- Rate limiter: 429 with Retry-After after limit exceeded
- Rate limiter fail-open when Redis is unavailable
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core import Settings
from app.core.context import RequestContext, reset_context, set_context
from app.core.errors import QuerySanitizationError, register_exception_handlers
from app.db.tenancy import sanitize_query
from app.main import create_app
from app.schemas import BaseSchema, StrictBaseModel

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mongodb_uri": "mongodb://127.0.0.1:1",
        "mongodb_server_selection_timeout_ms": 250,
        "redis_url": "redis://127.0.0.1:1/0",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    settings = _make_settings()
    app = create_app(settings)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
    ):
        yield http


@pytest.fixture
async def staging_client() -> AsyncIterator[AsyncClient]:
    settings = _make_settings(environment="staging")
    app = create_app(settings)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
    ):
        yield http


# ---------------------------------------------------------------------------
# Security headers — success response
# ---------------------------------------------------------------------------


async def test_security_headers_on_success_response(client: AsyncClient) -> None:
    """Every successful response carries the four mandatory security headers."""
    response = await client.get("/docs")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert response.headers.get("cache-control") == "no-store"


async def test_security_headers_on_error_response(client: AsyncClient) -> None:
    """404 and other error responses also carry the security headers."""
    response = await client.get("/no-such-route-exists-xyz")
    assert response.status_code == 404
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert response.headers.get("cache-control") == "no-store"


async def test_no_hsts_in_local_environment(client: AsyncClient) -> None:
    """local environment must NOT include Strict-Transport-Security."""
    response = await client.get("/docs")
    assert "strict-transport-security" not in response.headers


async def test_hsts_present_in_non_local_environment(staging_client: AsyncClient) -> None:
    """staging/production responses include the HSTS header."""
    response = await staging_client.get("/docs")
    hsts = response.headers.get("strict-transport-security", "")
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts


# ---------------------------------------------------------------------------
# Body size limit
# ---------------------------------------------------------------------------


async def test_oversized_body_returns_413(client: AsyncClient) -> None:
    """Requests larger than 1 MiB must be rejected with 413 payload_too_large."""
    large_body = b"x" * (1_048_576 + 1)  # 1 MiB + 1 byte
    response = await client.post(
        "/api/v1/auth/login",
        content=large_body,
        headers={"content-type": "application/octet-stream"},
    )
    assert response.status_code == 413
    # The middleware returns a raw JSON envelope, not the app's error handler.
    data = response.json()
    assert data.get("status_code") == 413


async def test_exact_limit_body_is_not_rejected(client: AsyncClient) -> None:
    """A body at exactly the limit (1 MiB) is passed through (not rejected)."""
    exact_body = b"x" * 1_048_576
    # We expect some non-413 response (maybe 422 for wrong content type, etc.)
    response = await client.post(
        "/api/v1/auth/login",
        content=exact_body,
        headers={"content-type": "application/octet-stream"},
    )
    assert response.status_code != 413


# ---------------------------------------------------------------------------
# NoSQL injection guard — sanitize_query
# ---------------------------------------------------------------------------


def test_dollar_key_raises_query_sanitization_error() -> None:
    """$-prefixed keys must be rejected by sanitize_query."""
    with pytest.raises(QuerySanitizationError, match=r"forbidden query key"):
        sanitize_query({"$where": "1==1"})


def test_dotted_key_raises_query_sanitization_error() -> None:
    """Dotted keys must be rejected by sanitize_query."""
    with pytest.raises(QuerySanitizationError, match=r"forbidden query key"):
        sanitize_query({"a.b": "value"})


def test_nested_dollar_key_raises_query_sanitization_error() -> None:
    """Nested $-prefixed keys must also be rejected."""
    with pytest.raises(QuerySanitizationError):
        sanitize_query({"outer": {"$gt": 0}})


def test_clean_query_passes_sanitize_query() -> None:
    """Normal key-value filters pass sanitize_query unchanged."""
    result = sanitize_query({"status": "active", "tenant_id": "t1"})
    assert result == {"status": "active", "tenant_id": "t1"}


def test_empty_query_passes_sanitize_query() -> None:
    """Empty/None filter is treated as an empty dict."""
    assert sanitize_query(None) == {}
    assert sanitize_query({}) == {}


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


async def test_rate_limit_exceeded_returns_429_with_retry_after() -> None:
    """After N requests the rate limiter raises 429 with a Retry-After header."""
    from fastapi import Depends, FastAPI

    from app.core.rate_limit import rate_limit

    # Build a fake Redis whose INCR counter monotonically increments.
    call_count = 0

    async def fake_incr(key: str) -> int:
        nonlocal call_count
        call_count += 1
        return call_count

    async def fake_expire(key: str, ttl: int) -> None:
        pass

    fake_redis = AsyncMock()
    fake_redis.incr = fake_incr
    fake_redis.expire = fake_expire

    app = FastAPI()
    # Inject fake Redis into app state so the _get_redis helper finds it.
    app.state.redis = fake_redis
    register_exception_handlers(app)

    dep = rate_limit("test_endpoint", limit=3, window_seconds=60)

    @app.get("/test-rl", dependencies=[Depends(dep)])
    async def _endpoint() -> dict[str, object]:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        for _ in range(3):
            r = await http.get("/test-rl")
            assert r.status_code == 200, r.text

        # 4th request must be rate-limited
        r = await http.get("/test-rl")
        assert r.status_code == 429
        assert "retry-after" in r.headers


async def test_rate_limiter_fail_open_when_redis_unavailable() -> None:
    """When Redis raises an exception the limiter allows the request through."""
    from fastapi import Depends, FastAPI

    from app.core.rate_limit import rate_limit

    async def broken_incr(key: str) -> int:
        raise ConnectionError("Redis is down")

    fake_redis = AsyncMock()
    fake_redis.incr = broken_incr

    app = FastAPI()
    app.state.redis = fake_redis
    register_exception_handlers(app)

    dep = rate_limit("test_fail_open", limit=1, window_seconds=60)

    @app.get("/fail-open", dependencies=[Depends(dep)])
    async def _endpoint() -> dict[str, object]:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        # Fail-open: all requests must pass even though Redis is broken
        for _ in range(5):
            r = await http.get("/fail-open")
            assert r.status_code == 200


async def test_rate_limiter_uses_tenant_id_when_context_present() -> None:
    """Rate-limit key must include the tenant ID when a request context is set."""
    from fastapi import Depends, FastAPI

    from app.core.rate_limit import rate_limit

    recorded_keys: list[str] = []

    async def tracking_incr(key: str) -> int:
        recorded_keys.append(key)
        return 1

    fake_redis = AsyncMock()
    fake_redis.incr = tracking_incr
    fake_redis.expire = AsyncMock()

    app = FastAPI()
    app.state.redis = fake_redis
    register_exception_handlers(app)

    dep = rate_limit("scoped_action", limit=5, window_seconds=60)

    @app.get("/scoped", dependencies=[Depends(dep)])
    async def _endpoint() -> dict[str, object]:
        return {"ok": True}

    token = set_context(RequestContext(tenant_id="tenant_xyz", request_id="req_123"))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            r = await http.get("/scoped")
            assert r.status_code == 200
            assert any("tenant_xyz" in key for key in recorded_keys)
    finally:
        reset_context(token)


async def test_query_sanitization_error_handler_renders_422() -> None:
    """QuerySanitizationError caught by the exception handler renders 422 validation_failed."""
    from fastapi import FastAPI

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/trigger-query-error")
    async def _bad_route() -> dict[str, object]:
        raise QuerySanitizationError("$where")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        r = await http.get("/trigger-query-error")
        assert r.status_code == 422
        body = r.json()
        assert body["status"] == "error"
        assert body["status_code"] == 422
        assert body["data"]["code"] == "validation_failed"
        assert any("$where" in err["message"] for err in body["data"]["errors"])


# ---------------------------------------------------------------------------
# Strict base schema
# ---------------------------------------------------------------------------


def test_base_schema_strips_whitespace_and_forbids_extra() -> None:
    """BaseSchema trims string fields and rejects unexpected attributes."""

    class CreateItemRequest(BaseSchema):
        title: str
        count: int = 1

    # Whitespace stripped
    item = CreateItemRequest(title="   widget   ")
    assert item.title == "widget"

    # Extra fields forbidden
    with pytest.raises(ValidationError) as exc_info:
        CreateItemRequest.model_validate({"title": "widget", "unexpected": "extra"})

    assert "extra_forbidden" in str(exc_info.value)


def test_strict_base_model_alias() -> None:
    """StrictBaseModel is an alias of BaseSchema with identical behaviour."""

    class StrictItem(StrictBaseModel):
        code: str

    item = StrictItem(code=" abc ")
    assert item.code == "abc"

    with pytest.raises(ValidationError):
        StrictItem.model_validate({"code": "abc", "rogue": 123})
