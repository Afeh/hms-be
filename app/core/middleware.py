"""Assign a request ID and publish the request context for the whole request.

This is a pure ASGI middleware rather than `BaseHTTPMiddleware`, so the endpoint
runs in the same task and therefore sees the `ContextVar` we set here.
"""

from __future__ import annotations

import re
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import RequestContext, reset_context, set_context

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class RequestContextMiddleware:
    """Stamps every request with an ID and an empty tenant context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        incoming = headers.get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else _new_request_id()

        client = scope.get("client")
        user_agent = headers.get(b"user-agent", b"").decode("latin-1")
        token = set_context(
            RequestContext(
                request_id=request_id,
                ip=client[0] if client else None,
                device_info=user_agent[:255] or None,
            )
        )

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != REQUEST_ID_HEADER.encode()
                ]
                message["headers"].append((REQUEST_ID_HEADER.encode(), request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_context(token)


def _new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:8]}"
