"""DELETE /v1/billing/card — Phase 2 endpoint.

Covers:
  * test_unlink_clears_autorefill_pm_id
  * test_unlink_keeps_welcome_credit
  * test_unlink_warns_active_subscription
  * test_unlink_idempotent_when_no_card_linked
  * test_unlink_disables_autorefill_loop
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from voltari_gateway import config as cfg
from voltari_gateway.auth import rate_limit as rl
from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    User,
)

_PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def cookie_env(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("COOKIE_DOMAIN", "")
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    monkeypatch.setenv("BASE_URL_FRONTEND", "http://test")
    cfg.get_settings.cache_clear()
    rl.reload_from_settings()
    yield
    cfg.get_settings.cache_clear()
    rl.reload_from_settings()


@pytest_asyncio.fixture
async def seeded_user_with_card(db):
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name="Unlink probe",
        balance_kopecks=100_00,  # 100 ₽ welcome credit already granted
        tariff=Tariff.PAYG,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
        settings={},
        autorefill_pm_id="pmid-saved-1",
        autorefill_enabled=True,
    )
    db.add(account)
    await db.commit()
    await db.refresh(user)
    await db.refresh(account)
    return user, account


async def _login(client, user):
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text
    csrf_r = await client.get("/v1/auth/csrf")
    from voltari_gateway.auth.csrf import CSRF_HEADER

    return {CSRF_HEADER: csrf_r.json()["csrf_token"]}


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlink_clears_autorefill_pm_id(client, db, seeded_user_with_card):
    user, account = seeded_user_with_card
    csrf = await _login(client, user)

    r = await client.delete("/v1/billing/card", headers=csrf)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["message"] == "unlinked"
    assert body["active_subscription_warning"] is False

    await db.refresh(account)
    assert account.autorefill_pm_id is None


@pytest.mark.asyncio
async def test_unlink_keeps_welcome_credit(client, db, seeded_user_with_card):
    user, account = seeded_user_with_card
    original_balance = account.balance_kopecks
    csrf = await _login(client, user)

    r = await client.delete("/v1/billing/card", headers=csrf)
    assert r.status_code == 200, r.text

    await db.refresh(account)
    # Welcome credit untouched — unlink only clears the PM handle.
    assert account.balance_kopecks == original_balance


@pytest.mark.asyncio
async def test_unlink_warns_active_subscription(client, db, seeded_user_with_card):
    user, account = seeded_user_with_card
    account.subscription_tier = "pro"
    account.subscription_active_until = datetime.now(UTC) + timedelta(days=15)
    db.add(account)
    await db.commit()

    csrf = await _login(client, user)
    r = await client.delete("/v1/billing/card", headers=csrf)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active_subscription_warning"] is True

    # autorefill_pm_id is still cleared even with the warning — the user
    # explicitly asked to unlink.
    await db.refresh(account)
    assert account.autorefill_pm_id is None


@pytest.mark.asyncio
async def test_unlink_idempotent_when_no_card_linked(client, db, seeded_user_with_card):
    user, account = seeded_user_with_card
    account.autorefill_pm_id = None
    account.autorefill_enabled = False
    db.add(account)
    await db.commit()

    csrf = await _login(client, user)
    r = await client.delete("/v1/billing/card", headers=csrf)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["message"] == "no_card_linked"


@pytest.mark.asyncio
async def test_unlink_disables_autorefill_loop(client, db, seeded_user_with_card):
    """autorefill_enabled was True; unlink must flip it off so the cron
    doesn't try to charge a NULL pm_id on the next tick."""
    user, account = seeded_user_with_card
    assert account.autorefill_enabled is True

    csrf = await _login(client, user)
    r = await client.delete("/v1/billing/card", headers=csrf)
    assert r.status_code == 200, r.text

    await db.refresh(account)
    assert account.autorefill_enabled is False
