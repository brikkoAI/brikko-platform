"""Tests for the subscription model (Pivot 2026-05-15).

Covers:
  1. POST /v1/billing/subscribe requires a linked card (412 otherwise).
  2. /subscribe creates a recurring ЮKassa charge + returns 202 pending.
  3. /v1/anonymize skips billing entirely when subscription is active.
  4. /v1/anonymize returns 402 ``subscription_required`` when PAYG balance
     is zero AND no active subscription.
  5. POST /v1/billing/subscribe/cancel sets ``canceled_at`` but keeps
     ``active_until`` unchanged.
  6. GET /v1/billing/subscription returns the current state.

Subscription tier strings are stored in ``Account.subscription_tier`` as
plain strings ('payg' | 'pro' | 'team'); the existing ``Account.tariff``
enum stays around for legacy entitlements.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
import respx
from sqlalchemy import select

from voltari_gateway import config as cfg
from voltari_gateway.auth import rate_limit as rl
from voltari_gateway.auth.password import hash_password
from voltari_gateway.billing.anonymize_billing import (
    ANONYMIZE_FREE_DAILY_QUOTA,
    _counter_key,
    _msk_date_key,
)
from voltari_gateway.billing.subscription import (
    SUBSCRIPTION_PERIOD_DAYS,
    TIER_PRICE_KOPECKS,
    build_sub_ref_id,
    is_subscription_active,
)
from voltari_gateway.billing.yookassa import YooKassaClient, YooKassaConfig
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    Tariff,
    Transaction,
    TransactionKind,
    User,
)

_PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def cookie_friendly_env(monkeypatch):
    """Cookie/CORS env so httpx ASGI transport can run cookie-auth flows."""
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("COOKIE_DOMAIN", "")
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    monkeypatch.setenv("BASE_URL_FRONTEND", "http://test")
    cfg.get_settings.cache_clear()
    rl.reload_from_settings()
    yield
    cfg.get_settings.cache_clear()
    rl.reload_from_settings()


def _yookassa_config() -> YooKassaConfig:
    return YooKassaConfig(
        shop_id="shop-test",
        secret_key="secret-test",
        webhook_secret="hook-secret",
        return_url_template="https://test/return?account={account_id}",
        base_url="https://test-yookassa/v3",
    )


def _hmac_header(raw: bytes, secret: str = "hook-secret") -> str:
    digest = hmac.new(secret.encode(), raw, hashlib.sha1).hexdigest()
    return f"sha1={digest}"


@pytest_asyncio.fixture
async def app_with_yookassa(app):
    cfg = _yookassa_config()
    app.state.yookassa = YooKassaClient(cfg)
    yield app
    await app.state.yookassa.aclose()


@pytest_asyncio.fixture
async def seeded_user(db):
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name="Sub test",
        balance_kopecks=10_000,
        tariff=Tariff.PAYG,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
        settings={},
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


# --- 1) /subscribe requires linked card -------------------------------------


@pytest.mark.asyncio
async def test_subscribe_pro_requires_card_linked(
    app_with_yookassa, client, db, seeded_user
):
    user, account = seeded_user
    # No autorefill_pm_id set on the account.
    assert account.autorefill_pm_id is None

    csrf = await _login(client, user)
    r = await client.post(
        "/v1/billing/subscribe",
        json={"tier": "pro"},
        headers=csrf,
    )
    assert r.status_code == 412, r.text
    assert r.json()["error"]["code"] == "card_not_linked"


# --- 2) /subscribe creates pending charge -----------------------------------


@pytest.mark.asyncio
async def test_subscribe_creates_pending_charge(
    app_with_yookassa, client, db, seeded_user
):
    user, account = seeded_user
    account.autorefill_pm_id = "pmid-saved-1"
    db.add(account)
    await db.commit()

    csrf = await _login(client, user)
    yk_payload = {
        "id": "pay_sub_1",
        "status": "pending",
        "amount": {"value": "290.00", "currency": "RUB"},
    }
    with respx.mock(base_url="https://test-yookassa/v3") as mock:
        route = mock.post("/payments").respond(200, json=yk_payload)
        r = await client.post(
            "/v1/billing/subscribe",
            json={"tier": "pro"},
            headers=csrf,
        )

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["tier"] == "pro"
    assert body["amount_kopecks"] == TIER_PRICE_KOPECKS["pro"]

    # The outbound ЮKassa request used the saved pm_id + subscription metadata.
    sent = json.loads(route.calls[0].request.content)
    assert sent["payment_method_id"] == "pmid-saved-1"
    assert sent["amount"]["value"] == "290.00"
    assert sent["metadata"]["purpose"] == "subscription_charge"
    assert sent["metadata"]["tier"] == "pro"

    # The endpoint does NOT activate the subscription immediately —
    # that's the webhook's job. Tier remains 'payg' at this moment.
    await db.refresh(account)
    assert account.subscription_tier == "payg"
    assert account.subscription_active_until is None


# --- 3) Active subscription bypasses billing in /v1/anonymize ---------------


@pytest_asyncio.fixture
async def api_key_with_subscription(db):
    """Seed a user/account with active Pro subscription + working API key."""
    from voltari_gateway.auth.keys import generate_api_key

    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()

    account = Account(
        owner_id=user.id,
        name="Pro sub",
        balance_kopecks=0,  # zero balance — subscription should bypass
        tariff=Tariff.PAYG,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
        subscription_tier="pro",
        subscription_active_until=datetime.now(UTC) + timedelta(days=15),
        settings={},
    )
    db.add(account)
    await db.flush()

    generated = generate_api_key()
    api_key = ApiKey(
        account_id=account.id,
        name="default",
        key_hash=generated.key_hash,
        key_prefix=generated.prefix,
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(account)
    await db.refresh(api_key)

    return user, account, api_key, generated.plaintext


@pytest.mark.asyncio
async def test_anonymize_skips_billing_when_active_subscription(
    client, db, api_key_with_subscription, redis_client
):
    user, account, api_key, plaintext = api_key_with_subscription
    headers = {"Authorization": f"Bearer {plaintext}"}

    # Pre-set the daily counter at the free quota so a non-subscriber would
    # be charged. The subscriber should pass through without touching it.
    counter = _counter_key(account.id, _msk_date_key())
    await redis_client.set(counter, str(ANONYMIZE_FREE_DAILY_QUOTA))

    r = await client.post(
        "/v1/anonymize",
        json={"text": "ИНН 7707083893"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    # Balance untouched (was 0; still 0).
    await db.refresh(account)
    assert account.balance_kopecks == 0

    # No CHARGE transaction was written.
    charges = (
        (
            await db.execute(
                select(Transaction).where(
                    Transaction.account_id == account.id,
                    Transaction.type == TransactionKind.CHARGE,
                )
            )
        )
        .scalars()
        .all()
    )
    assert charges == []


# --- 4) /v1/anonymize returns 402 ``subscription_required`` -----------------


@pytest.mark.asyncio
async def test_anonymize_402_when_no_subscription_and_no_balance(
    client, api_key_fixture, redis_client, db
):
    """Zero balance + free quota exhausted + no subscription → 402."""
    # Zero out balance + exhaust daily counter.
    api_key_fixture.account.balance_kopecks = 0
    api_key_fixture.account.subscription_tier = "payg"
    api_key_fixture.account.subscription_active_until = None
    db.add(api_key_fixture.account)
    await db.commit()

    counter = _counter_key(api_key_fixture.account.id, _msk_date_key())
    await redis_client.set(counter, str(ANONYMIZE_FREE_DAILY_QUOTA))

    r = await client.post(
        "/v1/anonymize",
        json={"text": "ИНН 7707083893"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 402, r.text
    body = r.json()
    assert body["error"] == "subscription_required"
    assert "Subscribe to Pro" in body["message"]
    assert body["subscribe_url"].endswith("brikko.ru/app/billing")


# --- 5) Cancel keeps active_until ------------------------------------------


@pytest.mark.asyncio
async def test_cancel_keeps_active_until(
    app_with_yookassa, client, db, seeded_user
):
    user, account = seeded_user
    active_until = datetime.now(UTC) + timedelta(days=20)
    account.subscription_tier = "pro"
    account.subscription_active_until = active_until
    account.autorefill_pm_id = "pmid-saved-1"
    db.add(account)
    await db.commit()

    csrf = await _login(client, user)
    r = await client.post(
        "/v1/billing/subscribe/cancel",
        json={},
        headers=csrf,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cancelled"] is True

    await db.refresh(account)
    assert account.subscription_canceled_at is not None
    # active_until preserved — user keeps access through paid period.
    db_active = account.subscription_active_until
    if db_active.tzinfo is None:
        db_active = db_active.replace(tzinfo=UTC)
    assert abs((db_active - active_until).total_seconds()) < 1
    # Tier preserved until expiry.
    assert account.subscription_tier == "pro"


# --- 6) GET /v1/billing/subscription ----------------------------------------


@pytest.mark.asyncio
async def test_get_subscription_returns_state(
    app_with_yookassa, client, db, seeded_user
):
    user, account = seeded_user
    account.subscription_tier = "team"
    account.subscription_active_until = datetime.now(UTC) + timedelta(days=10)
    account.autorefill_pm_id = "pmid-saved-1"
    db.add(account)
    await db.commit()

    await _login(client, user)
    r = await client.get("/v1/billing/subscription")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tier"] == "team"
    assert body["active_until"] is not None
    assert body["canceled_at"] is None
    assert body["card_linked"] is True


# --- 7) is_subscription_active helper ---------------------------------------


def test_is_subscription_active_helper():
    """Pure function — no DB. Cover the three branches.

    We build an unsaved Account ORM object via the regular constructor.
    SQLAlchemy properly initialises the InstrumentedAttribute descriptors
    on a constructor call; ``__new__`` alone leaves them un-mapped.
    """
    base = Account(
        owner_id=uuid.uuid4(),
        name="probe",
        subscription_tier="payg",
        subscription_active_until=None,
        subscription_canceled_at=None,
    )
    assert is_subscription_active(base) is False

    # Pro tier but expired → inactive.
    base.subscription_tier = "pro"
    base.subscription_active_until = datetime.now(UTC) - timedelta(days=1)
    assert is_subscription_active(base) is False

    # Pro tier + future active_until → active.
    base.subscription_active_until = datetime.now(UTC) + timedelta(days=5)
    assert is_subscription_active(base) is True

    # Cancelled but still inside paid period → active (user paid; honour it).
    base.subscription_canceled_at = datetime.now(UTC)
    assert is_subscription_active(base) is True


# --- 8) Subscription webhook activates the tier -----------------------------


@pytest.mark.asyncio
async def test_subscription_webhook_activates_tier(
    app_with_yookassa, client, db, seeded_user
):
    """Mocks ЮKassa ``payment.succeeded`` with metadata.purpose=subscription_charge."""
    user, account = seeded_user
    account.autorefill_pm_id = "pmid-saved-1"
    db.add(account)
    await db.commit()

    body = {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {
            "id": "pay_sub_webhook_1",
            "status": "succeeded",
            "amount": {"value": "290.00", "currency": "RUB"},
            "metadata": {
                "account_id": str(account.id),
                "purpose": "subscription_charge",
                "tier": "pro",
            },
            "payment_method": {"id": "pmid-saved-1", "type": "bank_card"},
        },
    }
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "Content-HMAC": _hmac_header(raw)}

    r = await client.post(
        "/v1/billing/yookassa/webhook",
        content=raw,
        headers=headers,
    )
    assert r.status_code == 200, r.text

    await db.refresh(account)
    assert account.subscription_tier == "pro"
    assert account.subscription_active_until is not None
    # ±60s tolerance for clock drift between webhook handler & test assertion.
    expected = datetime.now(UTC) + timedelta(days=SUBSCRIPTION_PERIOD_DAYS)
    db_until = account.subscription_active_until
    if db_until.tzinfo is None:
        db_until = db_until.replace(tzinfo=UTC)
    assert abs((db_until - expected).total_seconds()) < 60

    # Period stamp encodes today; the actual ref_id depends on the handler's
    # wall-clock "now" which may drift by a second over our assertion time.
    # Look up by tier + account instead of exact ref_id; then check the
    # prefix shape separately. ``build_sub_ref_id`` is exercised by its own
    # unit test (test_anonymize_billing covers similar ref_id patterns).
    _ = build_sub_ref_id  # imported for documentation; not asserted here.
    sub_tx = (
        await db.execute(
            select(Transaction).where(
                Transaction.account_id == account.id,
                Transaction.type == TransactionKind.SUBSCRIPTION,
            )
        )
    ).scalar_one()
    assert sub_tx.amount_kopecks == TIER_PRICE_KOPECKS["pro"]
    assert sub_tx.meta is not None
    assert sub_tx.meta.get("tier") == "pro"
    # And the ref_id starts with the expected prefix.
    assert sub_tx.ref_id is not None
    assert sub_tx.ref_id.startswith(f"sub:pro:{account.id}:")
