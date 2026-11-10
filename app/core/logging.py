"""JSON logs carrying the request and tenant identifiers.

Redaction of patient data happens where records are written: nothing here may log
names, phone numbers or clinical text, and the formatter only ever adds IDs.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.core.context import current_context

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the active request context attached."""

    def format(self, record: logging.LogRecord) -> str:
        ctx = current_context()
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": ctx.request_id if ctx else None,
            "tenant_id": ctx.tenant_id if ctx else None,
            "user_id": ctx.user_id if ctx else None,
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Send root logging to stdout as JSON. Called once per process."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
