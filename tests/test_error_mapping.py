"""Repository failures become the documented envelopes (412 / 409)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.errors import (
    DuplicateEntityError,
    VersionConflictError,
    register_exception_handlers,
)


@pytest.fixture
async def error_client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/stale")
    async def stale() -> None:
        raise VersionConflictError("66e7c0a2e4b0a1b2c3d4e5f6", 3)

    @app.get("/duplicate")
    async def duplicate() -> None:
        raise DuplicateEntityError("duplicate key")

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def test_stale_version_is_412_version_mismatch(error_client: AsyncClient) -> None:
    response = await error_client.get("/stale")

    assert response.status_code == 412
    assert response.json()["data"]["code"] == "version_mismatch"


async def test_duplicate_key_is_409_conflict(error_client: AsyncClient) -> None:
    response = await error_client.get("/duplicate")

    assert response.status_code == 409
    assert response.json()["data"]["code"] == "conflict"
