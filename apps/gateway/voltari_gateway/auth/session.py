"""JWT-based session tokens for the management API (the user-facing dashboard).

Two distinct tokens, two cookies:

* **access**  — short-lived (15 min), `SameSite=Strict`, sent on every request.
* **refresh** — long-lived  (30 days), `SameSite=Lax`, used only against the
  refresh endpoint to mint a fresh access token. Each refresh JWT carries a
  `jti` (jwt-id); the JTI is whitelisted in Redis at issue time and removed
  on logout / revocation, so a stolen refresh JWT can be killed instantly
  without waiting for the JWT itself to expire.

We use `pyjwt` so HS256 verification is constant-time + audited.

Module is intentionally pure — no Redis I/O happens on token *creation*.
``cookies.py`` is the layer that decides when to whitelist a JTI.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt.exceptions import InvalidTokenError
from redis.asyncio import Redis

from voltari_gateway.config import get_settings

# JWT "type" claim — guards against a refresh-token being accepted as access.
_TYPE_ACCESS = "access"
_TYPE_REFRESH = "refresh"
# Sprint 6 — short-lived (5 min) token issued after a correct password but
# BEFORE the second factor (TOTP / recovery). Carries only ``user_id``;
# /login/2fa is the single endpoint that accepts it.
_TYPE_PRE_AUTH = "pre_auth"
_PRE_AUTH_TTL_SECONDS = 300  # 5 minutes

# Issuer + audience pin the tokens to this service so a leaked secret used
# elsewhere can't be cross-replayed.
_JWT_ISS = "voltari-gateway"
_JWT_AUD = "voltari-management"

# Redis key for refresh-token whitelist. Includes user_id so revoking all
# sessions of a user is a SCAN+DEL on the user prefix.
_REFRESH_KEY = "refresh:{user_id}:{jti}"


@dataclass(frozen=True)
class AccessClaims:
    """Decoded access token payload."""

    user_id: uuid.UUID
    account_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class RefreshClaims:
    """Decoded refresh token payload."""

    user_id: uuid.UUID
    jti: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class PreAuthClaims:
    """Decoded pre-auth (post-password, pre-2FA) token payload."""

    user_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _encode(payload: dict[str, Any]) -> str:
    settings = get_settings()
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def _decode(token: str, *, expected_type: str) -> dict[str, Any] | None:
    """Verify signature + standard claims. Returns None on any failure."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=_JWT_AUD,
            issuer=_JWT_ISS,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "type"]},
        )
    except InvalidTokenError:
        return None
    except Exception:
        return None
    if payload.get("type") != expected_type:
        return None
    return payload


# --- access tokens ----------------------------------------------------------


def create_access_token(user_id: uuid.UUID, account_id: uuid.UUID) -> tuple[str, datetime]:
    """Mint a fresh access JWT. Returns ``(token, expires_at)``."""
    settings = get_settings()
    now = _now()
    exp = now + timedelta(minutes=settings.jwt_access_ttl_minutes)
    payload = {
        "iss": _JWT_ISS,
        "aud": _JWT_AUD,
        "sub": str(user_id),
        "type": _TYPE_ACCESS,
        "account_id": str(account_id),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return _encode(payload), exp


def verify_access_token(token: str) -> AccessClaims | None:
    """Verify + decode access JWT. Returns None on any failure."""
    payload = _decode(token, expected_type=_TYPE_ACCESS)
    if payload is None:
        return None
    try:
        return AccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            account_id=uuid.UUID(payload["account_id"]),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError, TypeError):
        return None


# --- refresh tokens ---------------------------------------------------------


def create_refresh_token(
    user_id: uuid.UUID,
) -> tuple[str, datetime, str]:
    """Mint a fresh refresh JWT. Returns ``(token, expires_at, jti)``.

    The JTI is *not* registered in Redis here — that's the cookie layer's
    job, since we want one transaction across "set cookie + whitelist JTI".
    """
    settings = get_settings()
    now = _now()
    exp = now + timedelta(days=settings.jwt_refresh_ttl_days)
    jti = secrets.token_urlsafe(16)
    payload = {
        "iss": _JWT_ISS,
        "aud": _JWT_AUD,
        "sub": str(user_id),
        "type": _TYPE_REFRESH,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return _encode(payload), exp, jti


def verify_refresh_token(token: str) -> RefreshClaims | None:
    """Verify + decode refresh JWT. Returns None on any failure.

    NOTE: This only checks the cryptographic envelope. The whitelist check
    (``is_refresh_token_active``) must be called separately because it
    needs Redis.
    """
    payload = _decode(token, expected_type=_TYPE_REFRESH)
    if payload is None:
        return None
    try:
        return RefreshClaims(
            user_id=uuid.UUID(payload["sub"]),
            jti=str(payload["jti"]),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError, TypeError):
        return None


# --- whitelist (Redis) ------------------------------------------------------


def _whitelist_key(user_id: uuid.UUID, jti: str) -> str:
    return _REFRESH_KEY.format(user_id=user_id, jti=jti)


async def register_refresh_token(
    redis: Redis, user_id: uuid.UUID, jti: str, expires_at: datetime
) -> None:
    """Add JTI to Redis with TTL = remaining JWT lifetime."""
    ttl = max(1, int((expires_at - _now()).total_seconds()))
    await redis.setex(_whitelist_key(user_id, jti), ttl, "1")


async def revoke_refresh_token(redis: Redis, user_id: uuid.UUID, jti: str) -> None:
    """Remove JTI from whitelist. Idempotent."""
    await redis.delete(_whitelist_key(user_id, jti))


async def is_refresh_token_active(redis: Redis, user_id: uuid.UUID, jti: str) -> bool:
    """True iff the JTI is still in the whitelist."""
    val = await redis.get(_whitelist_key(user_id, jti))
    return val is not None


# --- pre-auth tokens (Sprint 6, 2FA login flow) ----------------------------


def create_pre_auth_token(user_id: uuid.UUID) -> tuple[str, datetime]:
    """Mint a 5-minute pre-auth JWT used between password and 2FA factors.

    Carries only ``user_id``. NOT bound to an account_id (the user might
    have multiple, but selection happens after 2FA succeeds).
    """
    now = _now()
    exp = now + timedelta(seconds=_PRE_AUTH_TTL_SECONDS)
    payload = {
        "iss": _JWT_ISS,
        "aud": _JWT_AUD,
        "sub": str(user_id),
        "type": _TYPE_PRE_AUTH,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return _encode(payload), exp


def verify_pre_auth_token(token: str) -> PreAuthClaims | None:
    payload = _decode(token, expected_type=_TYPE_PRE_AUTH)
    if payload is None:
        return None
    try:
        return PreAuthClaims(
            user_id=uuid.UUID(payload["sub"]),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError, TypeError):
        return None


async def revoke_all_refresh_tokens(redis: Redis, user_id: uuid.UUID) -> int:
    """Remove every active refresh token for ``user_id``. Returns count.

    SCAN-based — safe for large key spaces, but on a fakeredis cluster of
    millions of users you'd want a per-user secondary index instead.
    """
    pattern = _REFRESH_KEY.format(user_id=user_id, jti="*")
    deleted = 0
    async for key in redis.scan_iter(match=pattern, count=200):
        deleted += await redis.delete(key)
    return deleted
