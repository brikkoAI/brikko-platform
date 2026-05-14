"""JWT session tokens — access + refresh + expiry."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from voltari_gateway.auth.session import (
    create_access_token,
    create_refresh_token,
    is_refresh_token_active,
    register_refresh_token,
    revoke_refresh_token,
    verify_access_token,
    verify_refresh_token,
)
from voltari_gateway.config import get_settings


def test_access_token_round_trip():
    user_id = uuid.uuid4()
    account_id = uuid.uuid4()

    token, exp = create_access_token(user_id, account_id)
    assert isinstance(token, str)
    assert exp > datetime.now(UTC)

    claims = verify_access_token(token)
    assert claims is not None
    assert claims.user_id == user_id
    assert claims.account_id == account_id
    # iat and exp are populated and consistent.
    assert claims.expires_at > claims.issued_at

    # A refresh token must NOT verify as an access token (type guard).
    refresh_token, _, _ = create_refresh_token(user_id)
    assert verify_access_token(refresh_token) is None


def test_refresh_token_round_trip():
    user_id = uuid.uuid4()

    token, exp, jti = create_refresh_token(user_id)
    assert isinstance(token, str)
    assert isinstance(jti, str) and len(jti) >= 16
    assert exp > datetime.now(UTC)

    claims = verify_refresh_token(token)
    assert claims is not None
    assert claims.user_id == user_id
    assert claims.jti == jti

    # An access token must NOT verify as a refresh token.
    access, _ = create_access_token(user_id, uuid.uuid4())
    assert verify_refresh_token(access) is None


def test_expired_access_token_is_rejected():
    """Forge a token with `exp` in the past and confirm verify returns None."""
    settings = get_settings()
    user_id = uuid.uuid4()
    past = int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())

    payload = {
        "iss": "voltari-gateway",
        "aud": "voltari-management",
        "sub": str(user_id),
        "type": "access",
        "account_id": str(uuid.uuid4()),
        "iat": past - 60,
        "exp": past,
    }
    token = jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )

    assert verify_access_token(token) is None

    # Tampering with the signature also yields None.
    tampered = token + "xx"
    assert verify_access_token(tampered) is None


@pytest.mark.asyncio
async def test_refresh_whitelist_register_and_revoke(redis_client):
    user_id = uuid.uuid4()
    _, exp, jti = create_refresh_token(user_id)

    # Not active until registered.
    assert await is_refresh_token_active(redis_client, user_id, jti) is False

    await register_refresh_token(redis_client, user_id, jti, exp)
    assert await is_refresh_token_active(redis_client, user_id, jti) is True

    await revoke_refresh_token(redis_client, user_id, jti)
    assert await is_refresh_token_active(redis_client, user_id, jti) is False
