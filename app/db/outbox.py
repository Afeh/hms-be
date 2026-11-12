"""`outbox` collection access: enqueue in a transaction, claim a lease, settle.
 
Everything goes through `TenantCollection`, so each row carries the tenant of the
change that produced it and one tenant's dispatcher can never settle another's
rows. The one unscoped call is `pending_tenant_ids`, which runs inside
`run_as_system` and returns tenant IDs, never payloads.
 
Delivery is at-least-once. The lease makes a crashed dispatcher's rows claimable
again after `locked_until` passes, and `mark_sent` refuses a row another worker
now owns, so a stale worker cannot overwrite a newer claim.
"""
 
from __future__ import annotations
 
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
 
from bson import ObjectId
from bson.errors import InvalidId
 
from app.db.tenancy import TenantCollection, run_as_system
from app.models.outbox import OutboxEvent
 
if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorClientSession
 
OUTBOX_COLLECTION = "outbox"
 
 
class OutboxRepository:
    """One tenant's outbox; the collection is scoped by the current context."""
 
    def __init__(self, collection: TenantCollection) -> None:
        self._collection = collection
 
    async def enqueue(
        self, event: OutboxEvent, *, session: AsyncIOMotorClientSession | None = None
    ) -> str:
        """Insert the row in the caller's transaction: pass that transaction's session."""
        result = await self._collection.insert_one(
            event.model_dump(by_alias=True, exclude_none=True), session=session
        )
        return str(result.inserted_id)
 
    async def count(self, query: Mapping[str, Any] | None = None) -> int:
        """Rows matching `query`, scoped to the context tenant."""
        return await self._collection.count_documents(query or {})
 
    async def claim_due(
        self, *, now: datetime, limit: int, worker_id: str, lease: timedelta
    ) -> list[OutboxEvent]:
        """Lease up to `limit` due rows; each claim is one atomic find-and-update."""
        claimed: list[OutboxEvent] = []
        while len(claimed) < limit:
            document = await self._collection.find_one_and_update(
                {
                    "sent_at": None,
                    "parked_at": None,
                    "available_at": {"$lte": now},
                    "$or": [{"locked_until": None}, {"locked_until": {"$lte": now}}],
                },
                {
                    "$set": {
                        "locked_until": now + lease,
                        "locked_by": worker_id,
                        "updated_at": now,
                    }
                },
            )
            if document is None:
                break
            claimed.append(OutboxEvent.model_validate(document))
        return claimed
 
    async def mark_sent(self, event_id: str, *, now: datetime, worker_id: str) -> bool:
        """Settle a row this worker still holds the lease on."""
        object_id = _object_id(event_id)
        if object_id is None:
            return False
        result = await self._collection.update_one(
            {"_id": object_id, "sent_at": None, "locked_by": worker_id},
            {
                "$set": {"sent_at": now, "updated_at": now},
                "$unset": {"locked_until": "", "locked_by": ""},
            },
        )
        return result.modified_count == 1
 
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
        """Store the attempt count and error, or park the row for good."""
        object_id = _object_id(event_id)
        if object_id is None:
            return False
        settable: dict[str, Any] = {
            "attempts": attempts,
            "available_at": available_at,
            "last_error": error,
            "updated_at": now,
        }
        if parked:
            settable["parked_at"] = now
        result = await self._collection.update_one(
            {"_id": object_id, "sent_at": None, "locked_by": worker_id},
            {"$set": settable, "$unset": {"locked_until": "", "locked_by": ""}},
        )
        return result.modified_count == 1
 
    async def pending_tenant_ids(self, *, now: datetime) -> list[str]:
        """Tenants with a claimable row, for the beat sweep. System access, IDs only."""
        with run_as_system("outbox sweep: enumerate tenants with a due row"):
            pipeline: list[dict[str, Any]] = [
                {
                    "$match": {
                        "sent_at": None,
                        "parked_at": None,
                        "available_at": {"$lte": now},
                    }
                },
                {"$group": {"_id": "$tenant_id"}},
            ]
            documents = [document async for document in self._collection.aggregate(pipeline)]
        return [str(document["_id"]) for document in documents if document.get("_id")]
 
 
def _object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None