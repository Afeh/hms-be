"""MongoDB document models, subclassing `app.db.tenancy.TenantDocument`.

Response bodies belong in `app.schemas`; these are the stored shapes.
"""

from app.models.outbox import OutboxEvent

__all__ = ["OutboxEvent"]
