"""Stored shape of a per-tenant counter (sprint S001)."""

from __future__ import annotations

from app.db.tenancy import TenantDocument


class Counter(TenantDocument):
    """A named monotonic counter used for sequence-based identifiers."""

    key: str
    value: int = 0


__all__ = ["Counter"]
