"""Data access: this package is the only importer of the MongoDB driver."""

from app.core.errors import DuplicateEntityError, QuerySanitizationError, VersionConflictError
from app.db.cache import create_redis, tenant_key
from app.db.client import TenantConnectionRegistry
from app.db.identities import IdentityRepository, normalize_email
from app.db.outbox import OUTBOX_COLLECTION, OutboxRepository
from app.db.refresh_tokens import RefreshTokenRepository
from app.db.repository import TenantRepository
from app.db.roles import RoleRepository, RoleStore
from app.db.sessions import SessionRepository, SessionStore
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
from app.db.users import MembershipStore, UserRepository

__all__ = [
    "OUTBOX_COLLECTION",
    "TENANTS_COLLECTION",
    "TENANT_CONFIGS_COLLECTION",
    "TENANT_EXEMPT_COLLECTIONS",
    "DuplicateEntityError",
    "IdentityRepository",
    "MembershipStore",
    "OutboxRepository",
    "QuerySanitizationError",
    "RefreshTokenRepository",
    "RoleRepository",
    "RoleStore",
    "SessionRepository",
    "SessionStore",
    "TenantCollection",
    "TenantConfigStore",
    "TenantConnectionRegistry",
    "TenantDocument",
    "TenantIsolationError",
    "TenantRepository",
    "TenantStore",
    "UserRepository",
    "VersionConflictError",
    "create_redis",
    "normalize_email",
    "run_as_system",
    "sanitize_query",
    "tenant_key",
]
