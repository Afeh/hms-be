"""Patient registration, lookup and updates (sprint S001)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.core import Settings
from app.core.security import hash_password
from app.db import TenantConnectionRegistry
from app.db.identities import IdentityRepository, normalize_email
from app.db.indexes import create_indexes, create_patient_indexes
from app.main import create_app
from app.models.identity import Identity, PersonName
from app.services.sessions import create_session

MONGODB_URI = os.environ.get("MONGODB_URI")
PASSWORD = "Password123!"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        MONGODB_URI is None, reason="set MONGODB_URI to run patient integration tests"
    ),
]


@pytest.fixture
async def registry() -> AsyncIterator[TenantConnectionRegistry]:
    database = f"hms_test_patients_{uuid.uuid4().hex[:8]}"
    connection = TenantConnectionRegistry(MONGODB_URI or "", database, timeout_ms=2000)
    await create_indexes(connection.database)
    await create_patient_indexes(connection.database)
    try:
        yield connection
    finally:
        await connection.client.drop_database(database)
        await connection.close()


def _settings(database: str) -> Settings:
    return Settings(
        mongodb_uri=MONGODB_URI or "",
        mongodb_database=database,
        mongodb_server_selection_timeout_ms=2000,
        redis_url="redis://127.0.0.1:1/0",
    )


async def _account(
    registry: TenantConnectionRegistry,
    *,
    code: str = "ACH",
    country: str = "NG",
) -> dict[str, str]:
    now = datetime.now(UTC)
    db = registry.database
    tenant_id = ObjectId()
    await db["tenants"].insert_one(
        {
            "_id": tenant_id,
            "name": f"Hospital {code}",
            "slug": f"hospital-{uuid.uuid4().hex[:8]}",
            "code": code,
            "status": "active",
            "plan": "starter",
            "country": country,
            "timezone": "UTC",
            "legal_hold": False,
            "created_at": now,
            "version": 1,
        }
    )
    await db["tenant_configs"].insert_one(
        {
            "tenant_id": str(tenant_id),
            "patient_id_format": {
                "prefix": code,
                "pattern": "{PREFIX}-{RANDOM:5}-{YYYY}",
            },
            "created_at": now,
            "version": 1,
        }
    )
    email = f"staff-{uuid.uuid4().hex[:8]}@example.com"
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
            "permissions": ["patient:create", "patient:read", "patient:update"],
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
            "status": "active",
            "perm_version": 1,
            "created_at": now,
            "version": 1,
        }
    )
    issued = await create_session(
        registry,
        _settings(registry.database.name),
        tenant_id=str(tenant_id),
        user_id=str(user_result.inserted_id),
        identity_id=identity.id or "",
        perm_version=1,
    )
    return {
        "tenant_id": str(tenant_id),
        "identity_id": identity.id or "",
        "user_id": str(user_result.inserted_id),
        "access_token": issued.access_token,
    }


def _headers(account: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {account['access_token']}"}


async def _client(registry: TenantConnectionRegistry) -> AsyncIterator[AsyncClient]:
    app = create_app(_settings(registry.database.name))
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": {"given": ["Ada", "Ngozi"], "family": "Okafor"},
        "date_of_birth": "1991-04-18",
        "sex": "female",
        "phones": ["0803 123 4567"],
        "address": {"line1": "12 Market Road", "city": "Enugu", "country": "NG"},
        "next_of_kin": [
            {"name": "Emeka Okafor", "relationship": "brother", "phone": "0806 234 5678"}
        ],
    }
    body.update(overrides)
    return body


async def test_create_patient_allocates_tenant_number_and_normalises_phones(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client in _client(registry):
        response = await client.post("/api/v1/patients", json=_payload(), headers=_headers(account))
        assert response.status_code == 201, response.text
        patient = response.json()["data"]["patient"]

        assert patient["patient_number"].startswith("ACH-")
        assert patient["patient_number"].endswith("-2026")
        assert patient["tenant_id"] == account["tenant_id"]
        assert patient["display_name"] == "Ada Ngozi Okafor"
        assert patient["age_years"] == 35
        assert patient["phones"] == ["+2348031234567"]
        assert patient["next_of_kin"][0]["phone"] == "+2348062345678"
        assert patient["status"] == "active"
        assert patient["version"] == 1
        assert response.headers["etag"] == '"1"'


async def test_get_and_list_are_scoped_to_the_callers_tenant(
    registry: TenantConnectionRegistry,
) -> None:
    acme = await _account(registry, code="ACH")
    beta = await _account(registry, code="BSC")
    async for client in _client(registry):
        created = await client.post("/api/v1/patients", json=_payload(), headers=_headers(acme))
        assert created.status_code == 201, created.text
        patient_id = created.json()["data"]["patient"]["_id"]

        own = await client.get(f"/api/v1/patients/{patient_id}", headers=_headers(acme))
        assert own.status_code == 200
        assert own.json()["data"]["patient"]["patient_number"].startswith("ACH-")

        other = await client.get(f"/api/v1/patients/{patient_id}", headers=_headers(beta))
        assert other.status_code == 404
        assert other.json()["data"]["code"] == "not_found"

        listing = await client.get("/api/v1/patients", headers=_headers(beta))
        assert listing.status_code == 200
        assert listing.json()["data"]["patients"] == []


async def test_list_filters_sorts_and_pages_without_duplicates(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client in _client(registry):
        for family in ("Okafor", "Balogun", "Bello"):
            body = _payload(
                name={"given": ["Ada"], "family": family},
                date_of_birth=f"199{len(family)}-01-01",
            )
            created = await client.post("/api/v1/patients", json=body, headers=_headers(account))
            assert created.status_code == 201, created.text

        first = await client.get(
            "/api/v1/patients?limit=2&sort=-registration_date",
            headers=_headers(account),
        )
        assert first.status_code == 200, first.text
        first_data = first.json()["data"]
        assert len(first_data["patients"]) == 2
        assert first_data["next_cursor"] is not None

        second = await client.get(
            f"/api/v1/patients?limit=2&sort=-registration_date&cursor={first_data['next_cursor']}",
            headers=_headers(account),
        )
        assert second.status_code == 200, second.text
        second_data = second.json()["data"]
        assert len(second_data["patients"]) == 1
        assert second_data["next_cursor"] is None

        seen = [item["_id"] for item in first_data["patients"] + second_data["patients"]]
        assert len(set(seen)) == 3

        filtered = await client.get("/api/v1/patients?status=inactive", headers=_headers(account))
        assert filtered.status_code == 200
        assert filtered.json()["data"]["patients"] == []

        in_range = await client.get(
            "/api/v1/patients?registered_from=2026-01-01&registered_to=2026-12-31",
            headers=_headers(account),
        )
        assert in_range.status_code == 200, in_range.text
        assert len(in_range.json()["data"]["patients"]) == 3

        out_of_range = await client.get(
            "/api/v1/patients?registered_to=2020-01-01", headers=_headers(account)
        )
        assert out_of_range.status_code == 200
        assert out_of_range.json()["data"]["patients"] == []


async def test_patch_uses_version_check_and_updates_display_name(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client in _client(registry):
        created = await client.post("/api/v1/patients", json=_payload(), headers=_headers(account))
        patient = created.json()["data"]["patient"]
        patient_id = patient["_id"]

        updated = await client.patch(
            f"/api/v1/patients/{patient_id}",
            json={
                "name": {"given": ["Ada"], "family": "Nwosu"},
                "phones": ["+234 810 000 0000"],
                "paper_file": True,
            },
            headers={**_headers(account), "If-Match": '"1"'},
        )
        assert updated.status_code == 200, updated.text
        changed = updated.json()["data"]["patient"]
        assert changed["display_name"] == "Ada Nwosu"
        assert changed["phones"] == ["+2348100000000"]
        assert changed["paper_file"] is True
        assert changed["version"] == 2
        assert updated.headers["etag"] == '"2"'

        stale = await client.patch(
            f"/api/v1/patients/{patient_id}",
            json={"paper_file": False},
            headers={**_headers(account), "If-Match": '"1"'},
        )
        assert stale.status_code == 412
        assert stale.json()["data"]["code"] == "version_mismatch"


async def test_patient_routes_require_permission_and_auth(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client in _client(registry):
        anonymous = await client.get("/api/v1/patients")
        assert anonymous.status_code == 401

        await registry.database["roles"].update_one(
            {"tenant_id": account["tenant_id"]},
            {"$set": {"permissions": ["patient:read"]}},
        )
        denied = await client.post("/api/v1/patients", json=_payload(), headers=_headers(account))
        assert denied.status_code == 403
        assert denied.json()["data"]["code"] == "forbidden"


async def test_seed_creates_three_sample_patients_per_tenant(
    registry: TenantConnectionRegistry,
) -> None:
    from unittest.mock import patch

    from scripts.seed import seed

    settings = _settings(registry.database.name)
    with patch("scripts.seed.get_settings", return_value=settings):
        assert seed() == 0

    db = registry.database
    for slug in ("acme", "beta"):
        tenant = await db["tenants"].find_one({"slug": slug})
        assert tenant is not None
        patients = await db["patients"].find({"tenant_id": str(tenant["_id"])}).to_list(10)
        assert len(patients) == 3
        assert all(
            patient["patient_number"].startswith(f"{tenant['code']}-") for patient in patients
        )

    with patch("scripts.seed.get_settings", return_value=settings):
        assert seed() == 0
    assert await db["patients"].count_documents({}) == 6
