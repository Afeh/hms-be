"""The tenant guard: scoping, stamping, aggregation and exemptions."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.context import MissingContextError, RequestContext
from app.db import TenantCollection, TenantIsolationError, run_as_system


class FakeCollection:
    """Records what the guard passed to the driver."""

    def __init__(self, name: str = "patients") -> None:
        self.name = name
        self.filters: list[dict[str, Any]] = []
        self.inserted: list[dict[str, Any]] = []
        self.pipelines: list[list[dict[str, Any]]] = []

    async def find_one(self, filter: dict[str, Any], projection: Any = None) -> None:
        del projection
        self.filters.append(filter)

    async def insert_one(self, document: dict[str, Any]) -> SimpleNamespace:
        self.inserted.append(document)
        return SimpleNamespace(inserted_id="66e7c0a2e4b0a1b2c3d4e5f6")

    def aggregate(self, pipeline: list[dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
        del kwargs
        self.pipelines.append(pipeline)
        return pipeline

    async def count_documents(self, filter: dict[str, Any]) -> int:
        self.filters.append(filter)
        return 0


def build(name: str = "patients") -> tuple[TenantCollection, FakeCollection]:
    fake = FakeCollection(name)
    return TenantCollection(cast(Any, fake), name), fake


async def test_requires_a_context() -> None:
    collection, _ = build()

    with pytest.raises(MissingContextError):
        await collection.find_one({})


async def test_injects_the_tenant_into_reads(tenant_context: RequestContext) -> None:
    collection, fake = build()

    await collection.find_one({"status": "draft"})

    assert fake.filters == [{"status": "draft", "tenant_id": tenant_context.tenant_id}]


async def test_rejects_a_filter_for_another_tenant(tenant_context: RequestContext) -> None:
    del tenant_context
    collection, _ = build()

    with pytest.raises(TenantIsolationError):
        await collection.find_one({"tenant_id": "t_beta"})


async def test_stamps_inserts(tenant_context: RequestContext) -> None:
    collection, fake = build()

    await collection.insert_one({"patient_number": "SMH-2026-000731"})

    assert fake.inserted == [
        {"patient_number": "SMH-2026-000731", "tenant_id": tenant_context.tenant_id}
    ]


async def test_rejects_a_document_for_another_tenant(tenant_context: RequestContext) -> None:
    del tenant_context
    collection, _ = build()

    with pytest.raises(TenantIsolationError):
        await collection.insert_one({"tenant_id": "t_beta"})


async def test_aggregation_starts_with_a_tenant_match(tenant_context: RequestContext) -> None:
    collection, fake = build()

    collection.aggregate([{"$group": {"_id": "$status"}}])

    assert fake.pipelines == [
        [{"$match": {"tenant_id": tenant_context.tenant_id}}, {"$group": {"_id": "$status"}}]
    ]


async def test_search_and_geo_near_keep_the_tenant_inside_the_first_stage(
    tenant_context: RequestContext,
) -> None:
    collection, fake = build()

    collection.aggregate([{"$search": {"text": {"path": "name", "query": "ada"}}}])
    collection.aggregate([{"$geoNear": {"near": [0, 0], "distanceField": "d"}}])

    search = fake.pipelines[0][0]["$search"]
    geo_near = fake.pipelines[1][0]["$geoNear"]
    assert search["filter"] == {"tenant_id": tenant_context.tenant_id}
    assert geo_near["query"] == {"tenant_id": tenant_context.tenant_id}


async def test_exempt_collections_are_not_scoped(tenant_context: RequestContext) -> None:
    del tenant_context
    collection, fake = build("identities")

    await collection.find_one({"email": "ada@example.com"})

    assert fake.filters == [{"email": "ada@example.com"}]


async def test_run_as_system_bypasses_the_guard() -> None:
    collection, fake = build()

    with run_as_system("migration 0001"):
        await collection.find_one({})

    assert fake.filters == [{}]
