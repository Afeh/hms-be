"""Browser session cookies for the access and refresh tokens.

The API also returns the token pair in the login body for native and
server-to-server clients. Browser clients should rely on these HttpOnly cookies;
JavaScript never needs to read or persist the tokens.

Both cookies use the same path and security attributes so a single sign-out
always clears the pair. ``SameSite=Lax`` is the default; cross-site frontends
must opt into ``SameSite=None`` together with a Secure cookie and HTTPS.
"""

from __future__ import annotations

from datetime import UTC, datetime

from starlette.responses import Response

from app.core.config import Settings

ACCESS_TOKEN_COOKIE = "hms_access_token"  # noqa: S105 - a cookie name, not a secret
REFRESH_TOKEN_COOKIE = "hms_refresh_token"  # noqa: S105 - a cookie name, not a secret
COOKIE_PATH = "/"


def set_auth_cookies(
    response: Response,
    settings: Settings,
    *,
    access_token: str,
    access_expires_in: int,
    refresh_token: str,
    refresh_expires_at: datetime,
) -> None:
    """Attach the access and refresh tokens to a response as HttpOnly cookies."""
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        access_token,
        max_age=access_expires_in,
        path=COOKIE_PATH,
        domain=settings.auth_cookie_domain,
        secure=settings.secure_auth_cookies,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )
    # Values read back from MongoDB can carry a fixed-offset UTC tzinfo, which
    # Starlette refuses for ``expires``; normalise the instant to timezone.utc.
    expires_at = (
        refresh_expires_at.replace(tzinfo=UTC)
        if refresh_expires_at.tzinfo is None
        else refresh_expires_at.astimezone(UTC)
    )
    refresh_max_age = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        REFRESH_TOKEN_COOKIE,
        refresh_token,
        max_age=refresh_max_age,
        expires=expires_at,
        path=COOKIE_PATH,
        domain=settings.auth_cookie_domain,
        secure=settings.secure_auth_cookies,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    """Expire both browser token cookies; safe when they were never set."""
    for name in (ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE):
        response.delete_cookie(
            name,
            path=COOKIE_PATH,
            domain=settings.auth_cookie_domain,
            secure=settings.secure_auth_cookies,
            httponly=True,
            samesite=settings.auth_cookie_samesite,
        )


__all__ = [
    "ACCESS_TOKEN_COOKIE",
    "COOKIE_PATH",
    "REFRESH_TOKEN_COOKIE",
    "clear_auth_cookies",
    "set_auth_cookies",
]
