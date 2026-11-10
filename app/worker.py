"""Worker process entry point: `celery -A app.worker worker -l info`.

Every task's first argument is a context envelope containing `tenant_id` and the
IDs it needs, never patient content. `TenantTask` restores the context before the
body runs and rejects a payload without a tenant (plan section 8). Beat runs the
scheduled queues: `celery -A app.worker beat -l info`.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Celery, Task

from app.core import get_settings
from app.core.context import RequestContext, reset_context, set_context
from app.core.logging import configure_logging

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
)


class TenantTask(Task):  # type: ignore[misc]
    """Base for every job: restores the tenant context from the payload envelope."""

    def __call__(self, *args: Any, context: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if not context or not context.get("tenant_id"):
            raise ValueError(f"job {self.name} payload has no tenant_id; refusing to run")

        token = set_context(
            RequestContext(
                tenant_id=str(context["tenant_id"]),
                request_id=str(context.get("request_id") or f"job_{uuid.uuid4().hex[:8]}"),
                user_id=context.get("user_id"),
                permissions=frozenset(context.get("permissions") or ()),
            )
        )
        try:
            return super().__call__(*args, **kwargs)
        finally:
            reset_context(token)
