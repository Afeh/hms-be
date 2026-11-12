"""Redis fixed-window rate limiter (plan section 12, issue 0006).

Fail-open policy
----------------
When Redis is unavailable the limiter logs a warning and allows the request
through, so a Redis blip cannot take down authentication endpoints. This is a
deliberate design choice: availability beats strict enforcement for anonymous
routes. The quota enforcement gap is acceptable; a full outage is not.

Key scheme
----------
  ``rl:{name}:{tenant_id or ip}:{window_slot}``

``window_slot`` is ``int(time.time()) // window_seconds``, so each window is an
aligned fixed bucket. Collisions within the same slot accumulate via INCR; the
key expires at ``window_seconds * 2`` so Redis reclaims it promptly.

Retry-After
-----------
The ``Retry-After`` response header is set to the number of seconds until the
end of the current window (``window_seconds - (now % window_seconds)``).
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from fastapi import Depends, Request
from redis.asyncio import Redis

from app.core.context import current_context
from app.core.errors import ApiError

logger = logging.getLogger(__name__)


def _get_redis(request: Request) -> Redis:
    """Return the Redis client from app state (same as api/deps.get_redis)."""
    client: Redis = request.app.state.redis
    return client


def rate_limit(
    name: str,
    limit: int,
    window_seconds: int,
    *,
    key_func: Callable[[Request], str | Awaitable[str | None] | None] | None = None,
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Return a FastAPI dependency that enforces a fixed-window rate limit.

    Parameters
    ----------
    name:
        A short slug identifying the guarded endpoint, e.g. ``"login_ip"``.
        Used in the Redis key so limits are independent per endpoint.
    limit:
        Maximum number of requests allowed within ``window_seconds``.
    window_seconds:
        Length of the sliding window in seconds.
    key_func:
        Optional custom discriminator callable receiving the request (e.g.,
        extracting an email from the body). When omitted, discriminator defaults
        to tenant ID (when in context) and/or user ID, falling back to client IP.

    Usage::

        @router.post("/login")
        async def login(
            _: Annotated[None, Depends(rate_limit("login_ip", limit=10, window_seconds=60))],
            body: LoginBody,
        ) -> ...:
            ...

    Rate-limit key includes the tenant ID when a request context is available,
    falling back to the client IP. The IP is read from ``get_context().ip``
    (populated by ``RequestContextMiddleware``) — routes never read
    ``request.client`` directly.
    """

    async def _dependency(
        request: Request,
        redis: Redis = Depends(_get_redis),
    ) -> None:
        if key_func is not None:
            custom = key_func(request)
            if inspect.isawaitable(custom):
                custom = await custom
            discriminator = str(custom) if custom else "unknown"
        else:
            ctx = current_context()
            if ctx is not None and ctx.tenant_id:
                discriminator = f"{ctx.tenant_id}:{ctx.user_id}" if ctx.user_id else ctx.tenant_id
            elif ctx is not None and (ctx.user_id or ctx.ip):
                discriminator = ctx.user_id or ctx.ip or "unknown"
            else:
                client = request.scope.get("client")
                discriminator = client[0] if client else "unknown"

        now = int(time.time())
        window_slot = now // window_seconds
        key = f"rl:{name}:{discriminator}:{window_slot}"
        retry_after = window_seconds - (now % window_seconds)

        try:
            count = await redis.incr(key)
            if count == 1:
                # First request in this window — set TTL. 2x the window so the
                # key is reclaimed soon after the window ends.
                await redis.expire(key, window_seconds * 2)
        except Exception:
            logger.warning(
                "rate limiter: Redis unavailable, failing open — name=%s discriminator=%s",
                name,
                discriminator,
            )
            return  # fail-open: allow the request through

        if count > limit:
            raise ApiError(
                429,
                "rate_limited",
                f"Too many requests — try again in {retry_after} seconds",
                headers={"Retry-After": str(retry_after)},
            )

    return _dependency
