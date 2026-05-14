"""Sprint 8 F6 — GET /v1/account/activity.

Merged feed of audit_log + transactions, newest-first, capped by ``limit``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from voltari_gateway.auth.password import hash_password
from voltari_gateway.billing.engine import credit_account
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    AuditLog,
    Tariff,
    TransactionKind,
    User,
)

_PASSWORD = "correct horse battery staple"


async def _seed_user(db, *, tariff: Tariff = Tariff.PRO) -> tuple[User, Account]:
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    acc = Account(
        owner_id=user.id,
        name="Activity-test",
        balance_kopecks=100_000,
        tariff=tariff,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
        settings={},
    )
    db.add(acc)
    await db.commit()
    await db.refresh(user)
    await db.refresh(acc)
    return user, acc


async def _login(client, user) -> None:
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_activity_merges_audit_and_transactions(client, db):
    """Feed surfaces both /v1/auth/login and a topup, newest first."""
    user, acc = await _seed_user(db)
    await _login(client, user)  # writes audit_log row "login_ok"

    # Seed a transaction the activity feed should pick up.
    await credit_account(
        db,
        account_id=acc.id,
        amount_kopecks=500_00,
        ref_id="topup-activity-1",
        kind=TransactionKind.TOPUP,
    )
    await db.commit()

    r = await client.get("/v1/account/activity")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["limit"] == 20
    sources = {item["source"] for item in body["items"]}
    assert "audit" in sources
    assert "transaction" in sources

    # Login event present.
    types = [item["type"] for item in body["items"]]
    assert "auth_login" in types
    assert "balance_topup" in types


@pytest.mark.asyncio
async def test_activity_limit_caps_results(client, db):
    user, acc = await _seed_user(db)
    await _login(client, user)
    # Seed 5 topups + the login event (1 audit row).
    for i in range(5):
        await credit_account(
            db,
            account_id=acc.id,
            amount_kopecks=100_00,
            ref_id=f"limit-test-{i}",
            kind=TransactionKind.TOPUP,
        )
    await db.commit()

    r = await client.get("/v1/account/activity", params={"limit": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["limit"] == 3
    assert len(body["items"]) == 3


@pytest.mark.asyncio
async def test_activity_cross_account_isolation(client, db):
    """Other account's audit/txn rows must not leak into this user's feed."""
    user, acc = await _seed_user(db)
    await _login(client, user)
    await credit_account(
        db,
        account_id=acc.id,
        amount_kopecks=200_00,
        ref_id="own-topup",
        kind=TransactionKind.TOPUP,
    )

    # Forge a foreign account + direct audit row + tx.
    other_user = User(
        email=f"o-{uuid.uuid4().hex[:6]}@x.test",
        password_hash="dummy",
        email_verified=True,
    )
    db.add(other_user)
    await db.flush()
    other_acc = Account(
        owner_id=other_user.id,
        name="Other",
        balance_kopecks=0,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
        settings={},
    )
    db.add(other_acc)
    await db.flush()
    db.add(
        AuditLog(
            user_id=other_user.id,
            account_id=other_acc.id,
            action="tariff_changed",
            outcome="ok",
            meta={"before": "payg", "after": "pro"},
        )
    )
    await credit_account(
        db,
        account_id=other_acc.id,
        amount_kopecks=999_99,
        ref_id="other-topup",
        kind=TransactionKind.TOPUP,
    )
    await db.commit()

    r = await client.get("/v1/account/activity", params={"limit": 100})
    assert r.status_code == 200
    body = r.json()

    # No foreign-account ref_id should appear in our items.
    refs_seen = []
    for item in body["items"]:
        details = item.get("details") or {}
        if details.get("ref_id"):
            refs_seen.append(details["ref_id"])
    assert "other-topup" not in refs_seen
    assert "own-topup" in refs_seen


@pytest.mark.asyncio
async def test_activity_orders_newest_first(client, db):
    """Two events at very different timestamps must come back DESC."""
    user, acc = await _seed_user(db)
    await _login(client, user)
    # Manually insert an audit row with an OLD timestamp.
    old_audit = AuditLog(
        user_id=user.id,
        account_id=acc.id,
        action="tariff_changed",
        outcome="ok",
        meta={"before": "payg", "after": "pro"},
    )
    old_audit.created_at = datetime.now(UTC) - timedelta(days=30)
    db.add(old_audit)
    await db.commit()

    r = await client.get("/v1/account/activity", params={"limit": 20})
    assert r.status_code == 200
    body = r.json()
    timestamps = [
        datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")) for item in body["items"]
    ]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_activity_unauthenticated(client):
    r = await client.get("/v1/account/activity")
    assert r.status_code == 401
