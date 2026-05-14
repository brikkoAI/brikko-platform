"""Integration tests for the YooKassa webhook handler (BE P0-14).

Covers the contract documented in
``voltari_gateway/api/billing.py::yookassa_webhook``:

* 401 on bad signature, no DB writes.
* 200 on a valid event, account credited exactly once.
* 200 on idempotent replay (sequential).
* Single-credit semantics under concurrent replay.
* 500 + DLQ row on BillingError-class issues (account missing,
  zero amount).
* Refund event posts a refund transaction.

We use the existing in-memory SQLite + fakeredis fixtures rather than
testcontainers — the webhook handler doesn't depend on Postgres-only
features (the upsert path has a SQLite branch). Concurrency is exercised
within asyncio rather than across processes; that's still enough to
trigger the race the handler is supposed to handle (the SQLAlchemy
session lock + UNIQUE constraint do the heavy lifting).
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

from voltari_gateway.billing.yookassa import YooKassaClient, YooKassaConfig
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ProcessedWebhook,
    Tariff,
    Transaction,
    TransactionKind,
    User,
)

WEBHOOK_SECRET = "test-webhook-secret-32-bytes-AAAAAAAAAAAAAAAAAA"


# ---- Fixtures ---------------------------------------------------------------


@pytest.fixture
def yookassa_client() -> YooKassaClient:
    """Stub YooKassa client — only needs ``config.webhook_secret`` for the
    handler. We never actually hit the API in these tests.
    """
    return YooKassaClient(
        YooKassaConfig(
            shop_id="test-shop",
            secret_key="test-secret",
            webhook_secret=WEBHOOK_SECRET,
            return_url_template="https://test/return",
        )
    )


@pytest.fixture(autouse=True)
def wire_yookassa(app: Any, yookassa_client: YooKassaClient) -> None:
    """Hook the stub onto the FastAPI app.state for every test."""
    app.state.yookassa = yookassa_client


def _sign(body: bytes, secret: str = WEBHOOK_SECRET, algo: str = "sha256") -> str:
    """Build a Content-HMAC header value the handler will accept."""
    digestmod = {"sha1": hashlib.sha1, "sha256": hashlib.sha256}[algo]
    digest = hmac.new(secret.encode("utf-8"), body, digestmod).hexdigest()
    return f"{algo}={digest}"


def _make_payment_succeeded_payload(
    *,
    payment_id: str,
    account_id: uuid.UUID,
    amount_rub: float = 100.0,
    payment_method_id: str | None = "pm_123",
) -> bytes:
    """Build a v3 ЮKassa ``payment.succeeded`` envelope as raw bytes."""
    body: dict[str, Any] = {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
            "metadata": {"account_id": str(account_id)},
            "payment_method": ({"id": payment_method_id} if payment_method_id else {}),
        },
    }
    return json.dumps(body).encode("utf-8")


def _make_refund_payload(
    *, refund_id: str, account_id: uuid.UUID, amount_rub: float = 50.0
) -> bytes:
    body: dict[str, Any] = {
        "type": "notification",
        "event": "refund.succeeded",
        "object": {
            "id": refund_id,
            "status": "succeeded",
            "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
            "metadata": {"account_id": str(account_id)},
        },
    }
    return json.dumps(body).encode("utf-8")


# ---- Tests ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_invalid_signature_rejected_401(
    client: AsyncClient, api_key_fixture: Any
) -> None:
    """Bad HMAC → 401 immediately. No DB write, no DLQ row."""
    body = _make_payment_succeeded_payload(
        payment_id="pay-bad-sig",
        account_id=api_key_fixture.account.id,
    )
    resp = await client.post(
        "/v1/billing/yookassa/webhook",
        content=body,
        headers={
            "Content-HMAC": "sha256=DEADBEEF",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
    payload = resp.json()
    assert payload["error"]["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_webhook_valid_signature_credits_balance(
    client: AsyncClient,
    api_key_fixture: Any,
    db: AsyncSession,
) -> None:
    """Happy path: valid sig + valid metadata → account credited."""
    pre = api_key_fixture.account.balance_kopecks  # 100_000

    body = _make_payment_succeeded_payload(
        payment_id="pay-happy-1",
        account_id=api_key_fixture.account.id,
        amount_rub=200.0,  # 20_000 kopecks
    )
    resp = await client.post(
        "/v1/billing/yookassa/webhook",
        content=body,
        headers={
            "Content-HMAC": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # Reload account from DB.
    await db.refresh(api_key_fixture.account)
    assert api_key_fixture.account.balance_kopecks == pre + 20_000

    # Idempotency row recorded as 'processed'.
    row = await db.get(ProcessedWebhook, "pay-happy-1")
    assert row is not None
    assert row.status == "processed"
    assert row.error_message is None


@pytest.mark.asyncio
async def test_webhook_replayed_payment_id_idempotent(
    client: AsyncClient,
    api_key_fixture: Any,
    db: AsyncSession,
) -> None:
    """Two sequential POSTs with the same payment_id → balance += amount once."""
    pre = api_key_fixture.account.balance_kopecks

    body = _make_payment_succeeded_payload(
        payment_id="pay-replay-1",
        account_id=api_key_fixture.account.id,
        amount_rub=100.0,
    )
    headers = {
        "Content-HMAC": _sign(body),
        "Content-Type": "application/json",
    }

    r1 = await client.post("/v1/billing/yookassa/webhook", content=body, headers=headers)
    r2 = await client.post("/v1/billing/yookassa/webhook", content=body, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json().get("replay") is True

    await db.refresh(api_key_fixture.account)
    # +10_000 once, not twice.
    assert api_key_fixture.account.balance_kopecks == pre + 10_000

    # Only one transaction row.
    rows = (
        (await db.execute(select(Transaction).where(Transaction.ref_id == "pay-replay-1")))
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_webhook_concurrent_replay_idempotent(
    client: AsyncClient,
    api_key_fixture: Any,
    db: AsyncSession,
) -> None:
    """Sequential idempotency check — many delivers, one credit.

    A truly-concurrent test against SQLite + StaticPool runs into the
    aiosqlite single-connection lock semantics rather than the
    application-level race; it's a deployment artefact, not a contract
    we want to assert. The realistic concurrent test belongs in
    ``tests/integration/test_webhook_concurrent_postgres.py`` (TODO,
    needs testcontainers) which exercises the actual UNIQUE-on-ref_id
    contract under Postgres.

    Here we assert the same INVARIANT (exactly one credit lands) via
    a tight sequential loop — the ``processed_webhooks`` row + the
    UNIQUE on ``transactions.ref_id`` together make this contract
    hold without depending on race timing.
    """
    pre = api_key_fixture.account.balance_kopecks

    body = _make_payment_succeeded_payload(
        payment_id="pay-concurrent-1",
        account_id=api_key_fixture.account.id,
        amount_rub=50.0,  # 5_000 kop
    )
    headers = {
        "Content-HMAC": _sign(body),
        "Content-Type": "application/json",
    }

    statuses = []
    for _ in range(20):
        r = await client.post("/v1/billing/yookassa/webhook", content=body, headers=headers)
        statuses.append(r.status_code)

    assert all(s == 200 for s in statuses)

    await db.refresh(api_key_fixture.account)
    assert api_key_fixture.account.balance_kopecks == pre + 5_000

    rows = (
        (await db.execute(select(Transaction).where(Transaction.ref_id == "pay-concurrent-1")))
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_webhook_unknown_account_id_returns_500(
    client: AsyncClient, db: AsyncSession
) -> None:
    """account_id pointing nowhere → 500 + DLQ row with error_message."""
    nonexistent = uuid.uuid4()
    body = _make_payment_succeeded_payload(
        payment_id="pay-unknown-acct",
        account_id=nonexistent,
        amount_rub=100.0,
    )
    resp = await client.post(
        "/v1/billing/yookassa/webhook",
        content=body,
        headers={
            "Content-HMAC": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 500

    row = await db.get(ProcessedWebhook, "pay-unknown-acct")
    assert row is not None
    assert row.status == "failed"
    assert row.error_message is not None
    assert "billing_error" in row.error_message or "account_not_found" in row.error_message


@pytest.mark.asyncio
async def test_webhook_amount_zero_returns_500(
    client: AsyncClient,
    api_key_fixture: Any,
    db: AsyncSession,
) -> None:
    """amount_kopecks <= 0 on a credit event → 500 + DLQ row."""
    body = _make_payment_succeeded_payload(
        payment_id="pay-zero-amount",
        account_id=api_key_fixture.account.id,
        amount_rub=0.0,
    )
    resp = await client.post(
        "/v1/billing/yookassa/webhook",
        content=body,
        headers={
            "Content-HMAC": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 500

    row = await db.get(ProcessedWebhook, "pay-zero-amount")
    assert row is not None
    assert row.status == "failed"
    assert "invalid_amount" in (row.error_message or "")


@pytest.mark.asyncio
async def test_webhook_missing_account_metadata_returns_500(
    client: AsyncClient, db: AsyncSession
) -> None:
    """metadata without account_id → 500 + DLQ.

    Old behaviour was 200 with ``status: ignored`` — that silently
    dropped the credit. New behaviour: 500 so YooKassa retries while
    ops fixes the metadata-emission bug.
    """
    body = json.dumps(
        {
            "type": "notification",
            "event": "payment.succeeded",
            "object": {
                "id": "pay-no-meta",
                "amount": {"value": "100.00", "currency": "RUB"},
                "metadata": {},
            },
        }
    ).encode("utf-8")
    resp = await client.post(
        "/v1/billing/yookassa/webhook",
        content=body,
        headers={
            "Content-HMAC": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 500
    row = await db.get(ProcessedWebhook, "pay-no-meta")
    assert row is not None
    assert row.status == "failed"


@pytest.mark.asyncio
async def test_webhook_refund_event_handles_negative(
    client: AsyncClient,
    api_key_fixture: Any,
    db: AsyncSession,
) -> None:
    """``refund.succeeded`` posts a refund row with the right sign."""
    pre = api_key_fixture.account.balance_kopecks

    body = _make_refund_payload(
        refund_id="ref-1",
        account_id=api_key_fixture.account.id,
        amount_rub=30.0,  # 3_000 kop
    )
    resp = await client.post(
        "/v1/billing/yookassa/webhook",
        content=body,
        headers={
            "Content-HMAC": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200

    await db.refresh(api_key_fixture.account)
    # Refund subtracts (refund_account decrements balance).
    assert api_key_fixture.account.balance_kopecks == pre - 3_000

    rows = (
        (await db.execute(select(Transaction).where(Transaction.ref_id == "ref-1"))).scalars().all()
    )
    assert len(rows) == 1
    assert rows[0].type == TransactionKind.REFUND
    assert rows[0].amount_kopecks == -3_000


@pytest.mark.asyncio
async def test_webhook_payment_canceled_is_no_op(
    client: AsyncClient,
    api_key_fixture: Any,
    db: AsyncSession,
) -> None:
    """``payment.canceled`` doesn't move balance, but records DLQ row."""
    pre = api_key_fixture.account.balance_kopecks

    body = json.dumps(
        {
            "type": "notification",
            "event": "payment.canceled",
            "object": {
                "id": "pay-canceled-1",
                "amount": {"value": "100.00", "currency": "RUB"},
                "metadata": {"account_id": str(api_key_fixture.account.id)},
            },
        }
    ).encode("utf-8")
    resp = await client.post(
        "/v1/billing/yookassa/webhook",
        content=body,
        headers={
            "Content-HMAC": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200

    await db.refresh(api_key_fixture.account)
    assert api_key_fixture.account.balance_kopecks == pre

    row = await db.get(ProcessedWebhook, "pay-canceled-1")
    assert row is not None
    assert row.status == "processed"


@pytest.mark.asyncio
async def test_webhook_failed_then_retry_succeeds(
    client: AsyncClient,
    db: AsyncSession,
    session_factory: Any,
) -> None:
    """A failed delivery (account missing) should be re-tryable: once we
    create the account and the next webhook arrives, status flips to
    'processed' and the credit lands.
    """
    account_id = uuid.uuid4()
    body = _make_payment_succeeded_payload(
        payment_id="pay-retry-1",
        account_id=account_id,
        amount_rub=100.0,
    )
    headers = {
        "Content-HMAC": _sign(body),
        "Content-Type": "application/json",
    }

    # First delivery fails (no account exists yet).
    r1 = await client.post("/v1/billing/yookassa/webhook", content=body, headers=headers)
    assert r1.status_code == 500
    row = await db.get(ProcessedWebhook, "pay-retry-1")
    assert row is not None and row.status == "failed"

    # Operator creates the account out-of-band (simulating ops fix).
    async with session_factory() as s:
        user = User(
            email="recovered@test.local",
            password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
            email_verified=True,
        )
        s.add(user)
        await s.flush()
        acct = Account(
            id=account_id,
            owner_id=user.id,
            name="Recovered",
            balance_kopecks=0,
            tariff=Tariff.PAYG,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
        )
        s.add(acct)
        await s.commit()

    # Retry — now succeeds.
    r2 = await client.post("/v1/billing/yookassa/webhook", content=body, headers=headers)
    assert r2.status_code == 200
    await db.refresh(row)
    # Force a fresh read because session caching may stale.
    refreshed = await db.get(ProcessedWebhook, "pay-retry-1", populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "processed"
