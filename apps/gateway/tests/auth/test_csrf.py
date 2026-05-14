"""Tests for the double-submit CSRF protocol (FE P0-5 / BE coord).

Covers:

* ``GET /v1/auth/csrf`` returns a token that matches the cookie value.
* Mutating cookie-auth requests without CSRF artefacts → 403.
* Mismatched header / cookie pair → 403.
* Correct pair → request passes through.
* Bearer auth ignores CSRF entirely.
* Legacy ``X-Requested-With: voltari-web`` is REJECTED (TD-036, Sprint 3
  Поток H removed the transitional fallback).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from voltari_gateway.auth.csrf import CSRF_COOKIE, CSRF_HEADER

# ---- /csrf bootstrap --------------------------------------------------------


@pytest.mark.asyncio
async def test_csrf_endpoint_returns_token_and_cookie(
    client: AsyncClient,
) -> None:
    resp = await client.get("/v1/auth/csrf")
    assert resp.status_code == 200
    body = resp.json()
    token = body["csrf_token"]
    assert isinstance(token, str)
    assert len(token) >= 64

    # Cookie value must equal body value (that's the whole point —
    # the SPA uses double-submit and the server compares the two).
    cookies = resp.cookies
    assert cookies.get(CSRF_COOKIE) == token


@pytest.mark.asyncio
async def test_csrf_endpoint_rotates_token(client: AsyncClient) -> None:
    """Each call should produce a fresh token."""
    r1 = await client.get("/v1/auth/csrf")
    r2 = await client.get("/v1/auth/csrf")
    assert r1.json()["csrf_token"] != r2.json()["csrf_token"]


# ---- cookie-auth mutating requests ------------------------------------------


async def _login_with_session(client: AsyncClient, db: Any) -> tuple[str, str, str]:
    """Helper: spin up a verified user, log in, return (csrf_token,
    vlt_access cookie, vlt_csrf cookie)."""
    from voltari_gateway.auth.password import hash_password
    from voltari_gateway.db.models import (
        Account,
        AccountStatus,
        Tariff,
        User,
    )

    email = f"csrf-{uuid.uuid4().hex[:8]}@example.com"
    password = "S0meStrongPassword!"

    user = User(
        email=email,
        password_hash=hash_password(password),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name="CSRF test",
        balance_kopecks=0,
        tariff=Tariff.PAYG,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
    )
    db.add(account)
    await db.commit()

    login = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200
    csrf_token = login.json()["csrf_token"]
    vlt_access = login.cookies["vlt_access"]
    vlt_csrf = login.cookies[CSRF_COOKIE]
    return csrf_token, vlt_access, vlt_csrf


@pytest.mark.asyncio
async def test_login_returns_csrf_token(client: AsyncClient, db: Any) -> None:
    csrf_token, _, vlt_csrf = await _login_with_session(client, db)
    assert csrf_token == vlt_csrf


@pytest.mark.asyncio
async def test_mutating_endpoint_without_csrf_returns_403(client: AsyncClient, db: Any) -> None:
    """A POST with cookies but no CSRF artefacts → 403."""
    _, vlt_access, vlt_csrf = await _login_with_session(client, db)

    # Drop the CSRF cookie + omit the header → server has nothing to verify.
    client.cookies.delete(CSRF_COOKIE)
    resp = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm_test_1234",
            "threshold_kopecks": 5000,
            "topup_kopecks": 50_000,
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_invalid"


@pytest.mark.asyncio
async def test_mutating_endpoint_with_mismatched_csrf_returns_403(
    client: AsyncClient, db: Any
) -> None:
    """Header doesn't match cookie → 403."""
    _, _, _ = await _login_with_session(client, db)

    resp = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm_test_1234",
            "threshold_kopecks": 5000,
            "topup_kopecks": 50_000,
        },
        headers={CSRF_HEADER: "this-is-not-the-cookie-value"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_invalid"


@pytest.mark.asyncio
async def test_mutating_endpoint_with_correct_csrf_passes(client: AsyncClient, db: Any) -> None:
    """Header == cookie + valid session → request reaches handler."""
    csrf_token, _, _ = await _login_with_session(client, db)

    resp = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm_test_1234",
            "threshold_kopecks": 5_000,
            "topup_kopecks": 50_000,
        },
        headers={CSRF_HEADER: csrf_token},
    )
    # Reaches the handler; success means the CSRF gate let it through.
    # Body validation may either succeed (200) or fail at a non-CSRF
    # error — but it MUST NOT be a 403 csrf_invalid.
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_csrf_only_required_for_cookie_auth(
    client: AsyncClient, api_key_fixture: Any
) -> None:
    """Bearer-auth requests don't need CSRF (no replayable ambient creds)."""
    resp = await client.delete(
        "/v1/billing/autorefill",
        headers=api_key_fixture.auth_header,
        # Deliberately no X-CSRF-Token, no X-Requested-With.
    )
    # Endpoint exists → either 200 (no autorefill set up) or 401 if
    # cookies leaked into the request. Critically: NOT a 403.
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_legacy_x_requested_with_is_rejected(client: AsyncClient, db: Any) -> None:
    """TD-036: Sprint 2 ``X-Requested-With: voltari-web`` fallback is gone.

    A request that carries only the legacy header (no double-submit pair)
    must now be rejected with 403 ``csrf_invalid``. The frontend already
    migrated to the new pair at the end of Sprint 2; this test pins the
    "no silent fallback" contract for any straggler client.
    """
    _, _, _ = await _login_with_session(client, db)
    # Drop new artefacts so only legacy header is present.
    client.cookies.delete(CSRF_COOKIE)

    resp = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm_test_1234",
            "threshold_kopecks": 5_000,
            "topup_kopecks": 50_000,
        },
        headers={"X-Requested-With": "voltari-web"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_invalid"


@pytest.mark.asyncio
async def test_csrf_partial_pair_rejected(client: AsyncClient, db: Any) -> None:
    """Header without cookie (or vice versa) MUST 403 — not silently
    fall back to the legacy path. A buggy SPA needs to fail loudly.
    """
    csrf_token, _, _ = await _login_with_session(client, db)
    # Drop the cookie, send the header.
    client.cookies.delete(CSRF_COOKIE)

    resp = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm_test_1234",
            "threshold_kopecks": 5_000,
            "topup_kopecks": 50_000,
        },
        headers={CSRF_HEADER: csrf_token},
    )
    assert resp.status_code == 403
