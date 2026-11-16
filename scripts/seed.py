"""Idempotent development database seeder (plan §14 M1, issue 0010).

Creates:
  - Two tenants: "acme" (US/Eastern) and "beta" (Europe/London)
  - One config document per tenant (issue 0011)
  - Five default roles per tenant (10 roles total)
  - One Hospital Administrator identity for each tenant with MFA disabled

Refuses to run in production. Safe to run repeatedly without creating duplicates.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from pymongo import MongoClient

from app.core.config import get_settings
from app.core.permissions import DEFAULT_ROLE_TEMPLATES
from app.core.security import hash_password
from app.db.identities import normalize_email
from app.db.tenancy import run_as_system
from app.models.tenant_config import PatientIdFormat, generate_patient_id

# Known local credentials for development
ADMIN_PASSWORD = "Password123!"  # noqa: S105

DEFAULT_VISIT_TYPES: list[dict[str, Any]] = [
    {"key": "outpatient", "label": "Outpatient", "active": True, "enabled": True},
    {"key": "inpatient", "label": "Inpatient", "active": True, "enabled": True},
    {"key": "emergency", "label": "Emergency", "active": True, "enabled": True},
]

SAMPLE_PATIENTS: dict[str, list[dict[str, Any]]] = {
    "acme": [
        {
            "given": ["Ada", "Ngozi"],
            "family": "Okafor",
            "sex": "female",
            "date_of_birth": (1991, 4, 18),
            "phone": "+2348031234567",
            "city": "Enugu",
            "state": "Enugu",
        },
        {
            "given": ["Chinedu", "Emeka"],
            "family": "Balogun",
            "sex": "male",
            "date_of_birth": (1985, 11, 2),
            "phone": "+2348062345678",
            "city": "Lagos",
            "state": "Lagos",
        },
        {
            "given": ["Fatima", "Amina"],
            "family": "Bello",
            "sex": "female",
            "date_of_birth": (1998, 7, 25),
            "phone": "+2348093456789",
            "city": "Abuja",
            "state": "FCT",
        },
    ],
    "beta": [
        {
            "given": ["Amara", "Chioma"],
            "family": "Eze",
            "sex": "female",
            "date_of_birth": (1990, 2, 14),
            "phone": "+2347034567890",
            "city": "Onitsha",
            "state": "Anambra",
        },
        {
            "given": ["David", "Adewale"],
            "family": "Cole",
            "sex": "male",
            "date_of_birth": (1979, 9, 9),
            "phone": "+2347055678901",
            "city": "Port Harcourt",
            "state": "Rivers",
        },
        {
            "given": ["Grace", "Nneka"],
            "family": "Ibrahim",
            "sex": "female",
            "date_of_birth": (2001, 12, 30),
            "phone": "+2347066789012",
            "city": "Kano",
            "state": "Kano",
        },
    ],
}

TENANTS_DATA: list[dict[str, Any]] = [
    {
        "slug": "acme",
        "name": "Acme Hospital",
        "code": "ACH",
        "status": "active",
        "plan": "enterprise",
        "country": "US",
        "timezone": "America/New_York",
        "admin_email": "admin@acme.example",
        "admin_name": {"given": "Acme", "family": "Admin"},
    },
    {
        "slug": "beta",
        "name": "Beta Clinic",
        "code": "BSC",
        "status": "active",
        "plan": "standard",
        "country": "GB",
        "timezone": "Europe/London",
        "admin_email": "admin@beta.example",
        "admin_name": {"given": "Beta", "family": "Admin"},
    },
]


def _seed_patients(
    db: Any,
    tenant_id: str,
    slug: str,
    code: str,
    now: datetime,
) -> list[str]:
    """Create the demo patients once per tenant, keyed by a stable legacy number."""
    format_config = PatientIdFormat(prefix=code, pattern="{PREFIX}-{RANDOM:5}-{YYYY}")
    numbers: list[str] = []
    for index, sample in enumerate(SAMPLE_PATIENTS.get(slug, []), start=1):
        legacy_number = f"seed-{slug}-{index:02d}"
        existing = db["patients"].find_one({"tenant_id": tenant_id, "legacy_number": legacy_number})
        if existing is not None:
            numbers.append(str(existing["patient_number"]))
            continue

        patient_number = generate_patient_id(
            format_config,
            registration_year=now.year,
        )
        year, month, day = sample["date_of_birth"]
        given = list(sample["given"])
        family = str(sample["family"])
        db["patients"].insert_one(
            {
                "tenant_id": tenant_id,
                "patient_number": patient_number,
                "status": "active",
                "name": {"given": given, "family": family},
                "date_of_birth": datetime(year, month, day, tzinfo=UTC),
                "dob_approximate": False,
                "sex": sample["sex"],
                "phones": [sample["phone"]],
                "address": {
                    "line1": None,
                    "line2": None,
                    "city": sample["city"],
                    "state": sample["state"],
                    "postal_code": None,
                    "country": "NG",
                },
                "next_of_kin": [],
                "registration_date": now,
                "payer": None,
                "care_team": [],
                "legacy_number": legacy_number,
                "paper_file": False,
                "display_name": f"{' '.join(given)} {family}",
                "version": 1,
                "created_at": now,
            }
        )
        numbers.append(patient_number)
    return numbers


def _seed_visits(
    db: Any,
    tenant_id: str,
    slug: str,
    admin_user_id: str,
    now: datetime,
) -> list[str]:
    """Create one closed demo visit with vitals for each seeded patient."""
    visit_ids: list[str] = []
    patients = list(
        db["patients"]
        .find({"tenant_id": tenant_id, "legacy_number": {"$regex": f"^seed-{slug}-"}})
        .sort("legacy_number", 1)
    )
    for index, patient in enumerate(patients, start=1):
        seed_key = f"seed-{slug}-visit-{index:02d}"
        existing = db["visits"].find_one({"tenant_id": tenant_id, "seed_key": seed_key})
        if existing is not None:
            visit_ids.append(str(existing["_id"]))
            continue

        patient_id = str(patient["_id"])
        visit_date = now - timedelta(days=7 * index)
        visit_result = db["visits"].insert_one(
            {
                "tenant_id": tenant_id,
                "patient_id": patient_id,
                "type": "outpatient",
                "visit_date": visit_date,
                "attending_clinician_id": admin_user_id,
                "status": "closed",
                "reason_for_visit": "Routine follow-up and blood pressure check",
                "closed_at": visit_date + timedelta(minutes=45),
                "closed_by": admin_user_id,
                "seed_key": seed_key,
                "version": 1,
                "created_at": visit_date,
                "updated_at": visit_date + timedelta(minutes=45),
            }
        )
        visit_id = str(visit_result.inserted_id)
        db["vital_signs"].insert_one(
            {
                "tenant_id": tenant_id,
                "patient_id": patient_id,
                "visit_id": visit_id,
                "measured_at": visit_date,
                "recorded_by": admin_user_id,
                "temperature_c": 36.7 + index / 10,
                "pulse_bpm": 72 + index,
                "systolic_mmhg": 118 + index,
                "diastolic_mmhg": 76 + index,
                "weight_kg": 70.0 + index,
                "height_cm": 175.0,
                "bmi": round((70.0 + index) / (1.75 * 1.75), 1),
                "warnings": [],
                "status": "active",
                "seed_key": seed_key,
                "version": 1,
                "created_at": visit_date,
            }
        )
        db["patients"].update_one(
            {"_id": patient["_id"]},
            {"$max": {"last_visit_date": visit_date}},
        )
        visit_ids.append(visit_id)
    return visit_ids


def seed() -> int:
    settings = get_settings()

    # Safety guard: refuse to run in production
    if settings.environment == "production":
        print("ERROR: Refusing to run seed script in production environment!", file=sys.stderr)
        return 1

    client: MongoClient[dict[str, Any]] = MongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
        tz_aware=True,
    )

    try:
        db = client[settings.mongodb_database]
        now = datetime.now(UTC)

        with run_as_system("scripts/seed.py"):
            summary: list[dict[str, str]] = []

            for t_spec in TENANTS_DATA:
                slug = str(t_spec["slug"])
                code = str(t_spec["code"])
                # 1. Upsert Tenant
                existing_tenant = db["tenants"].find_one({"slug": slug})
                if existing_tenant is None:
                    tenant_doc = {
                        "name": str(t_spec["name"]),
                        "slug": slug,
                        "code": code,
                        "status": str(t_spec["status"]),
                        "plan": str(t_spec["plan"]),
                        "country": str(t_spec["country"]),
                        "timezone": str(t_spec["timezone"]),
                        "database": {"mode": "shared", "secret_ref": None},
                        "legal_hold": False,
                        "created_at": now,
                        "version": 1,
                    }
                    res = db["tenants"].insert_one(tenant_doc)
                    tenant_id = str(res.inserted_id)
                else:
                    tenant_id = str(existing_tenant["_id"])
                    db["tenants"].update_one(
                        {"_id": existing_tenant["_id"]},
                        {"$set": {"code": code}},
                    )

                # 2. Upsert TenantConfig
                existing_config = db["tenant_configs"].find_one({"tenant_id": tenant_id})
                if existing_config is None:
                    config_doc = {
                        "tenant_id": tenant_id,
                        "branding": {
                            "display_name": t_spec["name"],
                            "logo_key": None,
                            "primary_color": None,
                        },
                        "modules": {
                            "ai_extraction": False,
                            "icd_coding": False,
                            "insurance": False,
                        },
                        "patient_id_format": {
                            "prefix": code,
                            "pattern": "{PREFIX}-{RANDOM:5}-{YYYY}",
                            "sequence_start": 1,
                            "reset_sequence": "never",
                        },
                        "document_types": [],
                        "visit_types": DEFAULT_VISIT_TYPES,
                        "ai": {
                            "enabled": False,
                            "provider": None,
                            "model": None,
                            "notice_version": None,
                            "max_pages_per_document": 10,
                        },
                        "security": {
                            "idle_timeout_min": 30,
                            "absolute_timeout_hours": 8,
                            "password_min_length": 12,
                            "break_glass_minutes": 15,
                        },
                        "retention": {
                            "clinical_years": 15,
                            "audit_years": 7,
                            "document_years": 15,
                            "outbox_days": 30,
                        },
                        "version": 1,
                        "created_at": now,
                    }
                    db["tenant_configs"].insert_one(config_doc)
                else:
                    config_updates: dict[str, Any] = {
                        "patient_id_format.prefix": code,
                        "patient_id_format.pattern": "{PREFIX}-{RANDOM:5}-{YYYY}",
                        "security.password_min_length": 12,
                    }
                    if not existing_config.get("visit_types"):
                        config_updates["visit_types"] = DEFAULT_VISIT_TYPES
                    db["tenant_configs"].update_one(
                        {"tenant_id": tenant_id},
                        {"$set": config_updates},
                    )

                # 3. Upsert Roles. Templates are code-owned (issue 0017), so an
                # existing role is updated in place to match the catalogue.
                admin_role_id: ObjectId | None = None
                for template in DEFAULT_ROLE_TEMPLATES:
                    role_doc: dict[str, Any] = {
                        "tenant_id": tenant_id,
                        "key": template.key,
                        "name": template.name,
                        "permissions": sorted(
                            permission.value for permission in template.permissions
                        ),
                        "is_system": True,
                        "mfa_required": template.mfa_required,
                    }
                    existing_role = db["roles"].find_one(
                        {"tenant_id": tenant_id, "key": template.key}
                    )
                    if existing_role is None:
                        res = db["roles"].insert_one(role_doc)
                        role_id = res.inserted_id
                    else:
                        db["roles"].update_one(
                            {"_id": existing_role["_id"]},
                            {
                                "$set": {
                                    "name": role_doc["name"],
                                    "permissions": role_doc["permissions"],
                                    "is_system": True,
                                    "mfa_required": role_doc["mfa_required"],
                                }
                            },
                        )
                        role_id = existing_role["_id"]
                    if template.key == "hospital_admin":
                        admin_role_id = role_id

                # 4. Upsert Admin Identity with the same argon2id helper login uses.
                admin_email = str(t_spec["admin_email"])
                admin_email_normalized = normalize_email(admin_email)
                admin_name = t_spec["admin_name"]
                if isinstance(admin_name, dict):
                    given = admin_name.get("given")
                    admin_name = {
                        "given": [given] if isinstance(given, str) else given,
                        "family": admin_name.get("family"),
                    }
                existing_identity = db["identities"].find_one(
                    {"email_normalized": admin_email_normalized}
                )
                identity_fields = {
                    "email": admin_email,
                    "email_normalized": admin_email_normalized,
                    "name": admin_name,
                    "password_hash": hash_password(ADMIN_PASSWORD),
                    "password_changed_at": now,
                    "mfa": {"enabled": False, "recovery_code_hashes": []},
                    "status": "active",
                    "email_verified_at": now,
                    "failed_login_count": 0,
                    "updated_at": now,
                }
                if existing_identity is None:
                    identity_doc = {**identity_fields, "created_at": now}
                    id_res = db["identities"].insert_one(identity_doc)
                    identity_id = id_res.inserted_id
                else:
                    identity_id = existing_identity["_id"]
                    db["identities"].update_one(
                        {"_id": identity_id},
                        {"$set": identity_fields, "$unset": {"locked_until": ""}},
                    )
                identity_id_str = str(identity_id)

                # 5. Upsert User Membership. References are stored as strings so
                # the models and API agree; old ObjectId refs are normalised here.
                existing_user = db["users"].find_one(
                    {
                        "tenant_id": tenant_id,
                        "identity_id": {"$in": [identity_id, identity_id_str]},
                    }
                )
                role_ids = [str(admin_role_id)] if admin_role_id else []
                if existing_user is None:
                    user_doc = {
                        "tenant_id": tenant_id,
                        "identity_id": identity_id_str,
                        "role_ids": role_ids,
                        "status": "active",
                        "perm_version": 1,
                        "version": 1,
                        "created_at": now,
                    }
                    user_result = db["users"].insert_one(user_doc)
                    admin_user_id = str(user_result.inserted_id)
                else:
                    admin_user_id = str(existing_user["_id"])
                    db["users"].update_one(
                        {"_id": existing_user["_id"]},
                        {
                            "$set": {
                                "identity_id": identity_id_str,
                                "role_ids": role_ids,
                                "status": "active",
                            }
                        },
                    )

                sample_numbers = _seed_patients(db, tenant_id, slug, code, now)
                sample_visit_ids = _seed_visits(db, tenant_id, slug, admin_user_id, now)
                sample_id = generate_patient_id(
                    PatientIdFormat(prefix=code, pattern="{PREFIX}-{RANDOM:5}-{YYYY}")
                )
                summary.append(
                    {
                        "tenant": slug,
                        "name": str(t_spec["name"]),
                        "code": code,
                        "tenant_id": tenant_id,
                        "sample_patient_id": sample_id,
                        "sample_patient_numbers": ", ".join(sample_numbers),
                        "sample_visit_ids": ", ".join(sample_visit_ids),
                        "admin_email": admin_email,
                        "admin_password": ADMIN_PASSWORD,
                    }
                )

        print("\n=== Seeding completed successfully ===")
        for s in summary:
            print(f"\nTenant: {s['name']} (slug: {s['tenant']})")
            print(f"  Tenant ID:         {s['tenant_id']}")
            print(f"  Hospital Code:     {s['code']}")
            print(f"  Sample Patient ID: {s['sample_patient_id']}")
            print(f"  Seeded Patients:   {s['sample_patient_numbers']}")
            print(f"  Seeded Visits:     {s['sample_visit_ids']}")
            print(f"  Admin Email:       {s['admin_email']}")
            print(f"  Admin Password:    {s['admin_password']}")

        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(seed())
