"""`If-Match` / `ETag` helpers for versioned resources (plan section 16.4).

An ETag is the quoted document version: `ETag: "4"`. PATCH requires a matching
`If-Match`; a missing or malformed header is `412 precondition_failed`, and a
stale version surfaces from the repository as `412 version_mismatch`.
"""

from __future__ import annotations

import re

from fastapi import Response

from app.core.errors import ApiError

_IF_MATCH = re.compile(r'^"(\d+)"$')


def etag_for(version: int) -> str:
    """The response header value for a version."""
    return f'"{version}"'


def set_etag(response: Response, version: int) -> None:
    """Set `ETag` on a single-resource read."""
    response.headers["ETag"] = etag_for(version)


def require_version(if_match: str | None) -> int:
    """Parse `If-Match: "<version>"`, rejecting a missing or malformed header.

    Weak validators (`W/"4"`) are rejected: `If-Match` uses strong comparison.
    """
    if if_match is None or not if_match.strip():
        raise ApiError(412, "precondition_failed", "If-Match header is required")
    match = _IF_MATCH.match(if_match.strip())
    if match is None:
        raise ApiError(412, "precondition_failed", 'If-Match must be a quoted version like "4"')
    return int(match.group(1))
