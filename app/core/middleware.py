"""ASGI middleware stack for the HMS API.

Middlewares in this module
--------------------------
RequestContextMiddleware
    Stamps every request with a unique ID and populates the ``RequestContext``
    ContextVar (tenant, IP, device info) for the duration of the request.

SecurityHeadersMiddleware
    Injects security headers into every HTTP response — including error
    responses, 404s, and validation failures.  When ``environment != "local"``
    the ``Strict-Transport-Security`` header is also added.

MaxBodySizeMiddleware
    Rejects requests whose body exceeds ``max_bytes`` with a 413 response
    before any body bytes are forwarded to the application, protecting against
    large-payload denial-of-service attacks.  The limit is enforced by reading
    the ``Content-Length`` header first; for chunked / streaming bodies the
    bytes are counted as they arrive and the connection is aborted early.

All three are pure ASGI middlewares (not ``BaseHTTPMiddleware``) so they run
in the same asyncio task as the endpoint and don't interfere with ContextVars.
"""

from __future__ import annotations

import json
import re
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import RequestContext, reset_context, set_context
from app.core.errors import ApiError
from app.schemas.envelope import error_envelope

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

_SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
]
_HSTS_HEADER = (
    b"strict-transport-security",
    b"max-age=31536000; includeSubDomains",
)


class SecurityHeadersMiddleware:
    """Inject security headers into every HTTP response.

    When ``environment`` is not ``"local"`` the ``Strict-Transport-Security``
    header is added so HTTPS-only deployments advertise HSTS.
    """

    def __init__(self, app: ASGIApp, *, environment: str = "local") -> None:
        self.app = app
        self._extra: list[tuple[bytes, bytes]] = list(_SECURITY_HEADERS)
        if environment != "local":
            self._extra.append(_HSTS_HEADER)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Build a new headers list, skipping any duplicates we're
                # about to add (defensive — none of our headers should come
                # from routes, but be safe).
                existing_names = {name.lower() for name, _ in message.get("headers", [])}
                extra = [(name, value) for name, value in self._extra if name not in existing_names]
                message = {
                    **message,
                    "headers": list(message.get("headers", [])) + extra,
                }
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


# ---------------------------------------------------------------------------
# Body size limiter
# ---------------------------------------------------------------------------


def _payload_too_large_response(max_bytes: int) -> tuple[dict[str, object], dict[str, object]]:
    """Return (start_message, body_message) for a 413 response."""
    envelope = error_envelope(
        413,
        "payload_too_large",
        f"Request body exceeds the {max_bytes}-byte limit",
    )
    body = json.dumps(envelope).encode()
    start: dict[str, object] = {
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    }
    body_msg: dict[str, object] = {"type": "http.response.body", "body": body, "more_body": False}
    return start, body_msg


class MaxBodySizeMiddleware:
    """Reject HTTP requests whose body exceeds ``max_bytes``.

    * If ``Content-Length`` is present and already over the limit, the request
      is rejected immediately with 413 without reading any body bytes.
    * For chunked / streaming bodies, bytes are counted as they arrive;
      if the limit is exceeded, an ``ApiError(413, "payload_too_large", ...)``
      is raised so FastAPI's exception handler renders the standard 413 envelope.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Fast path: reject based on Content-Length header alone.
        headers = dict(scope.get("headers", []))
        content_length_raw = headers.get(b"content-length")
        if content_length_raw is not None:
            try:
                content_length = int(content_length_raw)
            except ValueError:
                content_length = 0
            if content_length > self.max_bytes:
                start, body_msg = _payload_too_large_response(self.max_bytes)
                await send(start)
                await send(body_msg)
                return

        # Slow path: count streaming / chunked bytes as they arrive.
        total_received = 0

        async def limited_receive() -> Message:
            nonlocal total_received
            message = await receive()
            if message["type"] == "http.request":
                chunk: bytes = message.get("body", b"")
                total_received += len(chunk)
                if total_received > self.max_bytes:
                    raise ApiError(
                        413,
                        "payload_too_large",
                        f"Request body exceeds the {self.max_bytes}-byte limit",
                    )
            return message

        await self.app(scope, limited_receive, send)


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
