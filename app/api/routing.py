"""One place applies the response envelope, so no route repeats it.

Router handlers return plain data (`{"status": "ok"}`); `EnvelopeRoute` wraps the
serialized body into `status` / `status_code` / `message` / `data`. The message is
declared next to the route, not inside the handler:

    @router.get("/live")
    @response_message("API is live")
    async def live() -> Health: ...
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Any, ParamSpec, TypeVar

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from starlette.requests import Request

from app.schemas.envelope import default_message, success_envelope

MESSAGE_ATTRIBUTE = "__envelope_message__"

P = ParamSpec("P")
R = TypeVar("R")


def response_message(message: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Attach the envelope message to an endpoint. Ordered below the route decorator."""

    def decorator(endpoint: Callable[P, R]) -> Callable[P, R]:
        setattr(endpoint, MESSAGE_ATTRIBUTE, message)
        return endpoint

    return decorator


class EnvelopeRoute(APIRoute):
    """An API route whose successful JSON response is wrapped in the envelope."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.message: str | None = getattr(self.endpoint, MESSAGE_ATTRIBUTE, None)

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def envelope_handler(request: Request) -> Response:
            response = await handler(request)
            if not _should_wrap(response):
                return response

            wrapped = success_envelope(
                json.loads(bytes(response.body)),
                message=self.message or default_message(response.status_code),
                status_code=response.status_code,
            )
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            return JSONResponse(wrapped, status_code=response.status_code, headers=headers)

        return envelope_handler


class ApiRouter(APIRouter):
    """`APIRouter` that wraps its responses in the envelope."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # FastAPI reads this attribute when adding routes, rather than a constructor arg.
        self.route_class = EnvelopeRoute


def _should_wrap(response: Response) -> bool:
    """Wrap successful JSON bodies only: never errors, streams, files or 204s.

    FastAPI serializes a route's return value into a plain `Response` carrying
    JSON bytes, so the media type is what identifies a body worth wrapping.
    """
    return (
        not isinstance(response, StreamingResponse)
        and response.media_type == "application/json"
        and 200 <= response.status_code < 300
        and response.status_code != 204
    )
