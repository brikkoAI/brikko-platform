"""Tests for the card-link verification flow.

Covers Pay-per-use → subscription pivot (CEO 2026-05-15, BRIEF_v2_pivot.md):

  1. POST /v1/billing/link-card creates a 1 ₽ ЮKassa payment with
     ``save_payment_method=true`` and ``metadata.purpose=card_link_verification``.
  2. The webhook handler refunds the 1 ₽, saves ``payment_method.id``
     to ``account.autorefill_pm_id``, and credits 100 ₽ welcome bonus.
  3. Duplicate POSTs to /link-card are rejected with 400 ``card_already_linked``
     when ``autorefill_pm_id`` is already set.
  4. Failed verification (``payment.canceled``) does NOT credit welcome.
  5. Re-linking after un-linking does NOT re-grant the welcome bonus
     (the welcome ref_id is account-scoped, not payment-scoped).

We mock the outbound ЮKassa calls via ``respx`` — no real network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
import pytest_asyncio
import respx
from sqlalchemy import select

from voltari_gateway import config as cfg
from voltari_gateway.auth import rate_limit as rl
from voltari_gateway.auth.password import hash_password
from voltari_gateway.billing.card_link import (
    CARD_LINK_PURPOSE,
    WELCOME_CARD_LINK_BONUS_KOPECKS,
    WELCOME_CARD_LINK_REF_ID,
)
from voltari_gateway.billing.yookassa import (
    YooKassaClient,
    YooKassaConfig,
)
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    Transaction,
    TransactionKind,
    User,
)

_PASSWORD = "correct horse battery staple"


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def cookie_friendly_env(monkeypatch):
    """Cookie/CORS env so httpx ASGI transport can run cookie-auth flows.

    Mirrors the autouse fixture in tests/auth/conftest.py (which only
    applies to that subdirectory). We need the same env here so login
    sets cookies that the jar will actually keep.
    """
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
    """Attach a real YooKassaClient (with respx-mocked transport) to the app."""
    cfg = _yookassa_config()
    app.state.yookassa = YooKassaClient(cfg)
    yield app
    await app.state.yookassa.aclose()


@pytest_asyncio.fixture
async def seeded_user(db):
    """Create a user + PAYG account with no card linked."""
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name="Card-link test",
        balance_kopecks=10_000,  # signup welcome already on
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


async def _login(client, user) -> dict[str, str]:
    """Log in via /v1/auth/login → cookies stick to the httpx jar."""
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text
    # Fetch a CSRF token for mutating calls.
    csrf_r = await client.get("/v1/auth/csrf")
    assert csrf_r.status_code == 200, csrf_r.text
    from voltari_gateway.auth.csrf import CSRF_HEADER

    return {CSRF_HEADER: csrf_r.json()["csrf_token"]}


# --- 1) POST /v1/billing/link-card creates 1 ₽ ЮKassa payment ---------------


@pytest.mark.asyncio
async def test_link_card_creates_yookassa_1rub_payment(
    app_with_yookassa, client, db, seeded_user
):
    user, account = seeded_user
    csrf = await _login(client, user)

    yk_payload = {
        "id": "pay_card_link_1",
        "status": "pending",
        "amount": {"value": "1.00", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "confirmation_url": "https://yk/confirm?id=pay_card_link_1",
        },
    }
    with respx.mock(base_url="https://test-yookassa/v3") as mock:
        route = mock.post("/payments").respond(200, json=yk_payload)
        r = await client.post(
            "/v1/billing/link-card",
            json={},
            headers=csrf,
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["payment_id"] == "pay_card_link_1"
    assert body["confirmation_url"].startswith("https://yk/")

    # Verify ЮKassa request body — 1 ₽, save_payment_method, purpose metadata.
    sent = json.loads(route.calls[0].request.content)
    assert sent["amount"]["value"] == "1.00"
    assert sent["save_payment_method"] is True
    assert sent["metadata"]["purpose"] == CARD_LINK_PURPOSE
    assert sent["metadata"]["account_id"] == str(account.id)


# --- 2) Webhook refunds 1 ₽ AND credits 100 ₽ welcome -----------------------


def _make_card_link_webhook_body(account_id: uuid.UUID, payment_id: str) -> bytes:
    body = {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": "1.00", "currency": "RUB"},
            "metadata": {
                "account_id": str(account_id),
                "purpose": CARD_LINK_PURPOSE,
            },
            "payment_method": {
                "id": "pmid-saved-abc",
                "type": "bank_card",
                "saved": True,
            },
        },
    }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


@pytest.mark.asyncio
async def test_webhook_refunds_1rub_and_credits_100rub_welcome(
    app_with_yookassa, client, db, seeded_user
):
    user, account = seeded_user
    starting_balance = account.balance_kopecks

    raw = _make_card_link_webhook_body(account.id, "pay_card_link_2")
    headers = {"Content-Type": "application/json", "Content-HMAC": _hmac_header(raw)}

    with respx.mock(base_url="https://test-yookassa/v3") as mock:
        refund_route = mock.post("/refunds").respond(
            200,
            json={
                "id": "ref_xyz",
                "status": "succeeded",
                "amount": {"value": "1.00", "currency": "RUB"},
                "payment_id": "pay_card_link_2",
            },
        )

        r = await client.post(
            "/v1/billing/yookassa/webhook",
            content=raw,
            headers=headers,
        )

    assert r.status_code == 200, r.text

    # Refund was called with the right body.
    assert refund_route.called
    refund_body = json.loads(refund_route.calls[0].request.content)
    assert refund_body["payment_id"] == "pay_card_link_2"
    assert refund_body["amount"]["value"] == "1.00"

    # Balance grew by exactly the welcome bonus (NOT by 1 ₽ on top).
    await db.refresh(account)
    assert account.balance_kopecks == starting_balance + WELCOME_CARD_LINK_BONUS_KOPECKS

    # Welcome transaction exists with the stable ref_id.
    welcome_tx = (
        await db.execute(
            select(Transaction).where(
                Transaction.account_id == account.id,
                Transaction.ref_id == WELCOME_CARD_LINK_REF_ID.format(account_id=account.id),
            )
        )
    ).scalar_one()
    assert welcome_tx.type == TransactionKind.TOPUP
    assert welcome_tx.amount_kopecks == WELCOME_CARD_LINK_BONUS_KOPECKS
    assert welcome_tx.meta is not None
    assert welcome_tx.meta.get("kind") == "welcome_card_link"


# --- 3) Webhook saves autorefill_pm_id --------------------------------------


@pytest.mark.asyncio
async def test_webhook_saves_autorefill_pm_id(
    app_with_yookassa, client, db, seeded_user
):
    user, account = seeded_user
    assert account.autorefill_pm_id is None  # baseline

    raw = _make_card_link_webhook_body(account.id, "pay_card_link_3")
    headers = {"Content-Type": "application/json", "Content-HMAC": _hmac_header(raw)}

    with respx.mock(base_url="https://test-yookassa/v3") as mock:
        mock.post("/refunds").respond(
            200,
            json={
                "id": "ref_3",
                "status": "succeeded",
                "amount": {"value": "1.00", "currency": "RUB"},
            },
        )
        r = await client.post(
            "/v1/billing/yookassa/webhook",
            content=raw,
            headers=headers,
        )

    assert r.status_code == 200, r.text
    await db.refresh(account)
    assert account.autorefill_pm_id == "pmid-saved-abc"


# --- 4) Duplicate POST /link-card → 400 ------------------------------------


@pytest.mark.asyncio
async def test_duplicate_card_link_returns_400(
    app_with_yookassa, client, db, seeded_user
):
    user, account = seeded_user
    # Pre-set a saved card.
    account.autorefill_pm_id = "pmid-already-here"
    db.add(account)
    await db.commit()

    csrf = await _login(client, user)
    r = await client.post(
        "/v1/billing/link-card",
        json={},
        headers=csrf,
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "card_already_linked"


# --- 5) Failed verification (payment.canceled) does NOT credit welcome ------


@pytest.mark.asyncio
async def test_failed_verification_does_not_credit_welcome(
    app_with_yookassa, client, db, seeded_user
):
    user, account = seeded_user
    starting_balance = account.balance_kopecks

    body = {
        "type": "notification",
        "event": "payment.canceled",
        "object": {
            "id": "pay_card_link_failed",
            "status": "canceled",
            "amount": {"value": "1.00", "currency": "RUB"},
            "metadata": {
                "account_id": str(account.id),
                "purpose": CARD_LINK_PURPOSE,
            },
        },
    }
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "Content-HMAC": _hmac_header(raw)}

    r = await client.post(
        "/v1/billing/yookassa/webhook",
        content=raw,
        headers=headers,
    )
    assert r.status_code == 200  # no-op, but acknowledged

    await db.refresh(account)
    assert account.balance_kopecks == starting_balance  # untouched
    assert account.autorefill_pm_id is None
    # No welcome transaction was written.
    welcome_tx = (
        await db.execute(
            select(Transaction).where(
                Transaction.account_id == account.id,
                Transaction.ref_id == WELCOME_CARD_LINK_REF_ID.format(account_id=account.id),
            )
        )
    ).scalar_one_or_none()
    assert welcome_tx is None


# --- 6) Re-linking after removing card does NOT re-grant welcome -----------


@pytest.mark.asyncio
async def test_relinking_does_not_regrant_welcome(
    app_with_yookassa, client, db, seeded_user
):
    """User links a card → claims welcome → un-links → tries to re-link.

    The second /link-card POST should be rejected with
    ``welcome_card_link_already_granted`` even though autorefill_pm_id is NULL.
    """
    user, account = seeded_user

    # Simulate: previous card-link cycle already credited the welcome.
    account.balance_kopecks += WELCOME_CARD_LINK_BONUS_KOPECKS
    db.add(
        Transaction(
            account_id=account.id,
            type=TransactionKind.TOPUP,
            amount_kopecks=WELCOME_CARD_LINK_BONUS_KOPECKS,
            ref_id=WELCOME_CARD_LINK_REF_ID.format(account_id=account.id),
            meta={"kind": "welcome_card_link"},
        )
    )
    # User then removed the card (autorefill_pm_id back to NULL).
    account.autorefill_pm_id = None
    db.add(account)
    await db.commit()

    csrf = await _login(client, user)
    r = await client.post(
        "/v1/billing/link-card",
        json={},
        headers=csrf,
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "welcome_card_link_already_granted"
