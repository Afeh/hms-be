"""Passwords, access tokens and opaque refresh tokens (plan §5.1, issue 0013).

Passwords use argon2id. Access tokens are short-lived JWTs carrying identifiers
only — never an email, name or role name. Refresh tokens are 256-bit random values
whose SHA-256 hash is the only thing stored.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings
from app.core.errors import ApiError
from app.schemas.envelope import ErrorDetail

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
ACCESS_TOKEN_ALGORITHM = "HS256"  # noqa: S105 - an algorithm name, not a secret

#: Tuned for an interactive login on server hardware; tune again under load.
PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65_536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


class TokenValidationError(Exception):
    """A bearer or opaque token is missing, malformed, expired or forged."""


@dataclass(frozen=True, slots=True)
class PasswordCheck:
    """The outcome of checking a password against a stored hash."""

    valid: bool
    needs_rehash: bool = False


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Identifiers carried by an access token."""

    identity_id: str
    user_id: str
    tenant_id: str
    session_id: str
    perm_version: int
    mfa: bool


def hash_password(password: str) -> str:
    """Hash a password with argon2id."""
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str | None) -> PasswordCheck:
    """Verify a password; report whether the stored hash should be upgraded."""
    if not password_hash:
        return PasswordCheck(False)
    try:
        PASSWORD_HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return PasswordCheck(False)
    return PasswordCheck(True, PASSWORD_HASHER.check_needs_rehash(password_hash))


def dummy_password_hash() -> str:
    """A hash to verify against when no identity exists, to equalise login timing."""
    return PASSWORD_HASHER.hash("not-a-real-password")


def password_policy_errors(password: str, min_length: int = PASSWORD_MIN_LENGTH) -> list[str]:
    """Return the policy rules a password fails (empty when it passes)."""
    errors: list[str] = []
    if len(password) < min_length:
        errors.append(f"must be at least {min_length} characters")
    if len(password) > PASSWORD_MAX_LENGTH:
        errors.append(f"must be at most {PASSWORD_MAX_LENGTH} characters")
    return errors


def ensure_password_policy(password: str, min_length: int = PASSWORD_MIN_LENGTH) -> None:
    """Raise ``422 weak_password`` naming failed rules; never echo the password."""
    failures = password_policy_errors(password, min_length)
    if failures:
        raise ApiError(
            422,
            "weak_password",
            "Password does not meet the security policy",
            errors=[ErrorDetail(field="password", message=message) for message in failures],
        )


def encode_access_token(settings: Settings, claims: AccessTokenClaims) -> tuple[str, int]:
    """Sign an access token and return it with its lifetime in seconds."""
    now = datetime.now(UTC)
    expires_in = settings.access_token_ttl_seconds
    payload: dict[str, Any] = {
        "sub": claims.identity_id,
        "uid": claims.user_id,
        "tid": claims.tenant_id,
        "sid": claims.session_id,
        "perm_version": claims.perm_version,
        "mfa": claims.mfa,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ACCESS_TOKEN_ALGORITHM)
    return token, expires_in


def decode_access_token(token: str, settings: Settings) -> AccessTokenClaims:
    """Validate signature, issuer, audience and expiry, then return identifiers."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ACCESS_TOKEN_ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={
                "require": [
                    "exp",
                    "iat",
                    "iss",
                    "aud",
                    "sub",
                    "uid",
                    "tid",
                    "sid",
                    "perm_version",
                    "mfa",
                ]
            },
        )
    except jwt.InvalidTokenError as exc:
        raise TokenValidationError(str(exc)) from exc

    try:
        claims = AccessTokenClaims(
            identity_id=_required_str(payload, "sub"),
            user_id=_required_str(payload, "uid"),
            tenant_id=_required_str(payload, "tid"),
            session_id=_required_str(payload, "sid"),
            perm_version=_required_int(payload, "perm_version"),
            mfa=_required_bool(payload, "mfa"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenValidationError("token claims are malformed") from exc
    return claims


def generate_opaque_token(prefix: str = "rt") -> str:
    """A 256-bit URL-safe random token, prefixed for readability."""
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_opaque_token(token: str) -> str:
    """SHA-256 of an opaque token; what the database stores."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload[key]
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


__all__ = [
    "ACCESS_TOKEN_ALGORITHM",
    "PASSWORD_MAX_LENGTH",
    "PASSWORD_MIN_LENGTH",
    "AccessTokenClaims",
    "PasswordCheck",
    "TokenValidationError",
    "decode_access_token",
    "dummy_password_hash",
    "encode_access_token",
    "ensure_password_policy",
    "generate_opaque_token",
    "hash_opaque_token",
    "hash_password",
    "password_policy_errors",
    "verify_password",
]
