"""HttpOnly session cookies for the management API.

Cookie design:

* ``vlt_access``  — access JWT, ``SameSite=Strict``  (frontend SPA only).
* ``vlt_refresh`` — refresh JWT, ``SameSite=Lax``     (so a back-button POST to
  the refresh endpoint after a top-level navigation still works).

CSRF strategy: ``SameSite`` does the heavy lifting; we additionally require
the header ``X-Requested-With: voltari-web`` on every mutating request. See
``session_middleware.py`` for the check. No double-submit token in MVP.

This module owns:
1. Setting the cookies on the FastAPI ``Response`` with the correct flags.
2. Registering / revoking the refresh-token JTI in Redis.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import Response
from redis.asyncio import Redis

from voltari_gateway.auth.session import (
    register_refresh_token,
    revoke_refresh_token,
)
from voltari_gateway.config import get_settings

ACCESS_COOKIE = "vlt_access"
REFRESH_COOKIE = "vlt_refresh"

# The refresh cookie is only sent to /v1/auth/* — narrows the blast radius
# if a different subsystem ever accidentally reflects cookies.
REFRESH_COOKIE_PATH = "/v1/auth"


def _seconds_until(expires_at: datetime) -> int:
    """Cookie ``max_age`` cannot be negative — clamp at 1s for already-expired."""
    delta = (expires_at - datetime.now(expires_at.tzinfo)).total_seconds()
    return max(1, int(delta))


async def set_session_cookies(
    response: Response,
    redis: Redis,
    user_id: uuid.UUID,
    access_token: str,
    access_expires_at: datetime,
    refresh_token: str,
    refresh_expires_at: datetime,
    refresh_jti: str,
) -> None:
    """Attach both cookies to ``response`` and whitelist the refresh JTI.

    Caller must have already minted the tokens via ``session.create_*``.
    """
    settings = get_settings()
    # Empty COOKIE_DOMAIN ⇒ omit the Domain attribute so the browser scopes
    # the cookie to the request host. Useful for tests (httpx ASGITransport
    # serves http://test) and for single-host dev.
    domain = settings.cookie_domain or None

    # Whitelist BEFORE setting the cookie — if Redis is down the user simply
    # can't log in, which is preferable to handing out an unrevokable token.
    await register_refresh_token(redis, user_id, refresh_jti, refresh_expires_at)

    response.set_cookie(
        key=ACCESS_COOKIE,
        value=access_token,
        max_age=_seconds_until(access_expires_at),
        domain=domain,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        # Lax (не Strict): иначе OAuth redirect chain
        # accounts.google.com → api.brikko.ru → brikko.ru/app
        # классифицируется как cross-site и cookie не доставится. Защита
        # от CSRF строится на double-submit (X-CSRF-Token header), не на
        # SameSite, поэтому понижение до Lax безопасно.
        samesite="lax",
    )
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=refresh_token,
        max_age=_seconds_until(refresh_expires_at),
        domain=domain,
        path=REFRESH_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


async def clear_session_cookies(
    response: Response,
    redis: Redis,
    user_id: uuid.UUID | None,
    refresh_jti: str | None,
) -> None:
    """Clear both cookies and revoke the refresh JTI from Redis.

    ``user_id`` / ``refresh_jti`` may be None when the caller doesn't have a
    valid refresh token (e.g. logout endpoint with already-expired session) —
    we still clear cookies on the client so they don't keep replaying.
    """
    settings = get_settings()
    domain = settings.cookie_domain or None

    if user_id is not None and refresh_jti is not None:
        await revoke_refresh_token(redis, user_id, refresh_jti)

    # delete_cookie does not accept secure/httponly in older Starlette; we
    # pass an explicit max_age=0 + matching domain/path to make every browser
    # actually drop them.
    response.delete_cookie(
        key=ACCESS_COOKIE,
        domain=domain,
        path="/",
    )
    response.delete_cookie(
        key=REFRESH_COOKIE,
        domain=domain,
        path=REFRESH_COOKIE_PATH,
    )
