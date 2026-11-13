"""Tenant profile service (issue 0010).

Retrieves and serialises the current tenant's profile.
"""

from __future__ import annotations

from app.core.errors import ApiError
from app.db.tenants import TenantStore
from app.models.tenant import Tenant
from app.schemas.tenant import TenantProfileResponse


def tenant_to_profile(tenant: Tenant) -> TenantProfileResponse:
    """Map a Tenant document to the public profile response."""
    return TenantProfileResponse(
        id=tenant.id or "",
        name=tenant.name,
        slug=tenant.slug,
        code=tenant.code,
        status=tenant.status,
        plan=tenant.plan,
        country=tenant.country,
        timezone=tenant.timezone,
        legal_hold=tenant.legal_hold,
    )


async def get_tenant_profile(tenant_id_or_slug: str, store: TenantStore) -> TenantProfileResponse:
    """Load the tenant by ID or slug and return its profile.

    Raises ``ApiError(404)`` when the tenant is unknown.
    """
    tenant = await store.get_by_id(tenant_id_or_slug)
    if tenant is None:
        tenant = await store.get_by_slug(tenant_id_or_slug)
    if tenant is None:
        raise ApiError(404, "not_found", f"Tenant {tenant_id_or_slug!r} not found")
    return tenant_to_profile(tenant)


__all__ = ["get_tenant_profile", "tenant_to_profile"]
