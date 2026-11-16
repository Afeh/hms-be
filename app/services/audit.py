"""Minimal audit interface for the demo (issue 0021 is deferred).

Authentication calls this single function. It writes a structured log now and can
be backed by the real audit writer later without changing the call sites.
"""

from __future__ import annotations

import logging
from typing import Literal

from app.core.context import current_context

logger = logging.getLogger("audit")

AuditOutcome = Literal["success", "failure"]


def record(
    action: str,
    *,
    outcome: AuditOutcome = "success",
    tenant_id: str | None = None,
    actor_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Record an audit event without passwords, tokens or patient data."""
    context = current_context()
    logger.info(
        "audit event",
        extra={
            "audit_action": action,
            "audit_outcome": outcome,
            "tenant_id": tenant_id,
            "user_id": actor_id,
            "request_id": context.request_id if context else "",
            "reason": reason,
        },
    )


__all__ = ["record"]
