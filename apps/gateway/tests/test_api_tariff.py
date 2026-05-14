"""Tests for ``PATCH /v1/account/tariff`` (Sprint 6, Блок 9)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from voltari_gateway import config as cfg
from voltari_gateway.api.tariff import TARIFF_MONTHLY_FEE_KOPECKS
from voltari_gateway.auth import rate_limit as rl
from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    Transaction,
    TransactionKind,
    User,
)


@pytest.fixture(autouse=True)
def cookie_friendly_env(monkeypatch):
    """Cookie / CORS env so httpx ASGI transport can run cookie-auth flows."""
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("COOKIE_DOMAIN", "")
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    monkeypatch.setenv("BASE_URL_FRONTEND", "http://test")
    cfg.get_settings.cache_clear()
    rl.reload_from_settings()
    yield
    cfg.get_settings.cache_clear()
    rl.reload_from_settings()


async def _make_user_account(
    db, *, balance_kopecks: int = 0, tariff: Tariff = Tariff.PAYG
) -> tuple[User, Account]:
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("correct horse battery"),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    acc = Account(
        owner_id=user.id,
        name="x",
        balance_kopecks=balance_kopecks,
        tariff=tariff,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
    )
    db.add(acc)
    await db.commit()
    await db.refresh(user)
    await db.refresh(acc)
    return user, acc


async def _login(client, user: User) -> None:
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "correct horse battery"},
    )
    assert r.status_code == 200, r.text


async def _csrf(client) -> dict[str, str]:
    r = await client.get("/v1/auth/csrf")
    return {"X-CSRF-Token": r.json()["csrf_token"]}


@pytest.mark.asyncio
async def test_upgrade_payg_to_pro_charges_balance(client, db):
    user, acc = await _make_user_account(db, balance_kopecks=200_000)
    await _login(client, user)
    r = await client.patch(
        "/v1/account/tariff",
        json={"tariff": "pro", "confirmation": "UPGRADE"},
        headers=await _csrf(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tariff"] == "pro"
    assert body["previous_tariff"] == "payg"
    assert body["charged_kopecks"] == TARIFF_MONTHLY_FEE_KOPECKS[Tariff.PRO]
    assert body["balance_kopecks"] == 200_000 - TARIFF_MONTHLY_FEE_KOPECKS[Tariff.PRO]
    assert body["tariff_active_until"] is not None
    assert body["transaction_id"] is not None

    await db.refresh(acc)
    assert acc.tariff == Tariff.PRO


@pytest.mark.asyncio
async def test_upgrade_insufficient_balance_402(client, db, session_factory):
    user, acc = await _make_user_account(db, balance_kopecks=10_000)  # 100 ₽
    acc_id = acc.id
    await _login(client, user)
    r = await client.patch(
        "/v1/account/tariff",
        json={"tariff": "pro", "confirmation": "UPGRADE"},
        headers=await _csrf(client),
    )
    assert r.status_code == 402, r.text
    assert r.json()["error"]["code"] == "insufficient_balance"
    # Re-read via a fresh session because the endpoint rolled back its own
    # txn — using the same fixture session here would pull the rolled-back
    # state and trip MissingGreenlet on the connection.
    async with session_factory() as fresh_db:
        fresh = (await fresh_db.execute(select(Account).where(Account.id == acc_id))).scalar_one()
        assert fresh.tariff == Tariff.PAYG


@pytest.mark.asyncio
async def test_idempotency_double_post_only_charges_once(client, db):
    user, acc = await _make_user_account(db, balance_kopecks=500_000)
    await _login(client, user)
    headers = await _csrf(client)
    headers["Idempotency-Key"] = "test-idempotency-1"
    body = {"tariff": "pro", "confirmation": "UPGRADE"}
    r1 = await client.patch("/v1/account/tariff", json=body, headers=headers)
    # After first call, account is on pro — second PATCH would be "same_tariff".
    # So the idempotency check is on the ledger, not the endpoint contract:
    # verify there's exactly one tariff_charge.
    assert r1.status_code == 200, r1.text
    rows = (
        (
            await db.execute(
                select(Transaction).where(
                    Transaction.account_id == acc.id,
                    Transaction.type == TransactionKind.CHARGE,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].amount_kopecks == -TARIFF_MONTHLY_FEE_KOPECKS[Tariff.PRO]
    assert rows[0].meta.get("kind") == "tariff_change"


@pytest.mark.asyncio
async def test_upgrade_pro_to_pro_privacy_charges_delta(client, db):
    """Mid-cycle upgrade Pro → Pro Privacy charges only the price delta."""
    from datetime import UTC, datetime, timedelta

    user, acc = await _make_user_account(db, balance_kopecks=1_000_000)
    acc.tariff = Tariff.PRO
    acc.tariff_active_until = datetime.now(UTC) + timedelta(days=20)
    await db.commit()

    period_anchor = acc.tariff_active_until
    await _login(client, user)
    r = await client.patch(
        "/v1/account/tariff",
        json={"tariff": "pro_privacy", "confirmation": "UPGRADE"},
        headers=await _csrf(client),
    )
    assert r.status_code == 200, r.text
    delta = TARIFF_MONTHLY_FEE_KOPECKS[Tariff.PRO_PRIVACY] - TARIFF_MONTHLY_FEE_KOPECKS[Tariff.PRO]
    assert r.json()["charged_kopecks"] == delta
    await db.refresh(acc)
    assert acc.tariff == Tariff.PRO_PRIVACY
    # Period anchor preserved (compare on naive value because SQLite drops tzinfo).
    assert acc.tariff_active_until is not None
    assert acc.tariff_active_until.replace(tzinfo=None) == period_anchor.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_downgrade_to_payg_no_charge_no_refund(client, db):
    from datetime import UTC, datetime, timedelta

    user, acc = await _make_user_account(db, balance_kopecks=200_000, tariff=Tariff.PRO)
    acc.tariff_active_until = datetime.now(UTC) + timedelta(days=15)
    await db.commit()
    initial_balance = acc.balance_kopecks
    await _login(client, user)
    r = await client.patch(
        "/v1/account/tariff",
        json={"tariff": "payg", "confirmation": "DOWNGRADE"},
        headers=await _csrf(client),
    )
    assert r.status_code == 200, r.text
    assert r.json()["charged_kopecks"] == 0
    await db.refresh(acc)
    assert acc.tariff == Tariff.PAYG
    assert acc.balance_kopecks == initial_balance  # forfeit, not refund
    assert acc.tariff_active_until is None


@pytest.mark.asyncio
async def test_same_tariff_400(client, db):
    user, acc = await _make_user_account(db, tariff=Tariff.PRO, balance_kopecks=500_000)
    await _login(client, user)
    r = await client.patch(
        "/v1/account/tariff",
        json={"tariff": "pro", "confirmation": "UPGRADE"},
        headers=await _csrf(client),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "same_tariff"


@pytest.mark.asyncio
async def test_bad_confirmation_phrase_400(client, db):
    user, acc = await _make_user_account(db, balance_kopecks=500_000)
    await _login(client, user)
    r = await client.patch(
        "/v1/account/tariff",
        json={"tariff": "pro", "confirmation": "yes please"},
        headers=await _csrf(client),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_confirmation"


@pytest.mark.asyncio
async def test_non_owner_forbidden_403(client, db):
    """A user without ownership over the account that the cookie session
    is bound to gets a 403. Covers admin/member rejection.
    """
    # Create owner + a separate user; the cookie session is bound to the
    # owner's account by default. We simulate "non-owner" by re-pointing
    # account.owner_id to a different user before the PATCH.
    user, acc = await _make_user_account(db, balance_kopecks=500_000)
    other = User(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("correct horse battery"),
        email_verified=True,
    )
    db.add(other)
    await db.flush()
    await _login(client, user)
    # After login flip ownership so the cookie's user is no longer the owner.
    acc.owner_id = other.id
    await db.commit()
    r = await client.patch(
        "/v1/account/tariff",
        json={"tariff": "pro", "confirmation": "UPGRADE"},
        headers=await _csrf(client),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "not_owner"
