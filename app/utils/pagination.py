"""Opaque cursors: base64 of a small JSON object, as in `eyJpZCI6Ij...`.

Offset pagination is not used on patient or audit data (plan section 16.3).
"""

from __future__ import annotations

import base64
import json
from typing import Any


class InvalidCursorError(ValueError):
    """Raised when a cursor does not decode; routers translate it to a 400."""

    def __init__(self, cursor: str) -> None:
        super().__init__(f"invalid cursor: {cursor!r}")


def encode_cursor(**values: str) -> str:
    raw = json.dumps(values, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except ValueError as exc:
        raise InvalidCursorError(cursor) from exc
    if not isinstance(decoded, dict):
        raise InvalidCursorError(cursor)
    return {str(key): value for key, value in decoded.items()}
