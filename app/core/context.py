"""Request-scoped tenant and user context.

A `contextvars.ContextVar` follows the request across `await` boundaries, into the
threadpool that runs sync dependencies, and into Celery jobs once the task base
class restores it. The tenant ID is only ever populated from a verified token, a
server-side lookup, or a signed job envelope -- never from a header, path or body.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


class MissingContextError(RuntimeError):
    """Raised when tenant-scoped work runs without a request or job context."""


@dataclass(frozen=True, slots=True)
class RequestContext:
    """The tenant, user and request identifiers in scope for one unit of work."""

    tenant_id: str | None = None
    request_id: str = ""
    user_id: str | None = None
    session_id: str | None = None
    permissions: frozenset[str] = frozenset()
    ip: str | None = None
    device_info: str | None = None

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


_context: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def current_context() -> RequestContext | None:
    """Return the active context, or `None` outside a request or job."""
    return _context.get()


def get_context() -> RequestContext:
    """Return the active context or raise, for code that cannot work without it."""
    ctx = _context.get()
    if ctx is None:
        raise MissingContextError("no request context is set for this unit of work")
    return ctx


def set_context(ctx: RequestContext) -> Token[RequestContext | None]:
    """Bind a context to the current task; keep the token to reset it."""
    return _context.set(ctx)


def reset_context(token: Token[RequestContext | None]) -> None:
    _context.reset(token)
