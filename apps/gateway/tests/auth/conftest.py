"""Test fixtures for auth module + management API tests.

Two responsibilities:

1. Autouse ``clear_settings_cache`` so tests that ``monkeypatch.setenv`` env
   vars actually see them (otherwise pydantic-settings' lru_cache shadows
   the change). Mentioned in Commit 1's tech-debt list.
2. Autouse ``reset_rate_limits`` so per-test counters from in-memory
   ``auth.rate_limit`` don't bleed.

Plus narrow helpers used only by the management-API integration tests
in ``test_api_auth.py``.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from voltari_gateway import config as cfg
from voltari_gateway.auth import rate_limit as rl


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch):
    """Settings is an lru_cached singleton — drop it before AND after each test
    so any monkeypatched env vars in *this* test don't leak into the next.

    Also lock down a few env vars that make sense across the whole tests/auth
    suite (Secure cookies off, no fixed domain, console email backend) — the
    tests run via httpx's ASGITransport on http://test where Secure cookies
    would be dropped and a Domain=.brikko.ru would never match.
    """
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("COOKIE_DOMAIN", "")
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    monkeypatch.setenv("BASE_URL_FRONTEND", "http://test")
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """In-memory rate limiter is process-global. Wipe between tests."""
    rl.reload_from_settings()
    yield
    rl.reload_from_settings()


# ---------------------------------------------------------------------------
# Helpers used by test_api_auth.py
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def cors_safe_env(monkeypatch):
    """Env tuned for HTTP test client (httpx ASGITransport @ http://test)."""
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("COOKIE_DOMAIN", "")  # browser-default; cookies attach to *.test
    monkeypatch.setenv("CORS_ORIGINS", "http://test")
    monkeypatch.setenv("BASE_URL_FRONTEND", "http://test")
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    cfg.get_settings.cache_clear()
    yield


def random_email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@test.local"


# ---------------------------------------------------------------------------
# CSRF double-submit helper (TD-036, Sprint 3 Поток H)
# ---------------------------------------------------------------------------
#
# Sprint 3 Поток H removed the legacy ``X-Requested-With: voltari-web``
# fallback. All tests that mutate via a cookie-authenticated session now
# need the double-submit pair: ``X-CSRF-Token`` header equal to the
# ``vlt_csrf`` cookie value.
#
# httpx's ``AsyncClient`` already stores ``Set-Cookie`` from prior
# responses, so once ``GET /v1/auth/csrf`` (or ``POST /v1/auth/login``)
# has set the cookie all we need from the helper is the header to add.


async def csrf_headers(client) -> dict[str, str]:
    """Return ``{"X-CSRF-Token": <token>}`` after ensuring the cookie is set.

    Calls ``GET /v1/auth/csrf`` (idempotent — issues a token and sets the
    cookie). Safe to call multiple times; tokens rotate, but each call
    stores the latest pair on the client. Use **once per logical
    user-action group** to avoid stale-token mismatches.
    """
    from voltari_gateway.auth.csrf import CSRF_HEADER

    r = await client.get("/v1/auth/csrf")
    assert r.status_code == 200, r.text
    return {CSRF_HEADER: r.json()["csrf_token"]}
