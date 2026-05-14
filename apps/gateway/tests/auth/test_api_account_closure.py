"""Integration tests for account closure (Sprint 7).

Covers the three endpoints (POST /close, POST /close/cancel,
GET /closure-status), the middleware change (closed_at blocks auth,
grace period does not), and both cron loops (closure + PII purge).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.auth.conftest import csrf_headers as _csrf_headers
from voltari_gateway.auth.password import hash_password
from voltari_gateway.billing.account_closure_cron import (
    PII_PURGE_AFTER_DAYS,
    process_account_closures,
    process_account_pii_purge,
)
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    AuditLog,
    Tariff,
    Transaction,
    TransactionKind,
    User,
)
from voltari_gateway.email import client as email_client

_PASSWORD = "correct horse battery staple"


def _capture_emails(monkeypatch) -> list[dict[str, str]]:
    captured: list[dict[str, str]] = []

    async def _fake_send(to: str, subject: str, body: str) -> None:
        captured.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(email_client, "send_email", _fake_send)
    from voltari_gateway.api import account_closure as ac
    from voltari_gateway.billing import account_closure_cron as cron

    monkeypatch.setattr(ac, "send_email", _fake_send)
    monkeypatch.setattr(cron, "send_email", _fake_send)
    return captured


async def _seed_user(
    db,
    *,
    balance_kop: int = 50_000,
    name: str = "Acme",
) -> User:
    user = User(
        email=f"closure-{uuid.uuid4().hex[:10]}@example.com",
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
            tariff=Tariff.PRO,
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


async def _account_for(db, user) -> Account:
    res = await db.execute(select(Account).where(Account.owner_id == user.id))
    return res.scalars().one()


# ---------------------------------------------------------------------------
# 1) POST /close — schedules grace, emails confirmation, audits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_schedules_grace_window_and_emits_audit(client, db, redis_client, monkeypatch):
    captured = _capture_emails(monkeypatch)
    user = await _seed_user(db)
    await _login(client, user)

    headers = await _csrf_headers(client)
    r = await client.post("/v1/account/close", json={"reason": "no longer needed"}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requested"] is True
    assert body["can_cancel"] is True
    assert body["days_remaining"] == 30

    # Confirmation email landed.
    assert any("закрытие" in e["subject"].lower() for e in captured)

    # DB row reflects the request.
    account = await _account_for(db, user)
    await db.refresh(account)
    assert account.closure_requested_at is not None
    assert account.closure_scheduled_at is not None
    assert account.closed_at is None

    # Audit log row is present.
    audit = (
        (await db.execute(select(AuditLog).where(AuditLog.action == "account_closure_requested")))
        .scalars()
        .all()
    )
    assert len(audit) >= 1


# ---------------------------------------------------------------------------
# 2) GET /closure-status — accurate snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closure_status_reflects_state(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)

    # Before close — empty status.
    r = await client.get("/v1/account/closure-status")
    assert r.status_code == 200
    assert r.json()["requested"] is False

    # After close — populated.
    headers = await _csrf_headers(client)
    await client.post("/v1/account/close", json={}, headers=headers)
    r2 = await client.get("/v1/account/closure-status")
    body = r2.json()
    assert body["requested"] is True
    assert body["can_cancel"] is True
    assert body["days_remaining"] == 30


# ---------------------------------------------------------------------------
# 3) POST /close/cancel — works inside grace window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_inside_grace_window_clears_state(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    headers = await _csrf_headers(client)
    await client.post("/v1/account/close", json={}, headers=headers)

    headers2 = await _csrf_headers(client)
    r = await client.post("/v1/account/close/cancel", headers=headers2)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requested"] is False
    assert body["can_cancel"] is False

    account = await _account_for(db, user)
    await db.refresh(account)
    assert account.closure_requested_at is None
    assert account.closure_scheduled_at is None


# ---------------------------------------------------------------------------
# 4) Cron: does NOT close when grace not elapsed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_does_not_close_account_in_grace_window(db, session_factory, redis_client):
    user = await _seed_user(db)
    account = await _account_for(db, user)
    account.closure_requested_at = datetime.now(UTC) - timedelta(days=1)
    account.closure_scheduled_at = datetime.now(UTC) + timedelta(days=29)
    await db.commit()

    closed = await process_account_closures(session_factory, redis=redis_client)
    assert closed == 0

    await db.refresh(account)
    assert account.closed_at is None
    assert account.status == AccountStatus.ACTIVE


# ---------------------------------------------------------------------------
# 5) Cron: closes account when grace elapsed; revokes keys; refunds balance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_closes_after_grace_expired_and_revokes_keys(
    db, session_factory, redis_client, monkeypatch
):
    captured = _capture_emails(monkeypatch)
    user = await _seed_user(db, balance_kop=12_345)
    account = await _account_for(db, user)

    # Seed a key and a hold to make sure both get cleaned up.
    key = ApiKey(
        account_id=account.id,
        name="default",
        key_hash="$argon2id$dummy",
        key_prefix="sk-vlt-AAAAAAAAAA",
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(key)

    account.closure_requested_at = datetime.now(UTC) - timedelta(days=31)
    account.closure_scheduled_at = datetime.now(UTC) - timedelta(seconds=10)
    await db.commit()

    closed = await process_account_closures(session_factory, redis=redis_client)
    assert closed == 1

    await db.refresh(account)
    assert account.status == AccountStatus.CLOSED
    assert account.closed_at is not None
    assert account.balance_kopecks == 0  # refunded

    # Key revoked.
    await db.refresh(key)
    assert key.status == ApiKeyStatus.REVOKED
    assert key.revoked_at is not None

    # Refund Transaction row.
    txs = (
        (
            await db.execute(
                select(Transaction).where(
                    Transaction.account_id == account.id,
                    Transaction.type == TransactionKind.REFUND,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(txs) == 1
    assert txs[0].amount_kopecks == -12_345

    # Audit + email.
    audit = (
        (await db.execute(select(AuditLog).where(AuditLog.action == "account_closed")))
        .scalars()
        .all()
    )
    assert len(audit) == 1
    assert any("закрыт" in e["subject"].lower() for e in captured)


# ---------------------------------------------------------------------------
# 6) Cron is idempotent on re-run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_does_not_double_close_already_closed_account(db, session_factory, redis_client):
    user = await _seed_user(db)
    account = await _account_for(db, user)
    account.closure_requested_at = datetime.now(UTC) - timedelta(days=31)
    account.closure_scheduled_at = datetime.now(UTC) - timedelta(seconds=10)
    await db.commit()

    first = await process_account_closures(session_factory, redis=redis_client)
    second = await process_account_closures(session_factory, redis=redis_client)
    assert first == 1
    assert second == 0


# ---------------------------------------------------------------------------
# 7) API key still works in grace period (middleware does not block)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_works_during_grace_period(client, api_key_fixture, db):
    account = api_key_fixture.account
    account.closure_requested_at = datetime.now(UTC) - timedelta(days=1)
    account.closure_scheduled_at = datetime.now(UTC) + timedelta(days=29)
    await db.commit()

    r = await client.get("/v1/models", headers=api_key_fixture.auth_header)
    # Grace period must not affect Bearer auth — /v1/models is reachable.
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 8) After cron-closed, login fails with 401/403 (account closed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_after_cron_close_is_blocked(client, db, session_factory, redis_client):
    user = await _seed_user(db)
    account = await _account_for(db, user)
    # Set up an expired closure and run the cron — account becomes CLOSED.
    account.closure_requested_at = datetime.now(UTC) - timedelta(days=31)
    account.closure_scheduled_at = datetime.now(UTC) - timedelta(seconds=10)
    await db.commit()
    await process_account_closures(session_factory, redis=redis_client)

    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    # Login refuses because the account is no longer ACTIVE.
    assert r.status_code in (401, 403), r.text


# ---------------------------------------------------------------------------
# 9) Bearer auth on closed account → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bearer_auth_blocked_after_close(client, api_key_fixture, db):
    api_key_fixture.account.closed_at = datetime.now(UTC) - timedelta(seconds=1)
    api_key_fixture.account.status = AccountStatus.CLOSED
    await db.commit()

    r = await client.get("/v1/models", headers=api_key_fixture.auth_header)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 10) Cross-user prevention on /close — second user can't observe state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_only_affects_caller(client, db, redis_client):
    a = await _seed_user(db, name="Alice")
    b = await _seed_user(db, name="Bob")

    await _login(client, a)
    headers = await _csrf_headers(client)
    await client.post("/v1/account/close", json={}, headers=headers)

    # Bob's account untouched.
    bob_acc = await _account_for(db, b)
    await db.refresh(bob_acc)
    assert bob_acc.closure_requested_at is None
    assert bob_acc.closed_at is None


# ---------------------------------------------------------------------------
# 11) PII purge anonymises after 1y; transactions preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_purge_anonymises_user_after_1_year(db, session_factory, redis_client):
    user = await _seed_user(db)
    account = await _account_for(db, user)
    # Pre-seed a transaction so we can verify it survives the purge.
    db.add(
        Transaction(
            account_id=account.id,
            type=TransactionKind.TOPUP,
            amount_kopecks=10_000,
            ref_id="topup-1",
            meta={"src": "test"},
        )
    )
    account.status = AccountStatus.CLOSED
    account.closed_at = datetime.now(UTC) - timedelta(days=PII_PURGE_AFTER_DAYS + 1)
    await db.commit()

    purged = await process_account_pii_purge(session_factory)
    assert purged == 1

    await db.refresh(account)
    await db.refresh(user)
    assert account.pii_purged_at is not None
    assert user.email == f"deleted+{account.id}@brikko.local"
    assert user.password_hash == ""
    assert user.telegram_chat_id is None

    # Transactions preserved (152-ФЗ 5y rule).
    txs = (
        (await db.execute(select(Transaction).where(Transaction.account_id == account.id)))
        .scalars()
        .all()
    )
    assert len(txs) == 1
    assert txs[0].amount_kopecks == 10_000


# ---------------------------------------------------------------------------
# 12) PII purge skips accounts closed less than 1y ago
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_purge_skips_recently_closed_accounts(db, session_factory, redis_client):
    user = await _seed_user(db)
    account = await _account_for(db, user)
    account.status = AccountStatus.CLOSED
    account.closed_at = datetime.now(UTC) - timedelta(days=10)
    await db.commit()

    purged = await process_account_pii_purge(session_factory)
    assert purged == 0

    await db.refresh(user)
    assert "@brikko.local" not in user.email
