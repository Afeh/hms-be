"""Errors the API can describe to clients, and the handlers that render them.

Every handler returns the same envelope as a successful call, with `status` set to
`error`; unhandled exceptions never leak a traceback or patient data.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.envelope import ErrorDetail, error_envelope

logger = logging.getLogger(__name__)

_HTTP_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_failed",
    429: "rate_limited",
    500: "internal_error",
    503: "dependency_unavailable",
}


class ApiError(Exception):
    """An expected failure: status, machine-readable code, safe message."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        errors: Sequence[ErrorDetail] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.errors = list(errors) if errors else None
        self.headers = dict(headers) if headers else None


def _render(error: ApiError) -> JSONResponse:
    return JSONResponse(
        error_envelope(
            error.status_code,
            error.code,
            error.message,
            errors=error.errors,
        ),
        status_code=error.status_code,
        headers=error.headers,
    )


async def api_error_handler(request: Request, exc: Exception) -> Response:
    del request  # the message and request ID come from the exception and context
    if not isinstance(exc, ApiError):
        return _render(ApiError(500, "internal_error", "Something went wrong"))
    return _render(exc)


async def validation_error_handler(request: Request, exc: Exception) -> Response:
    del request
    details: list[ErrorDetail] = []
    if isinstance(exc, RequestValidationError):
        for error in exc.errors():
            location = [str(part) for part in error["loc"] if part not in {"body", "query", "path"}]
            details.append(
                ErrorDetail(field=".".join(location) or "body", message=str(error["msg"]))
            )
    return _render(
        ApiError(
            422,
            "validation_failed",
            "Validation failed",
            errors=details or None,
        )
    )


async def http_error_handler(request: Request, exc: Exception) -> Response:
    del request
    if not isinstance(exc, StarletteHTTPException):
        return _render(ApiError(500, "internal_error", "Something went wrong"))
    message = (
        exc.detail if isinstance(exc.detail, str) else _HTTP_CODES.get(exc.status_code, "error")
    )
    return _render(
        ApiError(
            exc.status_code,
            _HTTP_CODES.get(exc.status_code, "error"),
            message,
            headers=exc.headers,
        )
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> Response:
    logger.exception(
        "unhandled error",
        extra={"path": request.url.path, "method": request.method},
        exc_info=exc,
    )
    return _render(ApiError(500, "internal_error", "Something went wrong"))


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
