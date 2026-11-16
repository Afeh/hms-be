"""The eight-step demo acceptance path (sprint definition of done)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.core import Settings
from app.db import TenantConnectionRegistry
from app.db.indexes import (
    create_indexes,
    create_patient_indexes,
    create_visit_indexes,
)
from app.main import create_app

MONGODB_URI = os.environ.get("MONGODB_URI")
PASSWORD = "Password123!"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        MONGODB_URI is None, reason="set MONGODB_URI to run the demo acceptance test"
    ),
]


def _settings(database: str) -> Settings:
    return Settings(
        mongodb_uri=MONGODB_URI or "",
        mongodb_database=database,
        mongodb_server_selection_timeout_ms=2000,
        redis_url="redis://127.0.0.1:1/0",
    )


@pytest.fixture
async def registry() -> AsyncIterator[TenantConnectionRegistry]:
    database = f"hms_test_demo_{uuid.uuid4().hex[:8]}"
    connection = TenantConnectionRegistry(MONGODB_URI or "", database, timeout_ms=2000)
    await create_indexes(connection.database)
    await create_patient_indexes(connection.database)
    await create_visit_indexes(connection.database)
    try:
        yield connection
    finally:
        await connection.client.drop_database(database)
        await connection.close()


async def test_demo_flow_login_register_visit_vitals_close_search(
    registry: TenantConnectionRegistry,
) -> None:
    from scripts.seed import seed

    settings = _settings(registry.database.name)
    with patch("scripts.seed.get_settings", return_value=settings):
        assert seed() == 0

    app = create_app(settings)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        fake_redis = AsyncMock()
        fake_redis.incr = AsyncMock(return_value=1)
        fake_redis.expire = AsyncMock()
        app.state.redis = fake_redis

        # 1. Admin logs in.
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@acme.example", "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        login_data = login.json()["data"]
        headers = {"Authorization": f"Bearer {login_data['access_token']}"}

        # 2. Profile with permissions and tenant.
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        profile = me.json()["data"]
        assert profile["tenant"]["name"] == "Acme Hospital"
        assert {
            "patient:create",
            "patient:read",
            "visit:create",
            "visit:read",
            "visit:close",
            "vitals:write",
        }.issubset(profile["permissions"])

        # 3. Register a patient.
        patient_response = await client.post(
            "/api/v1/patients",
            json={
                "name": {"given": ["Demo", "Ada"], "family": "Okafor"},
                "date_of_birth": "1992-06-11",
                "sex": "female",
                "phones": ["+2348031112233"],
                "address": {"line1": "1 Demo Road", "city": "Enugu", "country": "NG"},
            },
            headers=headers,
        )
        assert patient_response.status_code == 201, patient_response.text
        patient = patient_response.json()["data"]["patient"]
        assert patient["patient_number"].startswith("ACH-")
        patient_id = patient["_id"]

        # 4. Create a visit.
        visit_response = await client.post(
            f"/api/v1/patients/{patient_id}/visits",
            json={
                "type": "outpatient",
                "visit_date": "2026-11-14",
                "attending_clinician_id": profile["user"]["_id"],
                "reason_for_visit": "Fever and headache for three days",
            },
            headers=headers,
        )
        assert visit_response.status_code == 201, visit_response.text
        visit = visit_response.json()["data"]["visit"]
        assert visit["status"] == "open"
        assert visit_response.headers["etag"] == '"1"'
        visit_id = visit["_id"]

        # 5. Record vitals; BMI is computed server-side.
        vitals_response = await client.post(
            f"/api/v1/visits/{visit_id}/vitals",
            json={
                "temperature_c": 38.4,
                "pulse_bpm": 98,
                "systolic_mmhg": 128,
                "diastolic_mmhg": 82,
                "weight_kg": 81.0,
                "height_cm": 175,
            },
            headers=headers,
        )
        assert vitals_response.status_code == 201, vitals_response.text
        vitals = vitals_response.json()["data"]["vitals"]
        assert vitals["bmi"] == 26.4
        assert vitals["warnings"] == []

        # 6. View the chart and the visit with its nested vitals.
        chart = await client.get(f"/api/v1/patients/{patient_id}", headers=headers)
        assert chart.status_code == 200, chart.text
        assert chart.json()["data"]["patient"]["last_visit_date"] is not None

        visits = await client.get(f"/api/v1/patients/{patient_id}/visits", headers=headers)
        assert visits.status_code == 200, visits.text
        assert [item["_id"] for item in visits.json()["data"]["visits"]] == [visit_id]

        visit_detail = await client.get(f"/api/v1/visits/{visit_id}", headers=headers)
        assert visit_detail.status_code == 200, visit_detail.text
        assert [row["_id"] for row in visit_detail.json()["data"]["visit"]["vitals"]] == [
            vitals["_id"]
        ]

        # 7. Close the visit.
        closed = await client.post(
            f"/api/v1/visits/{visit_id}/close",
            json={"force": False},
            headers={**headers, "If-Match": '"1"'},
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["data"]["visit"]["status"] == "closed"
        assert closed.json()["data"]["visit"]["closed_at"] is not None

        # 8. Search the patient list.
        search = await client.get(
            "/api/v1/patients?status=active&sort=-registration_date",
            headers=headers,
        )
        assert search.status_code == 200, search.text
        assert patient_id in {item["_id"] for item in search.json()["data"]["patients"]}
