"""Enqueueing and draining `outbox` rows.

`enqueue` runs inside the caller's MongoDB transaction, next to the state change
that needs the side effect, so a request that rolls back leaves no email and no
job behind. `OutboxDispatcher` is the only other writer: it leases a row, runs
the registered handler, then marks the row sent or schedules the next attempt
with exponential backoff (plan section 12, "Transactional outbox").

Delivery is at-least-once. A worker that dies between the handler and
`mark_sent` gets the row back once the lease expires, so handlers receive a
stable `event.id` they may use as an idempotency key with an external provider.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from redis.asyncio import Redis

from app.core import get_settings
from app.core.context import MissingContextError, get_context
from app.db.cache import create_redis, tenant_key
from app.db.client import TenantConnectionRegistry
from app.db.outbox import OUTBOX_COLLECTION, OutboxRepository
from app.db.tenancy import TenantCollection
from app.models.outbox import OutboxEvent

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_BASE_DELAY = timedelta(seconds=30)
DEFAULT_MAX_DELAY = timedelta(hours=1)
DEFAULT_LEASE = timedelta(seconds=120)
MAX_ERROR_LENGTH = 500


class PermanentFailureError(RuntimeError):
    """A handler that cannot succeed, however often it is retried."""


@dataclass(frozen=True, slots=True)
class HandlerDeps:
    """What a handler may use beyond the event itself."""

    tenant_id: str
    redis: Redis | None = None


OutboxHandler = Callable[[OutboxEvent, HandlerDeps], Awaitable[None]]

_HANDLERS: dict[str, OutboxHandler] = {}


def handler(event_type: str) -> Callable[[OutboxHandler], OutboxHandler]:
    """Register the handler for one event type, so the dispatcher needs no edit."""

    def register(func: OutboxHandler) -> OutboxHandler:
        if event_type in _HANDLERS:
            raise ValueError(f"a handler is already registered for {event_type!r}")
        _HANDLERS[event_type] = func
        return func

    return register


def handler_for(event_type: str) -> OutboxHandler | None:
    return _HANDLERS.get(event_type)


def registered_types() -> frozenset[str]:
    return frozenset(_HANDLERS)


def _mask(address: str) -> str:
    """Enough of an address to debug with, never enough to reuse."""
    local, _, domain = address.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


@handler("email.send")
async def send_email(event: OutboxEvent, deps: HandlerDeps) -> None:
    """Log-only delivery until 0015 provides the sender and the provider adapter."""
    del deps
    recipient = str(event.payload.get("to") or "")
    template = str(event.payload.get("template") or "")
    if not recipient or not template:
        raise PermanentFailureError("email.send needs 'to' and 'template' in its payload")
    logger.info(
        "outbox email (log-only provider)",
        extra={"template": template, "recipient": _mask(recipient), "outbox_id": event.id},
    )


@handler("cache.invalidate")
async def invalidate_cache(event: OutboxEvent, deps: HandlerDeps) -> None:
    """Delete tenant-prefixed cache keys, e.g. after a tenant config write."""
    if deps.redis is None:
        raise RuntimeError("cache.invalidate needs a Redis client")
    keys = [str(key) for key in event.payload.get("keys") or ()]
    if not keys:
        raise PermanentFailureError("cache.invalidate needs 'keys' in its payload")
    deleted = await deps.redis.delete(*(tenant_key(deps.tenant_id, key) for key in keys))
    logger.info("outbox cache invalidated", extra={"keys": keys, "deleted": deleted})


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Exponential backoff with a cap, and the attempt count that parks a row."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base: timedelta = DEFAULT_BASE_DELAY
    cap: timedelta = DEFAULT_MAX_DELAY

    def next_attempt(self, attempts: int, *, now: datetime) -> Attempt:
        attempt = attempts + 1
        if attempt >= self.max_attempts:
            return Attempt(attempts=attempt, available_at=now + self.cap, parked=True)
        delay = min(self.base * 2 ** (attempt - 1), self.cap)
        return Attempt(attempts=attempt, available_at=now + delay, parked=False)


@dataclass(frozen=True, slots=True)
class Attempt:
    attempts: int
    available_at: datetime
    parked: bool


@dataclass(slots=True)
class DispatchReport:
    claimed: int = 0
    sent: int = 0
    failed: int = 0
    parked: int = 0


class OutboxStore(Protocol):
    """The repository calls the dispatcher needs, which a test can fake."""

    async def claim_due(
        self, *, now: datetime, limit: int, worker_id: str, lease: timedelta
    ) -> list[OutboxEvent]: ...

    async def mark_sent(self, event_id: str, *, now: datetime, worker_id: str) -> bool: ...

    async def record_failure(
        self,
        event_id: str,
        *,
        now: datetime,
        worker_id: str,
        attempts: int,
        available_at: datetime,
        parked: bool,
        error: str,
    ) -> bool: ...


class OutboxDispatcher:
    """Drains one tenant's due rows; construct it inside that tenant's context."""

    def __init__(
        self,
        store: OutboxStore,
        *,
        redis: Redis | None = None,
        backoff: BackoffPolicy | None = None,
        lease: timedelta = DEFAULT_LEASE,
        batch_size: int = 50,
        clock: Callable[[], datetime] | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._store = store
        self._redis = redis
        self._backoff = backoff or BackoffPolicy()
        self._lease = lease
        self._batch_size = batch_size
        self._clock = clock or (lambda: datetime.now(UTC))
        self._worker_id = worker_id or f"worker_{uuid.uuid4().hex[:8]}"

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch_due(self) -> DispatchReport:
        """Claim what is due and settle it; safe to run every 30 seconds."""
        events = await self._store.claim_due(
            now=self._clock(),
            limit=self._batch_size,
            worker_id=self._worker_id,
            lease=self._lease,
        )
        report = DispatchReport(claimed=len(events))
        for event in events:
            await self._dispatch_one(event, report)
        if events:
            logger.info(
                "outbox dispatch finished",
                extra={
                    "claimed": report.claimed,
                    "sent": report.sent,
                    "failed": report.failed,
                    "parked": report.parked,
                },
            )
        return report

    async def _dispatch_one(self, event: OutboxEvent, report: DispatchReport) -> None:
        event_id = event.id or ""
        try:
            run = handler_for(event.type)
            if run is None:
                raise PermanentFailureError(f"no handler registered for type {event.type!r}")
            await run(event, HandlerDeps(tenant_id=event.tenant_id, redis=self._redis))
        except Exception as exc:
            parked = await self._record_failure(event, exc)
            report.failed += 1
            report.parked += int(parked)
            return

        settled = await self._store.mark_sent(
            event_id, now=self._clock(), worker_id=self._worker_id
        )
        if settled:
            report.sent += 1
        else:
            # The lease moved on while we were working: another worker owns it now.
            logger.warning(
                "outbox row was settled by another worker",
                extra={"outbox_id": event_id, "type": event.type},
            )

    async def _record_failure(self, event: OutboxEvent, exc: Exception) -> bool:
        now = self._clock()
        attempt = self._backoff.next_attempt(event.attempts, now=now)
        parked = attempt.parked or isinstance(exc, PermanentFailureError)
        error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
        await self._store.record_failure(
            event.id or "",
            now=now,
            worker_id=self._worker_id,
            attempts=attempt.attempts,
            available_at=attempt.available_at,
            parked=parked,
            error=error,
        )
        extra = {
            "outbox_id": event.id,
            "type": event.type,
            "attempts": attempt.attempts,
            "last_error": error,
        }
        if parked:
            logger.error("outbox row parked and will not be retried", extra=extra)
        else:
            logger.warning("outbox handler failed; retrying later", extra=extra)
        return parked


async def enqueue(
    repository: OutboxRepository | TenantCollection,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    session: Any = None,
    delay: timedelta = timedelta(0),
    clock: Callable[[], datetime] | None = None,
) -> str:
    """Write one outbox row inside the caller's transaction.

    `session` must be the session that transaction runs in; without it the row is
    written outside the transaction and a later rollback keeps the side effect.
    """
    ctx = get_context()
    if ctx.tenant_id is None:
        raise MissingContextError("enqueuing an outbox event requires a tenant context")
    repo = OutboxRepository(repository) if isinstance(repository, TenantCollection) else repository
    now = (clock or (lambda: datetime.now(UTC)))()
    event = OutboxEvent(
        tenant_id=ctx.tenant_id,
        type=event_type,
        payload=dict(payload),
        created_at=now,
        available_at=now + delay,
    )
    event_id = await repo.enqueue(event, session=session)
    logger.info("outbox event enqueued", extra={"outbox_id": event_id, "type": event_type})
    return event_id


async def dispatch_for_tenant(
    tenant_id: str,
    *,
    registry: TenantConnectionRegistry | None = None,
    redis: Redis | None = None,
    backoff: BackoffPolicy | None = None,
    lease: timedelta = DEFAULT_LEASE,
    batch_size: int = 50,
    clock: Callable[[], datetime] | None = None,
    worker_id: str | None = None,
) -> DispatchReport:
    """Drain due outbox events for one tenant."""
    settings = get_settings()
    created_registry = False
    created_redis = False

    if registry is None:
        registry = TenantConnectionRegistry(
            settings.mongodb_uri,
            settings.mongodb_database,
            timeout_ms=settings.mongodb_server_selection_timeout_ms,
        )
        created_registry = True

    if redis is None:
        redis = create_redis(settings.redis_url)
        created_redis = True

    try:
        collection = registry.collection(tenant_id, OUTBOX_COLLECTION)
        repo = OutboxRepository(collection)
        dispatcher = OutboxDispatcher(
            repo,
            redis=redis,
            backoff=backoff,
            lease=lease,
            batch_size=batch_size,
            clock=clock,
            worker_id=worker_id,
        )
        return await dispatcher.dispatch_due()
    finally:
        if created_redis and redis is not None:
            await redis.aclose()
        if created_registry and registry is not None:
            await registry.close()


async def sweep_pending_tenants(
    *,
    registry: TenantConnectionRegistry | None = None,
    clock: Callable[[], datetime] | None = None,
) -> list[str]:
    """Find all tenants with unsent, unparked events due for dispatch."""
    settings = get_settings()
    created_registry = False

    if registry is None:
        registry = TenantConnectionRegistry(
            settings.mongodb_uri,
            settings.mongodb_database,
            timeout_ms=settings.mongodb_server_selection_timeout_ms,
        )
        created_registry = True

    try:
        sweeper = OutboxRepository(registry.system_collection(OUTBOX_COLLECTION))
        now = (clock or (lambda: datetime.now(UTC)))()
        return await sweeper.pending_tenant_ids(now=now)
    finally:
        if created_registry and registry is not None:
            await registry.close()


async def sweep_and_dispatch(
    *,
    registry: TenantConnectionRegistry | None = None,
    redis: Redis | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, DispatchReport]:
    """Sweep and immediately dispatch due rows for all pending tenants."""
    settings = get_settings()
    created_registry = False
    created_redis = False

    if registry is None:
        registry = TenantConnectionRegistry(
            settings.mongodb_uri,
            settings.mongodb_database,
            timeout_ms=settings.mongodb_server_selection_timeout_ms,
        )
        created_registry = True

    if redis is None:
        redis = create_redis(settings.redis_url)
        created_redis = True

    try:
        sweeper = OutboxRepository(registry.system_collection(OUTBOX_COLLECTION))
        now = (clock or (lambda: datetime.now(UTC)))()
        tenant_ids = await sweeper.pending_tenant_ids(now=now)
        reports: dict[str, DispatchReport] = {}
        for tenant_id in tenant_ids:
            reports[tenant_id] = await dispatch_for_tenant(
                tenant_id,
                registry=registry,
                redis=redis,
                clock=clock,
            )
        return reports
    finally:
        if created_redis and redis is not None:
            await redis.aclose()
        if created_registry and registry is not None:
            await registry.close()
