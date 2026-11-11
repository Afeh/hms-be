"""Baseline marker: no schema yet; the first real indexes arrive with 0010/0012."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database


def upgrade(db: Database[dict[str, Any]]) -> None:
    """Apply the migration. Forward-only: a correction is a new revision."""
    del db
