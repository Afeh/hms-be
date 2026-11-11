"""Repository primitives against a real MongoDB: versions, soft delete, paging.

Integration tests: run with `MONGODB_URI=mongodb://127.0.0.1:27017 pytest -m integration`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.context import RequestContext
from app.core.errors import VersionConflictError
from app.db import TenantConnectionRegistry, TenantDocument, TenantRepository

MONGODB_URI = os.environ.get("MONGODB_URI")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        MONGODB_URI is None, reason="set MONGODB_URI to run repository integration tests"
    ),
]


class Widget(TenantDocument):
    name: str
    quantity: int = 0


class WidgetRepository(TenantRepository[Widget]):
    model = Widget


@pytest.fixture
async def repository() -> AsyncIterator[WidgetRepository]:
    database = f"hms_test_repository_{uuid.uuid4().hex[:8]}"
    registry = TenantConnectionRegistry(MONGODB_URI or "", database, timeout_ms=2000)
    try:
        yield WidgetRepository(registry.collection("t_alpha", "widgets"))
    finally:
        await registry.client.drop_database(database)
        await registry.close()


async def test_concurrent_updates_allow_exactly_one_winner(
    repository: WidgetRepository, tenant_context: RequestContext
) -> None:
    del tenant_context
    widget = await repository.create(Widget(tenant_id="t_alpha", name="original"))

    results = await asyncio.gather(
        repository.update_versioned(widget.id or "", {"name": "first"}, 1),
        repository.update_versioned(widget.id or "", {"name": "second"}, 1),
        return_exceptions=True,
    )

    assert sum(isinstance(result, VersionConflictError) for result in results) == 1
    winners = [result for result in results if isinstance(result, Widget)]
    assert len(winners) == 1
    current = await repository.get(widget.id or "")
    assert current is not None
    assert current.version == 2
    assert current.name == winners[0].name


async def test_soft_deleted_rows_disappear_until_explicitly_included(
    repository: WidgetRepository, tenant_context: RequestContext
) -> None:
    del tenant_context
    gone = await repository.create(Widget(tenant_id="t_alpha", name="gone"))
    kept = await repository.create(Widget(tenant_id="t_alpha", name="kept"))

    assert await repository.soft_delete(gone.id or "") is True
    assert await repository.soft_delete(gone.id or "") is False  # idempotent

    page, cursor = await repository.list()
    assert [item.id for item in page] == [kept.id]
    assert cursor is None
    assert await repository.get(gone.id or "") is None
    assert await repository.find_one({"name": "gone"}) is None

    restored = await repository.get(gone.id or "", include_deleted=True)
    assert restored is not None
    assert restored.deleted_at is not None
    all_rows, _ = await repository.list(include_deleted=True)
    assert {item.id for item in all_rows} == {gone.id, kept.id}


async def test_cursor_paging_visits_every_document_once(
    repository: WidgetRepository, tenant_context: RequestContext
) -> None:
    del tenant_context
    for index in range(250):
        await repository.create(Widget(tenant_id="t_alpha", name=f"widget-{index:03d}"))

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        page, cursor = await repository.list(limit=100, cursor=cursor)
        pages += 1
        seen.extend(item.id or "" for item in page)
        if cursor is None:
            break

    assert pages == 3
    assert len(seen) == 250
    assert len(set(seen)) == 250


async def test_create_uses_the_context_tenant(
    repository: WidgetRepository, tenant_context: RequestContext
) -> None:
    created = await repository.create(Widget(tenant_id="t_beta", name="claimed"))

    assert created.tenant_id == tenant_context.tenant_id
    assert await repository.find_one({"name": "claimed"}) is not None


async def test_update_refuses_immutable_fields(
    repository: WidgetRepository, tenant_context: RequestContext
) -> None:
    del tenant_context
    widget = await repository.create(Widget(tenant_id="t_alpha", name="original"))

    with pytest.raises(ValueError, match="tenant_id"):
        await repository.update_versioned(widget.id or "", {"tenant_id": "t_beta"}, 1)


async def test_list_accepts_a_filter_sort_and_projection(
    repository: WidgetRepository, tenant_context: RequestContext
) -> None:
    del tenant_context
    await repository.create(Widget(tenant_id="t_alpha", name="alpha", quantity=2))
    await repository.create(Widget(tenant_id="t_alpha", name="beta", quantity=5))

    page, _ = await repository.list(
        filter={"quantity": {"$gte": 2}},
        sort=[("name", -1)],
        projection={"tenant_id": 1, "name": 1},
    )

    assert [item.name for item in page] == ["beta", "alpha"]
