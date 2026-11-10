"""Data access: this package is the only importer of the MongoDB driver."""

from app.db.client import TenantConnectionRegistry
from app.db.repository import TenantRepository
from app.db.tenancy import (
    TENANT_EXEMPT_COLLECTIONS,
    TenantCollection,
    TenantDocument,
    TenantIsolationError,
    run_as_system,
)

__all__ = [
    "TENANT_EXEMPT_COLLECTIONS",
    "TenantCollection",
    "TenantConnectionRegistry",
    "TenantDocument",
    "TenantIsolationError",
    "TenantRepository",
    "run_as_system",
]
