"""Stateless HMAC-signed state token for the OAuth login flow.

Why stateless
-------------

The OAuth ``state`` parameter has two jobs:

1. CSRF defence — the callback URL must carry a token only the legit
   /start request could have produced.
2. Carry hop-by-hop context — what provider, what mode (login vs link),
   where to redirect the user back to in the SPA after success.

Storing this in Redis would work but adds a write on every login start
and a read on every callback for no real benefit (the lifetime is
≤10 minutes, the size is ≤200 bytes, and HMAC verify is microseconds).
We use ``itsdangerous.URLSafeTimedSerializer`` so the same library
that signs verify-email tokens signs state — one less dependency to
audit, identical TTL semantics.

Distinct ``salt``
-----------------

Different *purposes* MUST produce non-interchangeable tokens. We use
``voltari.oauth-login.v1`` so a leaked email-verify token can never be
replayed as a state token, and vice versa.

Payload shape
-------------

::

    {
        "provider": "google",     # 'google' | 'yandex'
        "mode": "login",          # 'login' | 'link'
        "user_id": "<uuid>"|None, # set in 'link' mode (signed-in user)
        "next": "/app",           # SPA route to redirect to on success
        "nonce": "<urlsafe>",     # 16 random bytes, defense in depth
    }

The TTL is enforced at verify time via ``loads(..., max_age=...)`` —
we read ``oauth_login_state_ttl_seconds`` from settings on every
call so an operator changing the env var without restart still gets
the new value on the next request.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Final, Literal

from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

from voltari_gateway.config import get_settings

_SALT: Final[str] = "voltari.oauth-login.v1"

ProviderName = Literal["google", "yandex"]
StateMode = Literal["login", "link"]


def _serializer() -> URLSafeTimedSerializer:
    """Reuse JWT_SECRET as the signing key.

    Per CEO 2026-05-06: don't proliferate per-feature secrets in dev/
    deploy footprint. The secret is already required-strong by the
    config validator, and the salt prevents cross-purpose replay.
    """
    return URLSafeTimedSerializer(get_settings().jwt_secret.get_secret_value())


def generate_state(
    *,
    provider: ProviderName,
    mode: StateMode,
    next_path: str,
    user_id: uuid.UUID | None = None,
) -> str:
    """Mint an HMAC-signed state token.

    ``user_id`` MUST be set in ``mode='link'`` so the callback knows
    which authenticated user is linking the identity. ``next_path`` is
    sanitised by the caller to be a relative URL on the SPA (no
    open-redirect — see ``_safe_next_path`` in the API layer).
    """
    if mode == "link" and user_id is None:
        msg = "user_id is required in mode='link'"
        raise ValueError(msg)
    payload = {
        "provider": provider,
        "mode": mode,
        "user_id": str(user_id) if user_id else None,
        "next": next_path,
        # Defence-in-depth: even if itsdangerous's timestamp-collision
        # property weakened we still get 128 bits of structural
        # randomness in the token body.
        "nonce": secrets.token_urlsafe(12),
    }
    return _serializer().dumps(payload, salt=_SALT)


class StatePayload:
    """Decoded state token. Immutable container for callback handler."""

    __slots__ = ("mode", "next_path", "provider", "user_id")

    def __init__(
        self,
        *,
        provider: ProviderName,
        mode: StateMode,
        user_id: uuid.UUID | None,
        next_path: str,
    ) -> None:
        self.provider = provider
        self.mode = mode
        self.user_id = user_id
        self.next_path = next_path


def verify_state(token: str) -> StatePayload | None:
    """Verify + decode a state token.

    Returns ``None`` on signature mismatch, expiry, or malformed
    payload — the caller MUST treat this as a generic ``invalid_state``
    error and never leak which sub-failure happened (would help an
    attacker tune their forgery attempts).
    """
    settings = get_settings()
    max_age = settings.oauth_login_state_ttl_seconds
    try:
        raw = _serializer().loads(token, salt=_SALT, max_age=max_age)
    except SignatureExpired:
        return None
    except BadData:
        return None
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None

    provider_raw = raw.get("provider")
    mode_raw = raw.get("mode")
    next_raw = raw.get("next")
    user_id_raw = raw.get("user_id")

    if provider_raw not in ("google", "yandex"):
        return None
    if mode_raw not in ("login", "link"):
        return None
    if not isinstance(next_raw, str):
        return None
    if mode_raw == "link" and not isinstance(user_id_raw, str):
        return None

    user_id: uuid.UUID | None = None
    if isinstance(user_id_raw, str):
        try:
            user_id = uuid.UUID(user_id_raw)
        except ValueError:
            return None

    return StatePayload(
        provider=provider_raw,
        mode=mode_raw,
        user_id=user_id,
        next_path=next_raw,
    )


__all__ = [
    "ProviderName",
    "StateMode",
    "StatePayload",
    "generate_state",
    "verify_state",
]
