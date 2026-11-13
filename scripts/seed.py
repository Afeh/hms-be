"""Idempotent development database seeder (plan §14 M1, issue 0010).

Creates:
  - Two tenants: "acme" (US/Eastern) and "beta" (Europe/London)
  - One config document per tenant (issue 0011)
  - Five default roles per tenant (10 roles total)
  - One Hospital Administrator identity for each tenant with MFA disabled

Refuses to run in production. Safe to run repeatedly without creating duplicates.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import MongoClient

from app.core.config import get_settings
from app.db.tenancy import run_as_system
from app.models.tenant_config import PatientIdFormat, generate_patient_id

# Known local credentials for development
ADMIN_PASSWORD = "Password123!"  # noqa: S105


# Password hash for development (SHA-256 for M1 bootstrap; argon2id in 0013)
def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Default role templates (plan §6.3, §18)
# ---------------------------------------------------------------------------

ROLE_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "hospital_admin",
        "name": "Hospital Administrator",
        "permissions": ["user:*", "role:*", "tenant:*", "audit:*", "patient:read:all"],
        "is_system": True,
        "mfa_required": False,  # Local development admin has MFA disabled
    },
    {
        "key": "medical_director",
        "name": "Medical Director",
        "permissions": [
            "patient:read",
            "visit:read",
            "clinical:read",
            "document:read",
            "patient:read:all",
        ],
        "is_system": True,
        "mfa_required": True,
    },
    {
        "key": "doctor",
        "name": "Doctor",
        "permissions": [
            "patient:read",
            "patient:update",
            "visit:create",
            "visit:read",
            "visit:update",
            "visit:close",
            "vitals:write",
            "clinical:read",
            "clinical:write",
            "clinical:sign",
            "document:read",
            "document:capture",
            "document:tag",
            "extraction:request",
            "extraction:review",
            "access:breakglass",
        ],
        "is_system": True,
        "mfa_required": True,
    },
    {
        "key": "nurse",
        "name": "Nurse",
        "permissions": [
            "patient:read",
            "visit:create",
            "visit:read",
            "vitals:write",
            "document:capture",
            "document:read",
            "document:tag",
            "clinical:read",
        ],
        "is_system": True,
        "mfa_required": True,
    },
    {
        "key": "records_clerk",
        "name": "Records Clerk",
        "permissions": [
            "patient:create",
            "patient:read",
            "patient:update",
            "patient:merge",
            "patient:read:all",
            "document:capture",
            "document:read",
            "document:tag",
            "extraction:request",
            "extraction:review",
        ],
        "is_system": True,
        "mfa_required": False,
    },
]

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
                        "visit_types": [],
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
                            "password_min_length": 8,
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
                    db["tenant_configs"].update_one(
                        {"tenant_id": tenant_id},
                        {
                            "$set": {
                                "patient_id_format.prefix": code,
                                "patient_id_format.pattern": "{PREFIX}-{RANDOM:5}-{YYYY}",
                            }
                        },
                    )

                # 3. Upsert Roles
                admin_role_id: ObjectId | None = None
                for r_tmpl in ROLE_TEMPLATES:
                    role_doc = dict(r_tmpl)
                    role_doc["tenant_id"] = tenant_id
                    existing_role = db["roles"].find_one(
                        {"tenant_id": tenant_id, "key": role_doc["key"]}
                    )
                    if existing_role is None:
                        res = db["roles"].insert_one(role_doc)
                        if role_doc["key"] == "hospital_admin":
                            admin_role_id = res.inserted_id
                    else:
                        if role_doc["key"] == "hospital_admin":
                            admin_role_id = existing_role["_id"]

                # 4. Upsert Admin Identity
                admin_email = str(t_spec["admin_email"])
                existing_identity = db["identities"].find_one({"email_normalized": admin_email})
                if existing_identity is None:
                    identity_doc = {
                        "email": admin_email,
                        "email_normalized": admin_email.lower().strip(),
                        "name": t_spec["admin_name"],
                        "password_hash": _hash_password(ADMIN_PASSWORD),
                        "mfa": {"enabled": False, "recovery_code_hashes": []},
                        "status": "active",
                        "email_verified_at": now,
                        "created_at": now,
                    }
                    id_res = db["identities"].insert_one(identity_doc)
                    identity_id = id_res.inserted_id
                else:
                    identity_id = existing_identity["_id"]

                # 5. Upsert User Membership
                existing_user = db["users"].find_one(
                    {"tenant_id": tenant_id, "identity_id": identity_id}
                )
                if existing_user is None:
                    user_doc = {
                        "tenant_id": tenant_id,
                        "identity_id": identity_id,
                        "role_ids": [admin_role_id] if admin_role_id else [],
                        "status": "active",
                        "perm_version": 1,
                        "version": 1,
                        "created_at": now,
                    }
                    db["users"].insert_one(user_doc)

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
            print(f"  Admin Email:       {s['admin_email']}")
            print(f"  Admin Password:    {s['admin_password']}")

        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(seed())
