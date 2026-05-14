"""Integration tests for the management API account endpoints (`/v1/account/*`).

Strategy mirrors test_api_auth.py — real ASGI transport, in-memory SQLite,
fakeredis, captured email backend. We log in via /v1/auth/login to get
session cookies, then exercise GET/PATCH on /v1/account.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    User,
)
from voltari_gateway.email import client as email_client

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_PASSWORD = "correct horse battery staple"

# TD-036 (Sprint 3 Поток H) — legacy CSRF fallback removed.
from tests.auth.conftest import csrf_headers as _csrf_headers  # noqa: E402


def _capture_emails(monkeypatch) -> list[dict[str, str]]:
    captured: list[dict[str, str]] = []

    async def _fake_send(to: str, subject: str, body: str) -> None:
        captured.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(email_client, "send_email", _fake_send)
    # Bound at import time in api/auth.py and api/account.py — patch all sites.
    from voltari_gateway.api import account as account_api
    from voltari_gateway.api import auth as auth_api

    monkeypatch.setattr(auth_api, "send_email", _fake_send)
    monkeypatch.setattr(account_api, "send_email", _fake_send)
    return captured


async def _seed_user(
    db, *, balance_kop: int = 12_345, tariff: Tariff = Tariff.PRO, name: str = "Acme"
) -> User:
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name=name,
            balance_kopecks=balance_kop,
            tariff=tariff,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
            settings={},
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _login(client, user) -> None:
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# GET /v1/account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_account_returns_correct_fields(client, db, redis_client):
    user = await _seed_user(db, balance_kop=99_900, tariff=Tariff.PRO, name="Acme Corp")
    await _login(client, user)

    r = await client.get("/v1/account")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == str(user.id)
    assert body["email"] == user.email
    assert uuid.UUID(body["account_id"])  # parses
    assert body["name"] == "Acme Corp"
    assert body["tariff"] == "pro"
    assert body["balance_kopecks"] == 99_900
    assert body["prompt_logging_enabled"] is True
    assert body["notifications"] == {}
    assert body["email_verified"] is True
    assert "created_at" in body


@pytest.mark.asyncio
async def test_get_account_requires_session(client):
    """No cookies → 401, NOT 200 with a guest envelope."""
    r = await client.get("/v1/account")
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_get_account_telegram_link_unlinked_default(client, db, redis_client):
    """Fresh user has no Telegram chat — payload reports linked=False."""
    user = await _seed_user(db)
    await _login(client, user)

    body = (await client.get("/v1/account")).json()
    assert body["telegram_link"] == {"linked": False, "chat_id": None}


@pytest.mark.asyncio
async def test_get_account_telegram_link_when_paired(client, db, redis_client):
    """After the bot writes users.telegram_chat_id, /account exposes it
    so the dashboard can render the «Подключён. Chat ID: …» plate."""
    user = await _seed_user(db)
    user.telegram_chat_id = 811_762_018
    db.add(user)
    await db.commit()
    await _login(client, user)

    body = (await client.get("/v1/account")).json()
    assert body["telegram_link"] == {"linked": True, "chat_id": "811762018"}


@pytest.mark.asyncio
async def test_get_account_telegram_link_after_revoke(client, db, redis_client):
    """DELETE /account/telegram-link must flip the snapshot back."""
    user = await _seed_user(db)
    user.telegram_chat_id = 42
    db.add(user)
    await db.commit()
    await _login(client, user)

    r = await client.delete("/v1/account/telegram-link", headers=await _csrf_headers(client))
    assert r.status_code == 204

    body = (await client.get("/v1/account")).json()
    assert body["telegram_link"] == {"linked": False, "chat_id": None}


# ---------------------------------------------------------------------------
# PATCH /v1/account/profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_profile_updates_name(client, db, redis_client):
    user = await _seed_user(db, name="Old Name")
    await _login(client, user)

    r = await client.patch(
        "/v1/account/profile",
        json={"name": "Brand New"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Brand New"

    fresh = await db.execute(select(Account).where(Account.owner_id == user.id))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.name == "Brand New"


@pytest.mark.asyncio
async def test_patch_profile_changing_email_resets_verified_and_sends_email(
    client, db, redis_client, monkeypatch
):
    captured = _capture_emails(monkeypatch)
    user = await _seed_user(db)
    await _login(client, user)

    new_email = f"new-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.patch(
        "/v1/account/profile",
        json={"email": new_email},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == new_email
    assert body["email_verified"] is False

    # User row reflects the new email + cleared verified flag + token hash.
    fresh = await db.execute(select(User).where(User.id == user.id))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.email == new_email
    assert refreshed.email_verified is False
    assert refreshed.verification_token is not None

    # New verification email was dispatched.
    assert any(e["to"] == new_email for e in captured)


@pytest.mark.asyncio
async def test_patch_profile_email_collision_returns_409(client, db, redis_client, monkeypatch):
    """Changing to an email already on another User → 409 email_taken."""
    _capture_emails(monkeypatch)
    other = await _seed_user(db)
    user = await _seed_user(db)
    await _login(client, user)

    r = await client.patch(
        "/v1/account/profile",
        json={"email": other.email},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "email_taken"


@pytest.mark.asyncio
async def test_patch_profile_requires_csrf_header(client, db, redis_client):
    """Mutating PATCH without CSRF token (post-TD-036) → 403."""
    user = await _seed_user(db)
    await _login(client, user)

    r = await client.patch("/v1/account/profile", json={"name": "X"})
    assert r.status_code == 403
    # Sprint 2: error code unified with the new double-submit pattern.
    assert r.json()["error"]["code"] == "csrf_invalid"


# ---------------------------------------------------------------------------
# PATCH /v1/account/settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_settings_updates_prompt_logging_enabled(client, db, redis_client):
    """API-name `prompt_logging_enabled` maps to DB column `store_prompts`."""
    user = await _seed_user(db)
    await _login(client, user)

    # Default after signup is True; flip to False.
    r = await client.patch(
        "/v1/account/settings",
        json={"prompt_logging_enabled": False},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200, r.text
    assert r.json()["prompt_logging_enabled"] is False

    fresh = await db.execute(select(Account).where(Account.owner_id == user.id))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.store_prompts is False


@pytest.mark.asyncio
async def test_patch_settings_persists_notifications(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)

    payload = {
        "notifications": {
            "billing_low_balance": True,
            "weekly_digest": False,
        }
    }
    r = await client.patch(
        "/v1/account/settings", json=payload, headers=await _csrf_headers(client)
    )
    assert r.status_code == 200, r.text
    assert r.json()["notifications"] == payload["notifications"]

    # GET round-trip to confirm persistence + reload from DB.
    g = await client.get("/v1/account")
    assert g.status_code == 200
    assert g.json()["notifications"] == payload["notifications"]


# ---------------------------------------------------------------------------
# Email change → revoke all refresh tokens (security)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_change_revokes_all_refresh_tokens(client, db, redis_client, monkeypatch):
    """Changing email = compromise of the login channel. Drop every JTI.

    Two devices logged in (current SPA + a second device). After a successful
    PATCH /profile {email}, both refresh JTIs must disappear from the Redis
    whitelist, and the caller's cookies must be cleared on the response.
    """
    _capture_emails(monkeypatch)
    from voltari_gateway.auth.session import (
        create_refresh_token,
        is_refresh_token_active,
        register_refresh_token,
        verify_refresh_token,
    )

    user = await _seed_user(db)
    await _login(client, user)

    # JTI #1 = the cookie we just received from /login.
    refresh_cookie = client.cookies.get("vlt_refresh")
    claims_a = verify_refresh_token(refresh_cookie)
    assert claims_a is not None
    assert await is_refresh_token_active(redis_client, claims_a.user_id, claims_a.jti)

    # JTI #2 = a "second device" — issue and whitelist directly.
    _, exp_b, jti_b = create_refresh_token(user.id)
    await register_refresh_token(redis_client, user.id, jti_b, exp_b)
    assert await is_refresh_token_active(redis_client, user.id, jti_b)

    # Change email.
    new_email = f"new-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.patch(
        "/v1/account/profile",
        json={"email": new_email},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200, r.text
    assert r.json()["email"] == new_email
    assert r.json()["email_verified"] is False

    # Both JTIs gone from whitelist.
    assert not await is_refresh_token_active(redis_client, claims_a.user_id, claims_a.jti)
    assert not await is_refresh_token_active(redis_client, user.id, jti_b)

    # Response cleared cookies on the SPA. Set-Cookie headers should contain
    # ``vlt_access`` and ``vlt_refresh`` with an expiration in the past
    # (Starlette's delete_cookie sets Max-Age=0).
    set_cookie_blob = "\n".join(
        r.headers.get_list("set-cookie")
        if hasattr(r.headers, "get_list")
        else [r.headers.get("set-cookie", "")]
    )
    assert "vlt_access" in set_cookie_blob
    assert "vlt_refresh" in set_cookie_blob
    assert "Max-Age=0" in set_cookie_blob
