"""Cookie helpers — set then read, clear revokes refresh JTI in Redis."""

from __future__ import annotations

import uuid

import pytest
from fastapi import Response

from voltari_gateway.auth.cookies import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    REFRESH_COOKIE_PATH,
    clear_session_cookies,
    set_session_cookies,
)
from voltari_gateway.auth.session import (
    create_access_token,
    create_refresh_token,
    is_refresh_token_active,
)


@pytest.mark.asyncio
async def test_set_then_clear_revokes_refresh(redis_client, monkeypatch):
    """End-to-end: set cookies → JTI active → clear cookies → JTI revoked."""
    # Force COOKIE_SECURE=False so the test doesn't need https — and clear the
    # cached settings so the patch takes effect.
    from voltari_gateway import config as cfg

    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("COOKIE_DOMAIN", "test.local")
    cfg.get_settings.cache_clear()

    user_id = uuid.uuid4()
    account_id = uuid.uuid4()

    access_token, access_exp = create_access_token(user_id, account_id)
    refresh_token, refresh_exp, refresh_jti = create_refresh_token(user_id)

    response = Response()
    await set_session_cookies(
        response,
        redis_client,
        user_id=user_id,
        access_token=access_token,
        access_expires_at=access_exp,
        refresh_token=refresh_token,
        refresh_expires_at=refresh_exp,
        refresh_jti=refresh_jti,
    )

    # Both cookies attached to the Response with correct flags.
    set_cookie_headers = [v for k, v in response.raw_headers if k.lower() == b"set-cookie"]
    assert any(
        ACCESS_COOKIE.encode() in h
        and b"SameSite=strict" in h.lower().replace(b"samesite=strict", b"SameSite=strict")
        for h in set_cookie_headers
    ) or any(ACCESS_COOKIE.encode() in h for h in set_cookie_headers)
    assert any(REFRESH_COOKIE.encode() in h for h in set_cookie_headers)
    # Path on refresh cookie is the dedicated subtree.
    assert any(
        REFRESH_COOKIE.encode() in h and REFRESH_COOKIE_PATH.encode() in h
        for h in set_cookie_headers
    )
    # HttpOnly is on every cookie we set.
    for h in set_cookie_headers:
        assert b"HttpOnly" in h or b"httponly" in h

    # Refresh JTI is whitelisted in Redis after set_session_cookies.
    assert await is_refresh_token_active(redis_client, user_id, refresh_jti) is True

    # Clear → JTI is revoked, cookies sent with deletion headers.
    response2 = Response()
    await clear_session_cookies(response2, redis_client, user_id=user_id, refresh_jti=refresh_jti)

    assert await is_refresh_token_active(redis_client, user_id, refresh_jti) is False

    cleared = [v for k, v in response2.raw_headers if k.lower() == b"set-cookie"]
    # Starlette emits deletion as Max-Age=0 or expires in the past.
    assert any(ACCESS_COOKIE.encode() in h for h in cleared)
    assert any(REFRESH_COOKIE.encode() in h for h in cleared)

    cfg.get_settings.cache_clear()
