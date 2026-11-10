"""The four-key response envelope (plan section 16.1).

`status` is `success` for 2xx and `error` otherwise, `status_code` repeats the HTTP
status, `message` is safe to show in the interface, and `data` is always an object.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from app.core.context import current_context


class ErrorDetail(BaseModel):
    """One field-level problem, used inside `data.errors`."""

    field: str
    message: str


def default_message(status_code: int) -> str:
    return "Request completed successfully" if 200 <= status_code < 300 else "Request failed"


def success_envelope(data: Any, *, message: str, status_code: int = 200) -> dict[str, Any]:
    return {
        "status": "success",
        "status_code": status_code,
        "message": message,
        "data": data,
    }


def error_envelope(
    status_code: int,
    code: str,
    message: str,
    *,
    errors: Sequence[ErrorDetail] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"code": code}
    if errors:
        data["errors"] = [detail.model_dump() for detail in errors]
    ctx = current_context()
    data["request_id"] = request_id or (ctx.request_id if ctx else "")
    return {
        "status": "error",
        "status_code": status_code,
        "message": message,
        "data": data,
    }
