"""The envelope convention and the health probes end to end."""

from __future__ import annotations

from httpx import AsyncClient


async def test_live_returns_the_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "status_code": 200,
        "message": "API is live",
        "data": {"health": {"status": "ok"}},
    }


async def test_every_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")
    assert response.headers["x-request-id"].startswith("req_")


async def test_client_supplied_request_id_is_echoed(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/health/live", headers={"X-Request-ID": "req_fromtheclient"}
    )
    assert response.headers["x-request-id"] == "req_fromtheclient"


async def test_ready_reports_unavailable_dependencies(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["data"]["code"] == "dependency_unavailable"
    assert body["data"]["request_id"] == response.headers["x-request-id"]


async def test_unknown_path_uses_the_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/nope")

    assert response.status_code == 404
    body = response.json()
    assert body == {
        "status": "error",
        "status_code": 404,
        "message": "Not Found",
        "data": {"code": "not_found", "request_id": response.headers["x-request-id"]},
    }
