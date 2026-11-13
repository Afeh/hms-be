"""MongoDB document models, subclassing `app.db.tenancy.TenantDocument`.

Response bodies belong in `app.schemas`; these are the stored shapes.
"""

from app.models.outbox import OutboxEvent
from app.models.tenant import Tenant
from app.models.tenant_config import TenantConfig

__all__ = ["OutboxEvent", "Tenant", "TenantConfig"]
