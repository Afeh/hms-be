"""Worker process entry point: `celery -A app.worker worker -l info`.

Every task's first argument is a context envelope containing `tenant_id` and the
IDs it needs, never patient content. `TenantTask` restores the context before the
body runs and rejects a payload without a tenant (plan section 8). Beat runs the
scheduled queues: `celery -A app.worker beat -l info`.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from celery import Celery, Task

from app.core import get_settings
from app.core.context import RequestContext, get_context, reset_context, set_context
from app.core.logging import configure_logging
from app.services.outbox import dispatch_for_tenant, sweep_pending_tenants

settings = get_settings()
configure_logging(settings.log_level)

celery_app = Celery("hms", broker=settings.broker_url, backend=settings.result_backend_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "outbox-sweep-every-30-seconds": {
            "task": "outbox.sweep",
            "schedule": 30.0,
        },
    },
)


class TenantTask(Task):  # type: ignore[misc]
    """Base for every job: restores the tenant context from the payload envelope."""

    def __call__(self, *args: Any, context: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        envelope = context
        if envelope is None and args and isinstance(args[0], dict) and "tenant_id" in args[0]:
            envelope = args[0]

        if not envelope or not envelope.get("tenant_id"):
            raise ValueError(f"job {self.name} payload has no tenant_id; refusing to run")

        token = set_context(
            RequestContext(
                tenant_id=str(envelope["tenant_id"]),
                request_id=str(envelope.get("request_id") or f"job_{uuid.uuid4().hex[:8]}"),
                user_id=envelope.get("user_id"),
                permissions=frozenset(envelope.get("permissions") or ()),
            )
        )
        try:
            return super().__call__(*args, **kwargs)
        finally:
            reset_context(token)


@celery_app.task(name="outbox.dispatch", base=TenantTask)  # type: ignore[untyped-decorator]
def dispatch_outbox(*args: Any, **kwargs: Any) -> dict[str, int]:
    """Drain one tenant's outbox rows; restores context from the payload envelope."""
    ctx = get_context()
    if not ctx.tenant_id:
        raise ValueError("job outbox.dispatch has no tenant context")
    report = asyncio.run(dispatch_for_tenant(ctx.tenant_id))
    return {
        "claimed": report.claimed,
        "sent": report.sent,
        "failed": report.failed,
        "parked": report.parked,
    }


@celery_app.task(name="outbox.sweep")  # type: ignore[untyped-decorator]
def sweep_outbox(*args: Any, **kwargs: Any) -> list[str]:
    """Find tenants with pending outbox rows and fan out one dispatch per tenant."""
    tenant_ids = asyncio.run(sweep_pending_tenants())
    for tenant_id in tenant_ids:
        dispatch_outbox.delay(context={"tenant_id": tenant_id})
    return tenant_ids

