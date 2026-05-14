"""Sprint 8 F4 — GET /v1/billing/transactions with filters.

The endpoint always existed but only honoured ``from``/``to``/``limit``.
This file locks in the filter additions:

* ``kind=topup|charge|refund|subscription|autorefill``
* ``min_amount`` / ``max_amount`` (kopecks, compared on |amount|)
* ``search`` substring (ref_id, meta JSON)
* ``limit`` / ``offset`` pagination + ``total_count`` in response

Cross-account isolation is implicit through ``principal.account_id`` —
verified once below to defend against regressions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from voltari_gateway.billing.engine import (
    credit_account,
    debit_account,
    refund_account,
)
from voltari_gateway.db.models import TransactionKind


async def _seed(db, account_id):
    """Seed 5 transactions of varied kinds + amounts."""
    # Topups (positive) so we have balance to debit/refund against later.
    await credit_account(
        db,
        account_id=account_id,
        amount_kopecks=10_000_00,
        ref_id="topup-A",
        kind=TransactionKind.TOPUP,
        meta={"model": "gpt-5.4-mini"},
    )
    await credit_account(
        db,
        account_id=account_id,
        amount_kopecks=500_00,
        ref_id="topup-B",
        kind=TransactionKind.TOPUP,
        meta={"model": "claude-haiku"},
    )
    # Charge (negative — internally signed by debit_account).
    await debit_account(
        db,
        account_id=account_id,
        amount_kopecks=200_00,
        ref_id="charge-A",
        meta={"model": "gpt-5.4-mini", "request_id": "req-123"},
    )
    # Refund (signed at the engine layer).
    await refund_account(
        db,
        account_id=account_id,
        amount_kopecks=100_00,
        ref_id="refund-A",
        meta={"reason": "customer asked"},
    )
    # Autorefill — uses TransactionKind.AUTOREFILL.
    await credit_account(
        db,
        account_id=account_id,
        amount_kopecks=1_000_00,
        ref_id="autorefill-A",
        kind=TransactionKind.AUTOREFILL,
        meta={"source": "yookassa_recurring"},
    )
    await db.commit()


@pytest.mark.asyncio
async def test_default_returns_all_in_desc_order(client, api_key_fixture, db):
    await _seed(db, api_key_fixture.account.id)
    r = await client.get(
        "/v1/billing/transactions",
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_count"] == 5
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert len(body["items"]) == 5
    # DESC ordering — newest first. The seed inserts in temporal order so
    # autorefill-A (last inserted) comes back first.
    assert body["items"][0]["ref_id"] == "autorefill-A"


@pytest.mark.asyncio
async def test_filter_by_kind(client, api_key_fixture, db):
    await _seed(db, api_key_fixture.account.id)
    r = await client.get(
        "/v1/billing/transactions",
        params={"kind": "charge"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 1
    assert body["items"][0]["type"] == "charge"


@pytest.mark.asyncio
async def test_filter_by_kind_invalid(client, api_key_fixture, db):
    """Unknown kind → 400 (better than silently empty)."""
    await _seed(db, api_key_fixture.account.id)
    r = await client.get(
        "/v1/billing/transactions",
        params={"kind": "frobnicate"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_transaction_kind"


@pytest.mark.asyncio
async def test_filter_by_amount_range(client, api_key_fixture, db):
    """min_amount=300_00 cuts the 100₽ refund and 200₽ charge,
    leaving topup-A (10000₽), topup-B (500₽), autorefill-A (1000₽)."""
    await _seed(db, api_key_fixture.account.id)
    r = await client.get(
        "/v1/billing/transactions",
        params={"min_amount": 300_00},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 3
    refs = {item["ref_id"] for item in body["items"]}
    assert refs == {"topup-A", "topup-B", "autorefill-A"}


@pytest.mark.asyncio
async def test_filter_by_amount_range_negative(client, api_key_fixture, db):
    """A −200₽ charge must match min_amount=100_00 (compares on |amount|)."""
    await _seed(db, api_key_fixture.account.id)
    r = await client.get(
        "/v1/billing/transactions",
        params={"min_amount": 150_00, "max_amount": 250_00},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    refs = {item["ref_id"] for item in body["items"]}
    assert "charge-A" in refs


@pytest.mark.asyncio
async def test_filter_by_date_range(client, api_key_fixture, db):
    """to_ts in the past → empty result, total_count=0."""
    await _seed(db, api_key_fixture.account.id)
    far_past = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    r = await client.get(
        "/v1/billing/transactions",
        params={"to": far_past},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_search_by_ref_id(client, api_key_fixture, db):
    """search='topup' matches both topup-A and topup-B."""
    await _seed(db, api_key_fixture.account.id)
    r = await client.get(
        "/v1/billing/transactions",
        params={"search": "topup"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 2
    refs = {item["ref_id"] for item in body["items"]}
    assert refs == {"topup-A", "topup-B"}


@pytest.mark.asyncio
async def test_search_in_meta_json(client, api_key_fixture, db):
    """search='claude' matches topup-B by its meta.model field."""
    await _seed(db, api_key_fixture.account.id)
    r = await client.get(
        "/v1/billing/transactions",
        params={"search": "claude"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    refs = {item["ref_id"] for item in body["items"]}
    assert "topup-B" in refs


@pytest.mark.asyncio
async def test_pagination_offset_limit(client, api_key_fixture, db):
    """limit=2 + offset=2 returns the second page; total_count remains 5."""
    await _seed(db, api_key_fixture.account.id)
    r = await client.get(
        "/v1/billing/transactions",
        params={"limit": 2, "offset": 2},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 2
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_combined_filters(client, api_key_fixture, db):
    """kind=topup + min_amount=600_00 → only topup-A (10000₽), not topup-B (500₽)."""
    await _seed(db, api_key_fixture.account.id)
    r = await client.get(
        "/v1/billing/transactions",
        params={"kind": "topup", "min_amount": 600_00},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 1
    assert body["items"][0]["ref_id"] == "topup-A"


@pytest.mark.asyncio
async def test_empty_account_returns_zero(client, api_key_fixture):
    """No seed → total_count=0 + empty items, not an error."""
    r = await client.get(
        "/v1/billing/transactions",
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 0
    assert body["items"] == []
    assert body["limit"] == 50
    assert body["offset"] == 0


@pytest.mark.asyncio
async def test_cross_account_isolation(client, api_key_fixture, db):
    """Seeding another account must not leak into our list."""
    from voltari_gateway.db.models import (
        Account,
        AccountStatus,
        Tariff,
        User,
    )

    # Fixture's account already exists. Add a second user/account and
    # seed a topup for them.
    other_user = User(
        email=f"other-{uuid.uuid4().hex[:6]}@test.local",
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
    )
    db.add(other_acc)
    await db.flush()
    await credit_account(
        db,
        account_id=other_acc.id,
        amount_kopecks=999_99,
        ref_id="other-topup",
        kind=TransactionKind.TOPUP,
    )
    await db.commit()

    r = await client.get(
        "/v1/billing/transactions",
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    refs = {item["ref_id"] for item in body["items"]}
    assert "other-topup" not in refs
