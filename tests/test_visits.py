"""Visits, vital signs and visit closure (sprint S002)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from asgi_lifespan import LifespanManager
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.core import Settings
from app.core.security import hash_password
from app.db import TenantConnectionRegistry
from app.db.identities import IdentityRepository, normalize_email
from app.db.indexes import (
    create_indexes,
    create_patient_indexes,
    create_visit_attachment_indexes,
    create_visit_indexes,
)
from app.main import create_app
from app.models.identity import Identity, PersonName
from app.services.sessions import create_session
from app.services.storage import PresignedDownload, Storage

MONGODB_URI = os.environ.get("MONGODB_URI")
PASSWORD = "Password123!"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        MONGODB_URI is None, reason="set MONGODB_URI to run visit integration tests"
    ),
]

VISIT_TYPES = [
    {"key": "outpatient", "label": "Outpatient", "active": True, "enabled": True},
    {"key": "inpatient", "label": "Inpatient", "active": True, "enabled": True},
    {"key": "emergency", "label": "Emergency", "active": True, "enabled": True},
]


@pytest.fixture
async def registry() -> AsyncIterator[TenantConnectionRegistry]:
    database = f"hms_test_visits_{uuid.uuid4().hex[:8]}"
    connection = TenantConnectionRegistry(MONGODB_URI or "", database, timeout_ms=2000)
    await create_indexes(connection.database)
    await create_patient_indexes(connection.database)
    await create_visit_indexes(connection.database)
    await create_visit_attachment_indexes(connection.database)
    try:
        yield connection
    finally:
        await connection.client.drop_database(database)
        await connection.close()


def _settings(database: str, **overrides: Any) -> Settings:
    return Settings(
        mongodb_uri=MONGODB_URI or "",
        mongodb_database=database,
        mongodb_server_selection_timeout_ms=2000,
        redis_url="redis://127.0.0.1:1/0",
        **overrides,
    )


async def _account(
    registry: TenantConnectionRegistry,
    *,
    code: str = "ACH",
    permissions: list[str] | None = None,
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
            "country": "NG",
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
            "visit_types": VISIT_TYPES,
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
            "permissions": permissions
            or [
                "patient:create",
                "patient:read",
                "visit:create",
                "visit:read",
                "visit:update",
                "visit:close",
                "vitals:write",
                "document:capture",
                "document:read",
            ],
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
            "display_name": "Dr Ada Okafor",
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


class FakeStorage:
    """An in-memory stand-in for R2 so the route flow is tested without network."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[str, bytes]] = {}
        self.last_upload_key: str | None = None
        self.deleted_keys: list[str] = []

    async def upload_object(self, *, key: str, content_type: str, data: bytes) -> str | None:
        self.last_upload_key = key
        self.objects[key] = (content_type, data)
        return "etag-123"

    async def presign_download(self, *, key: str) -> PresignedDownload:
        return PresignedDownload(
            url=f"https://r2.test/download/{key}",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    async def delete_object(self, *, key: str) -> None:
        self.deleted_keys.append(key)
        self.objects.pop(key, None)


async def _client(
    registry: TenantConnectionRegistry,
    storage: Storage | None = None,
    **settings_overrides: Any,
) -> AsyncIterator[AsyncClient]:
    app = create_app(_settings(registry.database.name, **settings_overrides))
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        if storage is not None:
            app.state.storage = storage
        yield client


async def _create_patient(client: AsyncClient, account: dict[str, str]) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/patients",
        json={
            "name": {"given": ["Ada", "Ngozi"], "family": "Okafor"},
            "date_of_birth": "1991-04-18",
            "sex": "female",
            "phones": ["+2348031234567"],
        },
        headers=_headers(account),
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json()["data"]["patient"])


async def _create_visit(
    client: AsyncClient,
    account: dict[str, str],
    patient_id: str,
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "outpatient",
        "visit_date": "2026-11-09",
        "attending_clinician_id": account["user_id"],
        "reason_for_visit": "Headache and fever for 3 days",
    }
    body.update(overrides)
    response = await client.post(
        f"/api/v1/patients/{patient_id}/visits",
        json=body,
        headers=_headers(account),
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json()["data"]["visit"])


async def test_visit_lifecycle_and_vitals_are_recorded_on_the_visit(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client in _client(registry):
        patient = await _create_patient(client, account)
        visit = await _create_visit(client, account, patient["_id"])
        assert visit["status"] == "open"
        assert visit["type"] == "outpatient"
        assert visit["visit_date"] == "2026-11-09"
        assert visit["attending_clinician"]["_id"] == account["user_id"]
        assert visit["attending_clinician"]["name"] == "Dr Ada Okafor"
        assert visit["version"] == 1
        visit_id = visit["_id"]

        patient_after = await client.get(
            f"/api/v1/patients/{patient['_id']}", headers=_headers(account)
        )
        assert patient_after.status_code == 200
        assert patient_after.json()["data"]["patient"]["last_visit_date"] is not None

        vitals_response = await client.post(
            f"/api/v1/visits/{visit_id}/vitals",
            json={
                "measured_at": "2026-11-09T09:55:00Z",
                "temperature_c": 38.4,
                "pulse_bpm": 98,
                "systolic_mmhg": 128,
                "diastolic_mmhg": 82,
                "weight_kg": 81.0,
                "height_cm": 175,
            },
            headers=_headers(account),
        )
        assert vitals_response.status_code == 201, vitals_response.text
        vitals = vitals_response.json()["data"]["vitals"]
        assert vitals["bmi"] == 26.4
        assert vitals["warnings"] == []
        assert vitals["recorded_by"] == account["user_id"]
        assert vitals_response.headers["etag"] == '"1"'

        detail = await client.get(f"/api/v1/visits/{visit_id}", headers=_headers(account))
        assert detail.status_code == 200, detail.text
        detail_visit = detail.json()["data"]["visit"]
        assert [row["_id"] for row in detail_visit["vitals"]] == [vitals["_id"]]
        assert detail.headers["etag"] == '"1"'

        visit_list = await client.get(
            f"/api/v1/patients/{patient['_id']}/visits",
            headers=_headers(account),
        )
        assert visit_list.status_code == 200
        assert len(visit_list.json()["data"]["visits"]) == 1
        assert visit_list.json()["data"]["visits"][0]["_id"] == visit_id

        stale = await client.post(
            f"/api/v1/visits/{visit_id}/close",
            json={"force": False},
            headers={**_headers(account), "If-Match": '"2"'},
        )
        assert stale.status_code == 412

        closed = await client.post(
            f"/api/v1/visits/{visit_id}/close",
            json={"force": False},
            headers={**_headers(account), "If-Match": '"1"'},
        )
        assert closed.status_code == 200, closed.text
        closed_visit = closed.json()["data"]["visit"]
        assert closed_visit["status"] == "closed"
        assert closed_visit["closed_at"] is not None
        assert closed_visit["closed_by"] == account["user_id"]
        assert closed_visit["version"] == 2
        assert closed.headers["etag"] == '"2"'

        rejected = await client.post(
            f"/api/v1/visits/{visit_id}/vitals",
            json={"pulse_bpm": 70},
            headers=_headers(account),
        )
        assert rejected.status_code == 409
        assert rejected.json()["data"]["code"] == "visit_not_open"


async def test_unusual_vitals_need_acknowledgement_and_impossible_vitals_are_rejected(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client in _client(registry):
        patient = await _create_patient(client, account)
        visit = await _create_visit(client, account, patient["_id"])
        visit_id = visit["_id"]

        warning = await client.post(
            f"/api/v1/visits/{visit_id}/vitals",
            json={"temperature_c": 41.5, "height_cm": 175},
            headers=_headers(account),
        )
        assert warning.status_code == 409, warning.text
        warning_data = warning.json()["data"]
        assert warning_data["code"] == "vitals_confirmation_required"
        assert warning_data["warnings"][0]["field"] == "temperature_c"
        assert warning_data["warnings"][0]["value"] == 41.5

        empty = await client.get(
            f"/api/v1/patients/{patient['_id']}/vitals", headers=_headers(account)
        )
        assert empty.status_code == 200
        assert empty.json()["data"]["vitals"] == []

        accepted = await client.post(
            f"/api/v1/visits/{visit_id}/vitals",
            json={
                "temperature_c": 41.5,
                "weight_kg": 81.0,
                "height_cm": 175,
                "acknowledge_warnings": True,
            },
            headers=_headers(account),
        )
        assert accepted.status_code == 201, accepted.text
        saved = accepted.json()["data"]["vitals"]
        assert saved["warnings"][0]["field"] == "temperature_c"
        assert saved["bmi"] == 26.4

        impossible = await client.post(
            f"/api/v1/visits/{visit_id}/vitals",
            json={"pulse_bpm": 900},
            headers=_headers(account),
        )
        assert impossible.status_code == 422, impossible.text
        assert impossible.json()["data"]["code"] == "vitals_invalid"
        assert impossible.json()["data"]["errors"][0]["field"] == "pulse_bpm"

        missing = await client.post(
            f"/api/v1/visits/{visit_id}/vitals",
            json={},
            headers=_headers(account),
        )
        assert missing.status_code == 422

        weight_only = await client.post(
            f"/api/v1/visits/{visit_id}/vitals",
            json={"weight_kg": 82.0},
            headers=_headers(account),
        )
        assert weight_only.status_code == 201, weight_only.text
        assert weight_only.json()["data"]["vitals"]["bmi"] == 26.8

        entered_in_error = await client.patch(
            f"/api/v1/vitals/{saved['_id']}",
            json={"status": "entered_in_error", "reason": "Wrong thermometer"},
            headers=_headers(account),
        )
        assert entered_in_error.status_code == 200, entered_in_error.text
        corrected = entered_in_error.json()["data"]["vitals"]
        assert corrected["status"] == "entered_in_error"
        assert corrected["entered_in_error_reason"] == "Wrong thermometer"

        detail = await client.get(f"/api/v1/visits/{visit_id}", headers=_headers(account))
        visible_ids = [row["_id"] for row in detail.json()["data"]["visit"]["vitals"]]
        assert saved["_id"] not in visible_ids
        assert len(visible_ids) == 1


async def test_visit_and_vitals_lists_page_without_duplicates(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client in _client(registry):
        patient = await _create_patient(client, account)
        visit_ids: list[str] = []
        for day in (9, 10, 11):
            visit = await _create_visit(
                client, account, patient["_id"], visit_date=f"2026-11-{day:02d}"
            )
            visit_ids.append(visit["_id"])
            vitals = await client.post(
                f"/api/v1/visits/{visit['_id']}/vitals",
                json={"pulse_bpm": 70, "measured_at": f"2026-11-{day:02d}T09:00:00Z"},
                headers=_headers(account),
            )
            assert vitals.status_code == 201, vitals.text

        first = await client.get(
            f"/api/v1/patients/{patient['_id']}/visits?limit=2",
            headers=_headers(account),
        )
        assert first.status_code == 200, first.text
        first_data = first.json()["data"]
        assert len(first_data["visits"]) == 2
        assert first_data["next_cursor"] is not None

        second = await client.get(
            f"/api/v1/patients/{patient['_id']}/visits?limit=2&cursor={first_data['next_cursor']}",
            headers=_headers(account),
        )
        assert second.status_code == 200, second.text
        second_data = second.json()["data"]
        assert len(second_data["visits"]) == 1
        assert second_data["next_cursor"] is None
        seen = {item["_id"] for item in first_data["visits"] + second_data["visits"]}
        assert seen == set(visit_ids)

        vitals_first = await client.get(
            f"/api/v1/patients/{patient['_id']}/vitals?limit=2",
            headers=_headers(account),
        )
        assert vitals_first.status_code == 200, vitals_first.text
        vitals_page = vitals_first.json()["data"]
        assert len(vitals_page["vitals"]) == 2
        assert vitals_page["next_cursor"] is not None

        vitals_second = await client.get(
            f"/api/v1/patients/{patient['_id']}/vitals?limit=2&cursor={vitals_page['next_cursor']}",
            headers=_headers(account),
        )
        assert vitals_second.status_code == 200, vitals_second.text
        assert len(vitals_second.json()["data"]["vitals"]) == 1


async def test_visit_validation_updates_and_tenant_isolation(
    registry: TenantConnectionRegistry,
) -> None:
    acme = await _account(registry, code="ACH")
    beta = await _account(registry, code="BSC")
    async for client in _client(registry):
        patient = await _create_patient(client, acme)
        unknown_type = await client.post(
            f"/api/v1/patients/{patient['_id']}/visits",
            json={
                "type": "home_visit",
                "visit_date": "2026-11-09",
                "attending_clinician_id": acme["user_id"],
            },
            headers=_headers(acme),
        )
        assert unknown_type.status_code == 422
        assert unknown_type.json()["data"]["code"] == "unknown_visit_type"

        unknown_clinician = await client.post(
            f"/api/v1/patients/{patient['_id']}/visits",
            json={
                "type": "outpatient",
                "visit_date": "2026-11-09",
                "attending_clinician_id": str(ObjectId()),
            },
            headers=_headers(acme),
        )
        assert unknown_clinician.status_code == 422
        assert unknown_clinician.json()["data"]["code"] == "attending_not_clinician"

        visit = await _create_visit(client, acme, patient["_id"])
        updated = await client.patch(
            f"/api/v1/visits/{visit['_id']}",
            json={"reason_for_visit": "Follow-up after treatment"},
            headers={**_headers(acme), "If-Match": '"1"'},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["visit"]["reason_for_visit"] == "Follow-up after treatment"
        assert updated.json()["data"]["visit"]["version"] == 2

        other_get = await client.get(f"/api/v1/visits/{visit['_id']}", headers=_headers(beta))
        assert other_get.status_code == 404

        other_list = await client.get(
            f"/api/v1/patients/{patient['_id']}/visits", headers=_headers(beta)
        )
        assert other_list.status_code == 404

        other_vitals = await client.post(
            f"/api/v1/visits/{visit['_id']}/vitals",
            json={"pulse_bpm": 72},
            headers=_headers(beta),
        )
        assert other_vitals.status_code == 404


async def test_visit_routes_require_permission(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    async for client in _client(registry):
        patient = await _create_patient(client, account)
        await registry.database["roles"].update_one(
            {"tenant_id": account["tenant_id"]},
            {"$set": {"permissions": ["visit:read"]}},
        )
        denied = await client.post(
            f"/api/v1/patients/{patient['_id']}/visits",
            json={
                "type": "outpatient",
                "visit_date": "2026-11-09",
                "attending_clinician_id": account["user_id"],
            },
            headers=_headers(account),
        )
        assert denied.status_code == 403


async def test_seed_creates_sample_visits_and_vitals(
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
        tenant_id = str(tenant["_id"])
        config = await db["tenant_configs"].find_one({"tenant_id": tenant_id})
        assert config is not None
        assert {item["key"] for item in config["visit_types"]} == {
            "outpatient",
            "inpatient",
            "emergency",
        }
        assert await db["visits"].count_documents({"tenant_id": tenant_id}) == 3
        assert await db["vital_signs"].count_documents({"tenant_id": tenant_id}) == 3

    with patch("scripts.seed.get_settings", return_value=settings):
        assert seed() == 0
    assert await db["visits"].count_documents({}) == 6
    assert await db["vital_signs"].count_documents({}) == 6


async def test_visit_images_are_uploaded_through_the_api_and_are_readable(
    registry: TenantConnectionRegistry,
) -> None:
    account = await _account(registry)
    storage = FakeStorage()
    async for client in _client(registry, storage, storage_max_image_bytes=10):
        patient = await _create_patient(client, account)
        visit = await _create_visit(client, account, patient["_id"])
        visit_id = visit["_id"]

        created = await client.post(
            f"/api/v1/visits/{visit_id}/attachments",
            data={"caption": "Wound on left forearm"},
            files={"file": ("ward-round.JPG", b"12345", "image/jpeg")},
            headers=_headers(account),
        )
        assert created.status_code == 201, created.text
        attachment = created.json()["data"]["attachment"]
        attachment_id = attachment["_id"]
        assert attachment["filename"] == "ward-round.JPG"
        assert attachment["caption"] == "Wound on left forearm"
        assert attachment["status"] == "uploaded"
        assert attachment["version"] == 1
        assert attachment["uploaded_by"] == account["user_id"]
        assert attachment["download_url"].startswith("https://r2.test/download/")
        assert "upload" not in attachment

        assert storage.last_upload_key is not None
        key = storage.last_upload_key
        assert key.startswith(
            f"t/{account['tenant_id']}/patients/{patient['_id']}/visits/{visit_id}/attachments/"
        )
        assert storage.objects[key] == ("image/jpeg", b"12345")

        listed = await client.get(
            f"/api/v1/visits/{visit_id}/attachments",
            headers=_headers(account),
        )
        assert listed.status_code == 200, listed.text
        assert [row["_id"] for row in listed.json()["data"]["attachments"]] == [attachment_id]

        fresh_url = await client.get(
            f"/api/v1/visits/{visit_id}/attachments/{attachment_id}/url",
            headers=_headers(account),
        )
        assert fresh_url.status_code == 200, fresh_url.text
        assert fresh_url.json()["data"]["download"]["url"].startswith("https://r2.test/download/")

        unsupported = await client.post(
            f"/api/v1/visits/{visit_id}/attachments",
            files={"file": ("scan.pdf", b"%PDF-1.4", "application/pdf")},
            headers=_headers(account),
        )
        assert unsupported.status_code == 415
        assert unsupported.json()["data"]["code"] == "unsupported_media_type"

        too_large = await client.post(
            f"/api/v1/visits/{visit_id}/attachments",
            files={"file": ("huge.jpg", b"x" * 11, "image/jpeg")},
            headers=_headers(account),
        )
        assert too_large.status_code == 413
        assert too_large.json()["data"]["code"] == "image_too_large"

        empty = await client.post(
            f"/api/v1/visits/{visit_id}/attachments",
            files={"file": ("empty.jpg", b"", "image/jpeg")},
            headers=_headers(account),
        )
        assert empty.status_code == 422
        assert empty.json()["data"]["code"] == "empty_image"

        closed = await client.post(
            f"/api/v1/visits/{visit_id}/close",
            json={"force": False},
            headers={**_headers(account), "If-Match": '"1"'},
        )
        assert closed.status_code == 200, closed.text
        rejected_after_close = await client.post(
            f"/api/v1/visits/{visit_id}/attachments",
            files={"file": ("late.jpg", b"12345", "image/jpeg")},
            headers=_headers(account),
        )
        assert rejected_after_close.status_code == 409
        assert rejected_after_close.json()["data"]["code"] == "visit_not_open"
