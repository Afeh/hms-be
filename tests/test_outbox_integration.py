"""The outbox against a real MongoDB replica set: transactions, leases, the sweep.
 
Integration tests: run with
`MONGODB_URI='mongodb://127.0.0.1:27017/?replicaSet=rs0' pytest -m integration`.
A transaction needs a replica set, so a standalone server fails these on purpose.
"""
 
from __future__ import annotations
 
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
 
import pytest
 
from app.core.context import RequestContext
from app.db import TenantConnectionRegistry
from app.db.outbox import OUTBOX_COLLECTION, OutboxRepository
from app.models.outbox import OutboxEvent
 
MONGODB_URI = os.environ.get("MONGODB_URI")
 
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        MONGODB_URI is None, reason="set MONGODB_URI to run outbox integration tests"
    ),
]
 
 
class RollbackError(RuntimeError):
    """Raised to roll a transaction back on purpose."""
 
 
@pytest.fixture
async def outbox() -> AsyncIterator[tuple[TenantConnectionRegistry, OutboxRepository]]:
    database = f"hms_test_outbox_{uuid.uuid4().hex[:8]}"
    registry = TenantConnectionRegistry(MONGODB_URI or "", database, timeout_ms=2000)
    try:
        yield registry, OutboxRepository(registry.collection("t_alpha", OUTBOX_COLLECTION))
    finally:
        await registry.client.drop_database(database)
        await registry.close()
 
 
def due(**overrides: Any) -> OutboxEvent:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "tenant_id": "t_alpha",
        "type": "test.recorded",
        "payload": {"n": 1},
        "created_at": now,
        "available_at": now - timedelta(seconds=5),
    }
    values.update(overrides)
    return OutboxEvent(**values)
 
 
async def _enqueue_then_roll_back(
    registry: TenantConnectionRegistry, repository: OutboxRepository
) -> None:
    async with await registry.client.start_session() as session, session.start_transaction():
        await repository.enqueue(due(), session=session)
        raise RollbackError
 
 
async def test_a_rolled_back_transaction_leaves_no_row(
    outbox: tuple[TenantConnectionRegistry, OutboxRepository],
    tenant_context: RequestContext,
) -> None:
    del tenant_context
    registry, repository = outbox
 
    with pytest.raises(RollbackError):
        await _enqueue_then_roll_back(registry, repository)
 
    assert await repository.count() == 0
 
    async with await registry.client.start_session() as session, session.start_transaction():
        await repository.enqueue(due(), session=session)
 
    assert await repository.count() == 1
 
 
async def test_a_dead_dispatcher_loses_nothing_and_a_live_lease_blocks_a_second_claim(
    outbox: tuple[TenantConnectionRegistry, OutboxRepository],
    tenant_context: RequestContext,
) -> None:
    del tenant_context
    _, repository = outbox
    now = datetime.now(UTC)
    await repository.enqueue(due())
 
    leased = await repository.claim_due(
        now=now, limit=10, worker_id="w1", lease=timedelta(seconds=60)
    )
    assert len(leased) == 1
 
    # While the lease holds, nobody else may take the row.
    assert (
        await repository.claim_due(now=now, limit=10, worker_id="w2", lease=timedelta(seconds=60))
        == []
    )
 
    # When the lease expires the row comes back, so a crash loses nothing.
    later = now + timedelta(seconds=61)
    reclaimed = await repository.claim_due(
        now=later, limit=10, worker_id="w2", lease=timedelta(seconds=60)
    )
    assert len(reclaimed) == 1
 
    # The stale worker can no longer settle it; the new owner can, exactly once.
    assert await repository.mark_sent(leased[0].id or "", now=later, worker_id="w1") is False
    assert await repository.mark_sent(reclaimed[0].id or "", now=later, worker_id="w2") is True
    assert await repository.mark_sent(reclaimed[0].id or "", now=later, worker_id="w2") is False
 
 
async def test_the_sweep_finds_only_tenants_with_a_due_row(
    outbox: tuple[TenantConnectionRegistry, OutboxRepository],
    tenant_context: RequestContext,
) -> None:
    del tenant_context
    registry, repository = outbox
    now = datetime.now(UTC)
    await repository.enqueue(due(available_at=now - timedelta(seconds=90)))
    await repository.enqueue(due(available_at=now + timedelta(days=1)))
 
    sweeper = OutboxRepository(registry.system_collection(OUTBOX_COLLECTION))
 
    assert await sweeper.pending_tenant_ids(now=now) == ["t_alpha"]
    assert await sweeper.pending_tenant_ids(now=now + timedelta(days=2)) == ["t_alpha"]
    assert await sweeper.pending_tenant_ids(now=now - timedelta(days=2)) == []
 
 
async def test_a_parked_row_is_not_due_for_the_sweep(
    outbox: tuple[TenantConnectionRegistry, OutboxRepository],
    tenant_context: RequestContext,
) -> None:
    del tenant_context
    registry, repository = outbox
    now = datetime.now(UTC)
    await repository.enqueue(due())
    leased = await repository.claim_due(
        now=now, limit=1, worker_id="w1", lease=timedelta(seconds=60)
    )
 
    await repository.record_failure(
        leased[0].id or "",
        now=now,
        worker_id="w1",
        attempts=8,
        available_at=now,
        parked=True,
        error="RuntimeError: too many attempts",
    )
 
    sweeper = OutboxRepository(registry.system_collection(OUTBOX_COLLECTION))
    assert await sweeper.pending_tenant_ids(now=now + timedelta(days=1)) == []