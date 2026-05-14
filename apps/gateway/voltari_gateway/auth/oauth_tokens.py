"""OAuth-specific JWT helpers — distinct from dashboard session tokens.

We deliberately use a different ``aud`` (``voltari-oauth``) and a
different ``type`` (``oauth_access`` / ``oauth_refresh``) so a dashboard
JWT can never be passed off as an OAuth token (or vice versa) even if a
secret leak gives the attacker valid signing material — the strict
audience check in ``jwt.decode`` rejects the cross-replay.

Design mirrors ``voltari_gateway.auth.session`` for consistency: dataclass
claims, secrets-only encoding via ``pyjwt``, no Redis I/O at mint time
(refresh whitelist is the cookie/handler layer's job — see
``oauth_refresh_whitelist.py`` if/when we add server-side revoke).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt.exceptions import InvalidTokenError

from voltari_gateway.config import get_settings

_TYPE_OAUTH_ACCESS = "oauth_access"
_TYPE_OAUTH_REFRESH = "oauth_refresh"

_JWT_ISS = "voltari-gateway"
# Distinct from the dashboard's "voltari-management" — cross-replay impossible.
_JWT_AUD = "voltari-oauth"


@dataclass(frozen=True)
class OAuthAccessClaims:
    user_id: uuid.UUID
    account_id: uuid.UUID
    client_id: str
    scopes: list[str]
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class OAuthRefreshClaims:
    user_id: uuid.UUID
    account_id: uuid.UUID
    client_id: str
    scopes: list[str]
    jti: str
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
    settings = get_settings()
    if not token:
        return None
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


def create_oauth_access_token(
    *,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    client_id: str,
    scopes: list[str],
) -> tuple[str, datetime, dict[str, Any]]:
    """Mint an OAuth access JWT.

    Returns ``(token, expires_at, raw_claims)``. The raw claims dict is
    handy for the audit log without re-decoding.
    """
    settings = get_settings()
    now = _now()
    exp = now + timedelta(minutes=settings.oauth_access_ttl_minutes)
    payload: dict[str, Any] = {
        "iss": _JWT_ISS,
        "aud": _JWT_AUD,
        "sub": str(user_id),
        "type": _TYPE_OAUTH_ACCESS,
        "account_id": str(account_id),
        "client_id": client_id,
        "scope": " ".join(scopes),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return _encode(payload), exp, payload


def verify_oauth_access_token(token: str) -> OAuthAccessClaims | None:
    payload = _decode(token, expected_type=_TYPE_OAUTH_ACCESS)
    if payload is None:
        return None
    try:
        scope_str = str(payload.get("scope", ""))
        scopes = [s for s in scope_str.split(" ") if s]
        return OAuthAccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            account_id=uuid.UUID(payload["account_id"]),
            client_id=str(payload["client_id"]),
            scopes=scopes,
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError, TypeError):
        return None


def create_oauth_refresh_token(
    *,
    user_id: uuid.UUID,
    account_id: uuid.UUID | None = None,
    client_id: str,
    scopes: list[str],
) -> tuple[str, datetime, str]:
    """Mint an OAuth refresh JWT. Returns ``(token, expires_at, jti)``.

    ``account_id`` is optional in the signature so a future test can pass
    ``None``; the implementation falls through to ``uuid.UUID(int=0)`` so
    the claim is always present (refresh always re-binds to the same
    account on token exchange).
    """
    settings = get_settings()
    now = _now()
    exp = now + timedelta(days=settings.oauth_refresh_ttl_days)
    jti = secrets.token_urlsafe(16)
    payload: dict[str, Any] = {
        "iss": _JWT_ISS,
        "aud": _JWT_AUD,
        "sub": str(user_id),
        "type": _TYPE_OAUTH_REFRESH,
        "account_id": str(account_id) if account_id is not None else str(uuid.UUID(int=0)),
        "client_id": client_id,
        "scope": " ".join(scopes),
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return _encode(payload), exp, jti


def verify_oauth_refresh_token(token: str) -> OAuthRefreshClaims | None:
    payload = _decode(token, expected_type=_TYPE_OAUTH_REFRESH)
    if payload is None:
        return None
    try:
        scope_str = str(payload.get("scope", ""))
        scopes = [s for s in scope_str.split(" ") if s]
        return OAuthRefreshClaims(
            user_id=uuid.UUID(payload["sub"]),
            account_id=uuid.UUID(payload["account_id"]),
            client_id=str(payload["client_id"]),
            scopes=scopes,
            jti=str(payload["jti"]),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError, TypeError):
        return None
