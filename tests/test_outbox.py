"""Outbox dispatch: the handler registry, backoff, retries and the park cap.
 
The store is in-memory here; transactions and leases against a real replica set
are in `test_outbox_integration.py`.
"""
 
from __future__ import annotations
 
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast
 
import pytest
 
from app.core.context import RequestContext
from app.models.outbox import OutboxEvent
from app.services.outbox import (
    Attempt,
    BackoffPolicy,
    DispatchReport,
    HandlerDeps,
    OutboxDispatcher,
    PermanentFailureError,
    handler,
    invalidate_cache,
    send_email,
)
 
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
 
HANDLED: list[str] = []
 
 
@handler("test.recorded")
async def _record(event: OutboxEvent, deps: HandlerDeps) -> None:
    HANDLED.append(f"{deps.tenant_id}:{event.payload['n']}")
 
 
@handler("test.always-fails")
async def _always_fails(event: OutboxEvent, deps: HandlerDeps) -> None:
    del event, deps
    raise RuntimeError("smtp is down")
 
 
def make_event(event_type: str = "test.recorded", **overrides: Any) -> OutboxEvent:
    values: dict[str, Any] = {
        "id": "evt_0001",
        "tenant_id": "t_alpha",
        "type": event_type,
        "payload": {"n": 1},
        "created_at": NOW,
        "available_at": NOW,
    }
    values.update(overrides)
    return OutboxEvent(**values)
 
 
class FakeRedis:
    """Records the keys a handler asked to delete."""
 
    def __init__(self) -> None:
        self.deleted: list[str] = []
 
    async def delete(self, *keys: str) -> int:
        self.deleted.extend(keys)
        return len(keys)
 
 
class FakeOutboxStore:
    """The three dispatcher calls over a dict, with the real due and lease rules."""
 
    def __init__(self, *events: OutboxEvent) -> None:
        self.events = {str(item.id): item for item in events}
 
    async def claim_due(
        self, *, now: datetime, limit: int, worker_id: str, lease: timedelta
    ) -> list[OutboxEvent]:
        claimed: list[OutboxEvent] = []
        for item in self.events.values():
            if len(claimed) >= limit:
                break
            if item.sent_at is not None or item.parked_at is not None:
                continue
            if item.available_at > now:
                continue
            if item.locked_until is not None and item.locked_until > now:
                continue
            item.locked_until = now + lease
            item.locked_by = worker_id
            claimed.append(item)
        return claimed
 
    async def mark_sent(self, event_id: str, *, now: datetime, worker_id: str) -> bool:
        item = self.events.get(event_id)
        if item is None or item.sent_at is not None or item.locked_by != worker_id:
            return False
        item.sent_at = now
        item.locked_until = None
        item.locked_by = None
        return True
 
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
    ) -> bool:
        item = self.events.get(event_id)
        if item is None or item.sent_at is not None or item.locked_by != worker_id:
            return False
        item.attempts = attempts
        item.available_at = available_at
        item.parked_at = now if parked else None
        item.last_error = error
        item.locked_until = None
        item.locked_by = None
        return True
 
 
@pytest.fixture(autouse=True)
def _reset_handled() -> None:
    HANDLED.clear()
 
 
def test_backoff_doubles_to_the_cap_and_then_parks() -> None:
    policy = BackoffPolicy(max_attempts=4, base=timedelta(seconds=30), cap=timedelta(minutes=2))
 
    assert policy.next_attempt(0, now=NOW) == Attempt(1, NOW + timedelta(seconds=30), False)
    assert policy.next_attempt(1, now=NOW) == Attempt(2, NOW + timedelta(seconds=60), False)
    assert policy.next_attempt(2, now=NOW) == Attempt(3, NOW + timedelta(minutes=2), False)
    assert policy.next_attempt(3, now=NOW).parked is True
 
 
async def test_dispatch_runs_the_handler_and_marks_the_row_sent(
    tenant_context: RequestContext,
) -> None:
    del tenant_context
    store = FakeOutboxStore(make_event())
 
    report = await OutboxDispatcher(store, clock=lambda: NOW, worker_id="w1").dispatch_due()
 
    assert report == DispatchReport(claimed=1, sent=1)
    assert HANDLED == ["t_alpha:1"]
    assert store.events["evt_0001"].sent_at == NOW
 
 
async def test_a_failing_handler_retries_with_backoff_then_parks(
    tenant_context: RequestContext,
) -> None:
    del tenant_context
    moment = [NOW]
    store = FakeOutboxStore(make_event("test.always-fails"))
    dispatcher = OutboxDispatcher(
        store,
        clock=lambda: moment[0],
        worker_id="w1",
        backoff=BackoffPolicy(max_attempts=3, base=timedelta(seconds=30), cap=timedelta(hours=1)),
    )
 
    first = await dispatcher.dispatch_due()
    assert (first.failed, first.sent) == (1, 0)
    row = store.events["evt_0001"]
    assert row.attempts == 1
    assert row.available_at == NOW + timedelta(seconds=30)
    assert row.last_error is not None
    assert "smtp is down" in row.last_error
 
    # nothing is due until the backoff elapses
    assert (await dispatcher.dispatch_due()).claimed == 0
 
    moment[0] = NOW + timedelta(seconds=31)
    assert (await dispatcher.dispatch_due()).failed == 1
    assert store.events["evt_0001"].attempts == 2
 
    moment[0] = NOW + timedelta(seconds=91)
    third = await dispatcher.dispatch_due()
    assert (third.failed, third.parked) == (1, 1)
    assert store.events["evt_0001"].parked_at is not None
 
    # a parked row is never claimed again, however long the queue waits
    moment[0] = NOW + timedelta(days=7)
    assert (await dispatcher.dispatch_due()).claimed == 0
 
 
async def test_an_unknown_type_parks_instead_of_retrying(
    tenant_context: RequestContext,
) -> None:
    del tenant_context
    store = FakeOutboxStore(make_event("not.registered"))
 
    report = await OutboxDispatcher(store, clock=lambda: NOW, worker_id="w1").dispatch_due()
 
    assert (report.failed, report.parked) == (1, 1)
    row = store.events["evt_0001"]
    assert row.attempts == 1
    assert row.last_error is not None
    assert "no handler registered" in row.last_error
 
 
async def test_a_leased_row_is_recovered_after_the_lease_expires(
    tenant_context: RequestContext,
) -> None:
    del tenant_context
    moment = [NOW]
    store = FakeOutboxStore(make_event())
    # A worker claims the row and then dies: the lease, not the row, is all it holds.
    claimed = await store.claim_due(now=NOW, limit=10, worker_id="w1", lease=timedelta(seconds=60))
    assert len(claimed) == 1
 
    survivor = OutboxDispatcher(store, clock=lambda: moment[0], worker_id="w2")
    assert (await survivor.dispatch_due()).claimed == 0
 
    moment[0] = NOW + timedelta(seconds=61)
    assert (await survivor.dispatch_due()).sent == 1
    assert store.events["evt_0001"].sent_at is not None
    assert HANDLED == ["t_alpha:1"]
 
 
async def test_the_log_only_email_handler_masks_the_recipient(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event = make_event("email.send", payload={"to": "ada@example.com", "template": "invitation"})
 
    with caplog.at_level(logging.INFO):
        await send_email(event, HandlerDeps(tenant_id="t_alpha"))
 
    record = caplog.records[-1]
    assert record.__dict__["recipient"] == "a***@example.com"
    assert "ada@example.com" not in record.getMessage()
 
 
async def test_email_without_a_recipient_is_a_permanent_failure() -> None:
    event = make_event("email.send", payload={"template": "invitation"})
 
    with pytest.raises(PermanentFailureError, match="'to' and 'template'"):
        await send_email(event, HandlerDeps(tenant_id="t_alpha"))
 
 
async def test_cache_invalidation_keys_are_tenant_prefixed() -> None:
    redis = FakeRedis()
    deps = HandlerDeps(tenant_id="t_alpha", redis=cast(Any, redis))
    event = make_event("cache.invalidate", payload={"keys": ["tenant_config"]})
 
    await invalidate_cache(event, deps)
 
    assert redis.deleted == ["t:t_alpha:tenant_config"]