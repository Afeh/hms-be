"""Declarative index specs shared by migrations and integration tests.

Keeping the keys here means the migration, the repositories' test helper and the
TTL assertions cannot drift. Migrations apply them through a synchronous PyMongo
database; tests await the Motor equivalent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymongo import ASCENDING, DESCENDING

IDENTITIES_COLLECTION = "identities"
USERS_COLLECTION = "users"
ROLES_COLLECTION = "roles"
SESSIONS_COLLECTION = "sessions"
REFRESH_TOKENS_COLLECTION = "refresh_tokens"
PATIENTS_COLLECTION = "patients"
COUNTERS_COLLECTION = "counters"
VISITS_COLLECTION = "visits"
VISIT_ATTACHMENTS_COLLECTION = "visit_attachments"
VITAL_SIGNS_COLLECTION = "vital_signs"


@dataclass(frozen=True, slots=True)
class IndexSpec:
    """One index to create on one collection."""

    collection: str
    keys: tuple[tuple[str, int], ...]
    name: str
    unique: bool = False
    expire_after_seconds: int | None = None
    partial_filter_expression: dict[str, Any] | None = None

    def options(self) -> dict[str, Any]:
        options: dict[str, Any] = {"name": self.name}
        if self.unique:
            options["unique"] = True
        if self.expire_after_seconds is not None:
            options["expireAfterSeconds"] = self.expire_after_seconds
        if self.partial_filter_expression is not None:
            options["partialFilterExpression"] = self.partial_filter_expression
        return options


IDENTITY_ACCESS_INDEXES: tuple[IndexSpec, ...] = (
    IndexSpec(
        collection=IDENTITIES_COLLECTION,
        keys=(("email_normalized", ASCENDING),),
        name="ix_identities_email_normalized_unique",
        unique=True,
    ),
    IndexSpec(
        collection=USERS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("identity_id", ASCENDING)),
        name="ix_users_tenant_identity_unique",
        unique=True,
        partial_filter_expression={"identity_id": {"$type": "string"}},
    ),
    IndexSpec(
        collection=USERS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("status", ASCENDING)),
        name="ix_users_tenant_status",
    ),
    IndexSpec(
        collection=USERS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("invited_email_normalized", ASCENDING)),
        name="ix_users_pending_invitation_unique",
        unique=True,
        partial_filter_expression={"status": "invited"},
    ),
    IndexSpec(
        collection=ROLES_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("key", ASCENDING)),
        name="ix_roles_tenant_key_unique",
        unique=True,
    ),
    IndexSpec(
        collection=SESSIONS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("user_id", ASCENDING)),
        name="ix_sessions_tenant_user",
    ),
    IndexSpec(
        collection=SESSIONS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("family_id", ASCENDING)),
        name="ix_sessions_tenant_family",
    ),
    IndexSpec(
        collection=SESSIONS_COLLECTION,
        keys=(("absolute_expires_at", ASCENDING),),
        name="ttl_sessions_absolute_expiry",
        expire_after_seconds=86_400,
    ),
    IndexSpec(
        collection=REFRESH_TOKENS_COLLECTION,
        keys=(("token_hash", ASCENDING),),
        name="ix_refresh_tokens_hash_unique",
        unique=True,
    ),
    IndexSpec(
        collection=REFRESH_TOKENS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("session_id", ASCENDING)),
        name="ix_refresh_tokens_tenant_session",
    ),
    IndexSpec(
        collection=REFRESH_TOKENS_COLLECTION,
        keys=(("family_id", ASCENDING),),
        name="ix_refresh_tokens_family",
    ),
    IndexSpec(
        collection=REFRESH_TOKENS_COLLECTION,
        keys=(("expires_at", ASCENDING),),
        name="ttl_refresh_tokens_expiry",
        expire_after_seconds=86_400,
    ),
)

PATIENT_INDEXES: tuple[IndexSpec, ...] = (
    IndexSpec(
        collection=PATIENTS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("patient_number", ASCENDING)),
        name="ix_patients_tenant_number_unique",
        unique=True,
    ),
    IndexSpec(
        collection=PATIENTS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("status", ASCENDING)),
        name="ix_patients_tenant_status",
    ),
    IndexSpec(
        collection=PATIENTS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("registration_date", DESCENDING)),
        name="ix_patients_tenant_registration_date",
    ),
    IndexSpec(
        collection=COUNTERS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("key", ASCENDING)),
        name="ix_counters_tenant_key_unique",
        unique=True,
    ),
)


VISIT_ATTACHMENT_INDEXES: tuple[IndexSpec, ...] = (
    IndexSpec(
        collection=VISIT_ATTACHMENTS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("visit_id", ASCENDING), ("created_at", ASCENDING)),
        name="ix_visit_attachments_tenant_visit_created",
    ),
    IndexSpec(
        collection=VISIT_ATTACHMENTS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("storage_key", ASCENDING)),
        name="ix_visit_attachments_tenant_storage_key_unique",
        unique=True,
    ),
)


VISIT_INDEXES: tuple[IndexSpec, ...] = (
    IndexSpec(
        collection=VISITS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("patient_id", ASCENDING), ("status", ASCENDING)),
        name="ix_visits_tenant_patient_status",
    ),
    IndexSpec(
        collection=VISITS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("patient_id", ASCENDING), ("visit_date", DESCENDING)),
        name="ix_visits_tenant_patient_date",
    ),
    IndexSpec(
        collection=VITAL_SIGNS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("visit_id", ASCENDING)),
        name="ix_vital_signs_tenant_visit",
    ),
    IndexSpec(
        collection=VITAL_SIGNS_COLLECTION,
        keys=(("tenant_id", ASCENDING), ("patient_id", ASCENDING), ("measured_at", DESCENDING)),
        name="ix_vital_signs_tenant_patient_measured",
    ),
)


def create_indexes_sync(db: Any) -> None:
    """Apply every identity/access spec to a PyMongo database (migration 0005)."""
    for spec in IDENTITY_ACCESS_INDEXES:
        db[spec.collection].create_index(list(spec.keys), **spec.options())


async def create_indexes(db: Any) -> None:
    """Apply every identity/access spec to a Motor database (integration tests)."""
    for spec in IDENTITY_ACCESS_INDEXES:
        await db[spec.collection].create_index(list(spec.keys), **spec.options())


def create_patient_indexes_sync(db: Any) -> None:
    """Apply patient and counter indexes to a PyMongo database (migration 0006)."""
    for spec in PATIENT_INDEXES:
        db[spec.collection].create_index(list(spec.keys), **spec.options())


async def create_patient_indexes(db: Any) -> None:
    """Apply patient and counter indexes to a Motor database (integration tests)."""
    for spec in PATIENT_INDEXES:
        await db[spec.collection].create_index(list(spec.keys), **spec.options())


def create_visit_indexes_sync(db: Any) -> None:
    """Apply visit and vital-sign indexes to a PyMongo database (migration 0007)."""
    for spec in VISIT_INDEXES:
        db[spec.collection].create_index(list(spec.keys), **spec.options())


async def create_visit_indexes(db: Any) -> None:
    """Apply visit and vital-sign indexes to a Motor database (integration tests)."""
    for spec in VISIT_INDEXES:
        await db[spec.collection].create_index(list(spec.keys), **spec.options())


def create_visit_attachment_indexes_sync(db: Any) -> None:
    """Apply visit-attachment indexes to a PyMongo database (migration 0008)."""
    for spec in VISIT_ATTACHMENT_INDEXES:
        db[spec.collection].create_index(list(spec.keys), **spec.options())


async def create_visit_attachment_indexes(db: Any) -> None:
    """Apply visit-attachment indexes to a Motor database (integration tests)."""
    for spec in VISIT_ATTACHMENT_INDEXES:
        await db[spec.collection].create_index(list(spec.keys), **spec.options())


def index_for(collection: str, name: str) -> IndexSpec:
    """Look up a spec by collection and index name, for tests."""
    for spec in (
        *IDENTITY_ACCESS_INDEXES,
        *PATIENT_INDEXES,
        *VISIT_INDEXES,
        *VISIT_ATTACHMENT_INDEXES,
    ):
        if spec.collection == collection and spec.name == name:
            return spec
    raise KeyError(f"no index {name!r} on {collection!r}")


__all__ = [
    "COUNTERS_COLLECTION",
    "IDENTITIES_COLLECTION",
    "IDENTITY_ACCESS_INDEXES",
    "PATIENTS_COLLECTION",
    "PATIENT_INDEXES",
    "REFRESH_TOKENS_COLLECTION",
    "ROLES_COLLECTION",
    "SESSIONS_COLLECTION",
    "USERS_COLLECTION",
    "VISITS_COLLECTION",
    "VISIT_ATTACHMENTS_COLLECTION",
    "VISIT_ATTACHMENT_INDEXES",
    "VISIT_INDEXES",
    "VITAL_SIGNS_COLLECTION",
    "IndexSpec",
    "create_indexes",
    "create_indexes_sync",
    "create_patient_indexes",
    "create_patient_indexes_sync",
    "create_visit_attachment_indexes",
    "create_visit_attachment_indexes_sync",
    "create_visit_indexes",
    "create_visit_indexes_sync",
    "index_for",
]
