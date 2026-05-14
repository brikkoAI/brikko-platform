"""E2E payment flow — happy path.

Covers the full story for the first paying Brikko customer:

    signup → verify-email → login → create API-key
        → ЮKassa webhook (signed) → balance credited
        → POST /v1/chat/completions → balance debited
        → usage_event row written

Steps 1-7 of ``test_e2e_payment_flow.md``. Steps 8-13 (НПД-чек, акт PDF
attribution, refund-then-402) are covered by separate single-purpose tests
or the manual regression checklist (real ЮKassa sandbox / real ФНС API).

Strategy notes:

* In-memory SQLite + fakeredis + Stub OpenAI provider (parent ``conftest.py``).
* ЮKassa webhook signature is computed locally over the raw body using a
  fixed ``WEBHOOK_SECRET`` — this is exactly what the production handler
  expects and what the merchant configures in ЛК ЮKassa.
* Receipt issuance is intentionally NOT exercised here: ``app.state
  .yookassa_receipts_http`` is unset, so the credit path logs a warning
  and skips. Real receipt flow needs respx + the ЮKassa-Самозанятые
  contract, which is the spawn of a separate test (TC-8).
* CSRF + cookies: we mirror the behaviour the SPA uses — login sets
  cookies, GET /v1/auth/csrf seeds the double-submit token, all mutating
  requests echo it via ``X-CSRF-Token``.

Run:
    cd apps/gateway && python -m pytest tests/test_e2e_payment_flow.py -q -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth import rate_limit as rl
from voltari_gateway.auth.csrf import CSRF_HEADER
from voltari_gateway.auth.email_verification import generate_verification_token
from voltari_gateway.billing.yookassa import YooKassaClient, YooKassaConfig
from voltari_gateway.db.models import (
    Account,
    ApiKey,
    ApiKeyStatus,
    ProcessedWebhook,
    Tariff,
    Transaction,
    TransactionKind,
    UsageEvent,
    User,
    WelcomeCreditsLog,
)
from voltari_gateway.email import client as email_client

WEBHOOK_SECRET = "e2e-webhook-secret-32-bytes-AAAAAAAAAAAAAAAAAA"

# A password that satisfies the strong-password validator (>= 12 chars, mixed).
TEST_PASSWORD = "correct horse battery staple"

# Welcome bonus per BRIEF.md §7 = 200 ₽ = 20_000 kop.
WELCOME_KOP = 20_000

# Top-up amount we drive through ЮKassa: 1500 ₽ = 150_000 kop.
# Kept >1000 ₽ so the same fixture is reusable for АКТ tests later
# (АКТ has a 1000₽ minimum threshold).
TOPUP_RUB = 1500
TOPUP_KOP = TOPUP_RUB * 100


# ---------------------------------------------------------------------------
# Fixtures (module-local — don't pollute global conftest)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _e2e_env(monkeypatch: Any) -> None:
    """Env tuned for the httpx ASGITransport (http://test).

    Mirrors ``tests/auth/conftest.py``:

    * ``COOKIE_SECURE=false`` — without this, Set-Cookie ships with
      ``Secure`` and httpx-on-http silently drops it. Login looks fine
      on the wire but the next request has no auth cookie.
    * ``COOKIE_DOMAIN=""`` — browser-default attaches cookies to the
      request host. A pinned ``.brikko.ru`` would never match ``test``.
    * ``EMAIL_BACKEND=console`` — disables the SMTP path entirely; we
      patch ``send_email`` separately to capture the body.
    * ``BASE_URL_FRONTEND=http://test`` — verify-email link that the
      template renders points back at the test client (no production
      URL leakage in fake outbound mail).

    Settings is an lru_cached singleton; clear before AND after so the
    overrides don't leak into the next test.
    """
    from voltari_gateway import config as cfg

    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("COOKIE_DOMAIN", "")
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    monkeypatch.setenv("BASE_URL_FRONTEND", "http://test")
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_rate_limits() -> None:
    """In-memory rate limiter is process-global. Wipe before/after each test.

    Without this, a previous test's signup attempts could push us into
    the 5/min ceiling and the E2E flow would 429 on the first call.
    """
    rl.reload_from_settings()
    yield
    rl.reload_from_settings()


@pytest.fixture
def yookassa_client() -> YooKassaClient:
    """Stub ЮKassa client — only ``config.webhook_secret`` is consulted by
    the webhook handler. We never make outbound HTTP calls in this test.
    """
    return YooKassaClient(
        YooKassaConfig(
            shop_id="e2e-shop",
            secret_key="e2e-secret",
            webhook_secret=WEBHOOK_SECRET,
            return_url_template="https://test/return?account={account_id}",
        )
    )


@pytest.fixture(autouse=True)
def _wire_yookassa(app: Any, yookassa_client: YooKassaClient) -> None:
    """Hook the stub client onto app.state — webhook handler reads it via
    ``get_yookassa`` dependency.
    """
    app.state.yookassa = yookassa_client


@pytest.fixture(autouse=True)
def _capture_emails(monkeypatch: Any) -> list[dict[str, str]]:
    """Replace the SMTP send with an in-memory list collector.

    The signup endpoint sends a verify email; we don't actually want to
    deliver it (no SMTP server in tests). Capturing also lets us assert
    the email shipped with the right subject.
    """
    captured: list[dict[str, str]] = []

    async def _fake_send(to: str, subject: str, body: str) -> None:
        captured.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(email_client, "send_email", _fake_send)
    # api/auth.py imported send_email at module load — patch it there too
    # so the rebound name resolves to our fake.
    from voltari_gateway.api import auth as auth_api

    monkeypatch.setattr(auth_api, "send_email", _fake_send)
    return captured


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign_webhook(body: bytes, secret: str = WEBHOOK_SECRET, algo: str = "sha256") -> str:
    """Build a Content-HMAC header value the handler will accept."""
    digestmod = {"sha1": hashlib.sha1, "sha256": hashlib.sha256}[algo]
    digest = hmac.new(secret.encode("utf-8"), body, digestmod).hexdigest()
    return f"{algo}={digest}"


def _payment_succeeded_payload(
    *,
    payment_id: str,
    account_id: uuid.UUID,
    amount_rub: float,
    receipt_email: str | None = None,
) -> bytes:
    """Build a v3 ЮKassa ``payment.succeeded`` envelope as raw bytes.

    ``metadata.account_id`` is the routing key the webhook handler uses
    to attribute the credit. Without it the handler 500s into DLQ.
    """
    body: dict[str, Any] = {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
            "metadata": {"account_id": str(account_id)},
            "payment_method": {"id": "pm_e2e_test"},
        },
    }
    if receipt_email:
        body["object"]["receipt"] = {
            "customer": {"email": receipt_email},
        }
    return json.dumps(body).encode("utf-8")


async def _csrf_headers(client: AsyncClient) -> dict[str, str]:
    """Mint a fresh CSRF double-submit token + header.

    httpx auto-stores ``Set-Cookie`` from the response, so we only return
    the header part. Call once per logical user-action group.
    """
    r = await client.get("/v1/auth/csrf")
    assert r.status_code == 200, r.text
    return {CSRF_HEADER: r.json()["csrf_token"]}


# ---------------------------------------------------------------------------
# THE happy-path test — one method covers TC-1 → TC-7.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_first_paying_customer(
    client: AsyncClient,
    db: AsyncSession,
    session_factory: Any,
    redis_client: Any,
    _capture_emails: list[dict[str, str]],
) -> None:
    """End-to-end: signup → verify → login → key → topup webhook → chat.

    We assert at every step that the DB state is what we expect AND that
    the wire response is what the SPA / SDK relies on. Failures here
    block the public launch.
    """
    # ``test.local`` is rejected by pydantic's EmailStr validator (special-
    # use TLD). Use ``example.com`` like the rest of the auth integration
    # tests.
    email = f"e2e-{uuid.uuid4().hex[:10]}@example.com"

    # -----------------------------------------------------------------
    # TC-1. Signup creates user + account, sends verification email
    # -----------------------------------------------------------------
    signup_r = await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": TEST_PASSWORD},
    )
    assert signup_r.status_code == 200, signup_r.text
    signup_body = signup_r.json()
    assert signup_body["email"] == email
    assert signup_body["verification_required"] is True
    user_id = uuid.UUID(signup_body["user_id"])

    # User row exists, NOT yet verified, no welcome credit yet.
    user_row = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert user_row.email_verified is False
    assert user_row.verification_token is not None
    account_row = (
        await db.execute(select(Account).where(Account.owner_id == user_id))
    ).scalar_one()
    assert account_row.balance_kopecks == 0
    assert account_row.tariff == Tariff.PAYG

    # Verification email captured.
    assert any(e["to"] == email and "verify" in e["body"].lower() for e in _capture_emails), (
        "verification email never went out"
    )

    # -----------------------------------------------------------------
    # TC-2. Verify-email grants 200 ₽ welcome credit
    # -----------------------------------------------------------------
    # Re-mint the token plaintext locally (deterministic given JWT_SECRET).
    # Production: token comes from the email link. Tests: shortcut the
    # email roundtrip.
    verify_token = generate_verification_token(user_id, email)
    verify_r = await client.post("/v1/auth/verify-email", json={"token": verify_token})
    assert verify_r.status_code == 200, verify_r.text
    assert verify_r.json() == {"verified": True, "welcome_credit_kop": WELCOME_KOP}

    # Account is now welcome-credited; user is verified.
    await db.refresh(account_row)
    await db.refresh(user_row)
    assert user_row.email_verified is True
    assert user_row.verification_token is None  # token consumed
    assert account_row.balance_kopecks == WELCOME_KOP
    welcome_log = (await db.execute(select(WelcomeCreditsLog))).scalar_one_or_none()
    assert welcome_log is not None
    welcome_tx = (
        await db.execute(
            select(Transaction).where(
                Transaction.account_id == account_row.id,
                Transaction.type == TransactionKind.TOPUP,
            )
        )
    ).scalar_one()
    assert welcome_tx.amount_kopecks == WELCOME_KOP
    assert (welcome_tx.ref_id or "").startswith("welcome:")

    account_id = account_row.id

    # -----------------------------------------------------------------
    # TC-3. Login mints session cookies
    # -----------------------------------------------------------------
    login_r = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )
    assert login_r.status_code == 200, login_r.text
    login_body = login_r.json()
    assert login_body["status"] == "authenticated"
    assert login_body["user_id"] == str(user_id)
    assert login_body["account_id"] == str(account_id)
    assert "csrf_token" in login_body

    # httpx stored the cookies — no need to re-attach manually.
    assert client.cookies.get("vlt_access") is not None
    assert client.cookies.get("vlt_refresh") is not None

    # -----------------------------------------------------------------
    # TC-4. Create API key — sk-brk- prefix, plaintext returned ONCE
    # -----------------------------------------------------------------
    key_r = await client.post(
        "/v1/keys",
        json={"name": "e2e-default", "scope": "write"},
        headers=await _csrf_headers(client),
    )
    assert key_r.status_code == 201, key_r.text
    key_body = key_r.json()
    full_key = key_body["full_key"]
    assert full_key.startswith("sk-brk-"), f"key prefix not Brikko-style: {full_key[:14]}"
    assert key_body["prefix"] == full_key[:14]
    assert len(full_key) > 14  # body has more than just the prefix
    assert key_body["scope"] == "write"

    # DB row stores hash + prefix, NOT plaintext.
    api_key_row = (
        await db.execute(select(ApiKey).where(ApiKey.id == uuid.UUID(key_body["id"])))
    ).scalar_one()
    assert api_key_row.status == ApiKeyStatus.ACTIVE
    assert api_key_row.key_prefix == full_key[:14]
    assert api_key_row.key_hash != full_key  # argon2 hash, not plaintext

    # GET /v1/keys NEVER returns full_key (only listed by prefix).
    list_r = await client.get("/v1/keys")
    assert list_r.status_code == 200
    items = list_r.json()
    assert len(items) == 1
    assert "full_key" not in items[0]
    assert items[0]["prefix"] == full_key[:14]

    # -----------------------------------------------------------------
    # TC-5. ЮKassa webhook → balance credited (signed body)
    # -----------------------------------------------------------------
    payment_id = f"pay-e2e-{uuid.uuid4().hex[:10]}"
    payload = _payment_succeeded_payload(
        payment_id=payment_id,
        account_id=account_id,
        amount_rub=float(TOPUP_RUB),
        receipt_email=email,
    )
    webhook_r = await client.post(
        "/v1/billing/yookassa/webhook",
        content=payload,
        headers={
            "Content-HMAC": _sign_webhook(payload),
            "Content-Type": "application/json",
        },
    )
    assert webhook_r.status_code == 200, webhook_r.text
    assert webhook_r.json()["status"] == "ok"

    # Balance after = welcome + topup.
    await db.refresh(account_row)
    expected_after_topup = WELCOME_KOP + TOPUP_KOP  # 20_000 + 150_000 = 170_000
    assert account_row.balance_kopecks == expected_after_topup

    # Topup transaction row.
    topup_tx = (
        await db.execute(select(Transaction).where(Transaction.ref_id == payment_id))
    ).scalar_one()
    assert topup_tx.type == TransactionKind.TOPUP
    assert topup_tx.amount_kopecks == TOPUP_KOP
    assert (topup_tx.meta or {}).get("source") == "yookassa"

    # processed_webhooks ledger.
    pw_row = await db.get(ProcessedWebhook, payment_id)
    assert pw_row is not None
    assert pw_row.status == "processed"
    assert pw_row.error_message is None

    # -----------------------------------------------------------------
    # TC-5b. Webhook idempotency — replay returns 200 / replay=True,
    #         balance unchanged.
    # -----------------------------------------------------------------
    replay_r = await client.post(
        "/v1/billing/yookassa/webhook",
        content=payload,
        headers={
            "Content-HMAC": _sign_webhook(payload),
            "Content-Type": "application/json",
        },
    )
    assert replay_r.status_code == 200
    assert replay_r.json().get("replay") is True

    await db.refresh(account_row)
    assert account_row.balance_kopecks == expected_after_topup, "replay double-credited"
    duplicate_count = len(
        (await db.execute(select(Transaction).where(Transaction.ref_id == payment_id)))
        .scalars()
        .all()
    )
    assert duplicate_count == 1, "duplicate transaction row written"

    # -----------------------------------------------------------------
    # TC-6. POST /v1/chat/completions with the new key — OpenAI envelope
    # -----------------------------------------------------------------
    chat_r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "ping"}],
        },
        headers={"Authorization": f"Bearer {full_key}"},
    )
    assert chat_r.status_code == 200, chat_r.text
    chat_body = chat_r.json()
    # OpenAI-compatible envelope.
    assert chat_body["object"] == "chat.completion"
    assert chat_body["choices"][0]["message"]["role"] == "assistant"
    assert "content" in chat_body["choices"][0]["message"]
    # StubProvider returns canned 10/20 token usage.
    assert chat_body["usage"]["prompt_tokens"] == 10
    assert chat_body["usage"]["completion_tokens"] == 20
    # Brikko exposes the chosen provider in x_gateway for routing observability.
    assert chat_body["x_gateway"]["provider"] == "openai"

    # -----------------------------------------------------------------
    # TC-7. Balance debited + usage_event row written
    # -----------------------------------------------------------------
    # gpt-5.4-mini @ 10/20 tokens × markup 1.15 = round(0.06 + 0.72, 1) ≈ 1 kop.
    # The exact number is asserted by the catalog test; here we just
    # require it to be positive AND less than the topup.
    await db.refresh(account_row)
    assert account_row.balance_kopecks < expected_after_topup, "no debit happened"
    assert account_row.balance_kopecks > 0, "overshot debit / went negative"
    debit_amount = expected_after_topup - account_row.balance_kopecks
    assert 0 < debit_amount < TOPUP_KOP, f"debit out of range: {debit_amount} kop"

    # Exactly one usage_event row, attributed to the key + the account.
    usage_rows = (
        (await db.execute(select(UsageEvent).where(UsageEvent.account_id == account_id)))
        .scalars()
        .all()
    )
    assert len(usage_rows) == 1
    ev = usage_rows[0]
    assert ev.api_key_id == api_key_row.id
    assert ev.input_tokens == 10
    assert ev.output_tokens == 20
    assert ev.cost_kopecks == debit_amount, "cost on usage_event != debit on account"

    # Charge transaction row exists (negative amount).
    charge_rows = (
        (
            await db.execute(
                select(Transaction).where(
                    Transaction.account_id == account_id,
                    Transaction.type == TransactionKind.CHARGE,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(charge_rows) == 1
    assert charge_rows[0].amount_kopecks == -debit_amount

    # -----------------------------------------------------------------
    # GET /v1/billing/balance returns the same number — the API the
    # dashboard polls must agree with the DB state we just asserted.
    # -----------------------------------------------------------------
    balance_r = await client.get(
        "/v1/billing/balance",
        headers={"Authorization": f"Bearer {full_key}"},
    )
    assert balance_r.status_code == 200
    assert balance_r.json()["balance_kopecks"] == account_row.balance_kopecks


# ---------------------------------------------------------------------------
# TC-13: Forged webhook (bad signature) is rejected with no DB writes.
#
# We split this off the main happy-path test so a regression in signature
# checking trips a single, focused failure rather than torpedoing the
# whole flow assertion at step 5.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_forged_webhook_rejected(
    client: AsyncClient,
    api_key_fixture: Any,
    db: AsyncSession,
) -> None:
    """A webhook with a wrong HMAC must 401 and leave no traces.

    This is the perimeter — without it any anonymous attacker could
    forge credits to an account they know the UUID of (which is
    derivable from the dashboard URL).
    """
    pre_balance = api_key_fixture.account.balance_kopecks
    payload = _payment_succeeded_payload(
        payment_id=f"forged-{uuid.uuid4().hex[:8]}",
        account_id=api_key_fixture.account.id,
        amount_rub=999_999.00,
    )

    r = await client.post(
        "/v1/billing/yookassa/webhook",
        content=payload,
        headers={
            "Content-HMAC": "sha256=DEADBEEFDEADBEEFDEADBEEFDEADBEEF",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"

    # Balance unchanged — no credit landed.
    await db.refresh(api_key_fixture.account)
    assert api_key_fixture.account.balance_kopecks == pre_balance

    # No DLQ row either — bad-sig requests aren't even enqueued (they're
    # dropped at the perimeter before parse_webhook runs).
    dlq_rows = (await db.execute(select(ProcessedWebhook))).scalars().all()
    assert dlq_rows == []
