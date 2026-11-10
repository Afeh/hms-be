"""Shared fixtures. Tests never touch a real MongoDB or Redis."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.core import Settings
from app.core.context import RequestContext, reset_context, set_context
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Point the probes at closed ports so readiness fails fast."""
    return Settings(
        mongodb_uri="mongodb://127.0.0.1:1",
        mongodb_server_selection_timeout_ms=250,
        redis_url="redis://127.0.0.1:1/0",
    )


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


@pytest.fixture
def tenant_context() -> Iterator[RequestContext]:
    ctx = RequestContext(tenant_id="t_alpha", request_id="req_test0001", user_id="u_1")
    token = set_context(ctx)
    try:
        yield ctx
    finally:
        reset_context(token)
