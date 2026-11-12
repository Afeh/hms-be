"""Data access: this package is the only importer of the MongoDB driver."""

from app.core.errors import DuplicateEntityError, QuerySanitizationError, VersionConflictError
from app.db.cache import create_redis, tenant_key
from app.db.client import TenantConnectionRegistry
from app.db.outbox import OUTBOX_COLLECTION, OutboxRepository
from app.db.repository import TenantRepository
from app.db.tenancy import (
    TENANT_EXEMPT_COLLECTIONS,
    TenantCollection,
    TenantDocument,
    TenantIsolationError,
    run_as_system,
    sanitize_query,
)

__all__ = [
    "OUTBOX_COLLECTION",
    "TENANT_EXEMPT_COLLECTIONS",
    "DuplicateEntityError",
    "OutboxRepository",
    "QuerySanitizationError",
    "TenantCollection",
    "TenantConnectionRegistry",
    "TenantDocument",
    "TenantIsolationError",
    "TenantRepository",
    "VersionConflictError",
    "create_redis",
    "run_as_system",
    "sanitize_query",
    "tenant_key",
]
