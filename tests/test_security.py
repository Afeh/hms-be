"""Password, JWT and opaque-token primitives (issue 0013)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError

from app.core import Settings
from app.core.errors import ApiError
from app.core.security import (
    AccessTokenClaims,
    TokenValidationError,
    decode_access_token,
    encode_access_token,
    ensure_password_policy,
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    password_policy_errors,
    verify_password,
)

SETTINGS = Settings()


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        identity_id="identity_1",
        user_id="user_1",
        tenant_id="tenant_1",
        session_id="session_1",
        perm_version=3,
        mfa=False,
    )


def test_argon2id_hash_round_trip_and_wrong_password() -> None:
    password_hash = hash_password("Password123!")

    assert password_hash.startswith("$argon2id$")
    assert verify_password("Password123!", password_hash).valid is True
    assert verify_password("wrong-password", password_hash).valid is False
    assert verify_password("Password123!", None).valid is False


def test_password_policy_reports_failed_rules_without_the_password() -> None:
    assert password_policy_errors("short", 12) == ["must be at least 12 characters"]

    with pytest.raises(ApiError) as raised:
        ensure_password_policy("short", 12)

    assert raised.value.status_code == 422
    assert raised.value.code == "weak_password"
    assert raised.value.errors is not None
    assert raised.value.errors[0].field == "password"
    assert "short" not in raised.value.message
    assert all("short" not in error.message for error in raised.value.errors)


def test_jwt_round_trip_carries_identifiers_only() -> None:
    token, expires_in = encode_access_token(SETTINGS, _claims())

    assert expires_in == 600
    claims = decode_access_token(token, SETTINGS)
    assert claims == _claims()
    payload = jwt.decode(
        token,
        SETTINGS.jwt_secret,
        algorithms=["HS256"],
        audience=SETTINGS.jwt_audience,
        issuer=SETTINGS.jwt_issuer,
    )
    assert "email" not in payload
    assert "name" not in payload
    assert "roles" not in payload


def test_jwt_rejects_wrong_audience_issuer_and_expiry() -> None:
    token, _ = encode_access_token(SETTINGS, _claims())

    wrong_audience = SETTINGS.model_copy(update={"jwt_audience": "somewhere-else"})
    wrong_issuer = SETTINGS.model_copy(update={"jwt_issuer": "somewhere-else"})
    with pytest.raises(TokenValidationError):
        decode_access_token(token, wrong_audience)
    with pytest.raises(TokenValidationError):
        decode_access_token(token, wrong_issuer)

    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": "identity_1",
            "uid": "user_1",
            "tid": "tenant_1",
            "sid": "session_1",
            "perm_version": 1,
            "mfa": False,
            "iss": SETTINGS.jwt_issuer,
            "aud": SETTINGS.jwt_audience,
            "iat": now - timedelta(minutes=10),
            "exp": now - timedelta(seconds=1),
        },
        SETTINGS.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(TokenValidationError):
        decode_access_token(expired, SETTINGS)


def test_non_local_environment_refuses_the_development_secrets() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(environment="production", _env_file=None)


def test_opaque_token_hash_is_stable_and_not_the_token() -> None:
    token = generate_opaque_token()

    assert token.startswith("rt_")
    assert hash_opaque_token(token) != token
    assert hash_opaque_token(token) == hash_opaque_token(token)
    assert hash_opaque_token(generate_opaque_token()) != hash_opaque_token(token)
