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
from app.db.tenant_configs import TENANT_CONFIGS_COLLECTION, TenantConfigStore
from app.db.tenants import TENANTS_COLLECTION, TenantStore

__all__ = [
    "OUTBOX_COLLECTION",
    "TENANTS_COLLECTION",
    "TENANT_CONFIGS_COLLECTION",
    "TENANT_EXEMPT_COLLECTIONS",
    "DuplicateEntityError",
    "OutboxRepository",
    "QuerySanitizationError",
    "TenantCollection",
    "TenantConfigStore",
    "TenantConnectionRegistry",
    "TenantDocument",
    "TenantIsolationError",
    "TenantRepository",
    "TenantStore",
    "VersionConflictError",
    "create_redis",
    "run_as_system",
    "sanitize_query",
    "tenant_key",
]
