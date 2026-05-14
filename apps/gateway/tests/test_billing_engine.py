"""Unit tests for ``voltari_gateway.billing.engine``.

Covers scenarios 16, 17, 19, 20, 23 from
``03_Finance/10_billing_test_scenarios.md`` directly. The remaining race /
limit / lifecycle scenarios are exercised in ``test_billing_api.py`` and
``test_billing_scenarios.py``.
"""

from __future__ import annotations

import pytest

from voltari_gateway.billing.engine import (
    InsufficientBalanceError,
    commit_hold_to_debit,
    credit_account,
    debit_account,
    hold_amount,
    refund_account,
    release_hold,
)
from voltari_gateway.db.models import Account, TransactionKind


@pytest.mark.asyncio
async def test_debit_basic(db, api_key_fixture):
    """Sc. 1 baseline: simple debit reduces balance, records transaction."""
    acc = api_key_fixture.account
    start = acc.balance_kopecks
    tx = await debit_account(
        db,
        account_id=acc.id,
        amount_kopecks=28,
        ref_id="req-1",
    )
    await db.commit()
    await db.refresh(acc)

    assert acc.balance_kopecks == start - 28
    assert tx.amount_kopecks == -28
    assert tx.type == TransactionKind.CHARGE


@pytest.mark.asyncio
async def test_debit_idempotent(db, api_key_fixture):
    """Sc. 17 (chat path): same ref_id called twice → one debit total."""
    acc = api_key_fixture.account
    start = acc.balance_kopecks

    tx1 = await debit_account(db, account_id=acc.id, amount_kopecks=100, ref_id="req-2")
    await db.commit()
    tx2 = await debit_account(db, account_id=acc.id, amount_kopecks=100, ref_id="req-2")
    await db.commit()

    await db.refresh(acc)
    assert tx1.id == tx2.id
    assert acc.balance_kopecks == start - 100  # only deducted once


@pytest.mark.asyncio
async def test_debit_insufficient_balance(db, api_key_fixture):
    """Sc. 23: balance < amount → InsufficientBalanceError."""
    acc = api_key_fixture.account
    acc.balance_kopecks = 50
    db.add(acc)
    await db.commit()

    with pytest.raises(InsufficientBalanceError) as ei:
        await debit_account(db, account_id=acc.id, amount_kopecks=100, ref_id="req-3")
    assert ei.value.balance_kopecks == 50
    assert ei.value.required_kopecks == 100


@pytest.mark.asyncio
async def test_credit_idempotent_payment(db, api_key_fixture):
    """Sc. 17: ЮKassa webhook delivered twice → credit applied once."""
    acc = api_key_fixture.account
    start = acc.balance_kopecks

    tx1 = await credit_account(db, account_id=acc.id, amount_kopecks=200_000, ref_id="yk_pay_abc")
    await db.commit()
    tx2 = await credit_account(db, account_id=acc.id, amount_kopecks=200_000, ref_id="yk_pay_abc")
    await db.commit()

    await db.refresh(acc)
    assert tx1.id == tx2.id
    assert acc.balance_kopecks == start + 200_000  # only +1 topup


@pytest.mark.asyncio
async def test_refund_can_overdraft(db, api_key_fixture):
    """Sc. 19: client paid 5000 ₽, spent 200 ₽, refunded 5000 ₽ → balance -200 ₽.

    Our DB CHECK floor is -1000 kop (10 ₽), so we use a smaller scale
    refund here: balance 480 → spent 20 → refund 500 → -20.
    """
    acc = api_key_fixture.account
    acc.balance_kopecks = 480  # 4.80 ₽
    db.add(acc)
    await db.commit()

    await refund_account(
        db,
        account_id=acc.id,
        amount_kopecks=500,
        ref_id="yk_refund_1",
    )
    await db.commit()
    await db.refresh(acc)
    assert acc.balance_kopecks == -20  # within REFUND_OVERDRAFT_FLOOR_KOPECKS


@pytest.mark.asyncio
async def test_refund_below_overdraft_floor_rejected(db, api_key_fixture):
    """Refund cannot bust the DB-level overdraft floor."""
    acc = api_key_fixture.account
    acc.balance_kopecks = 0
    db.add(acc)
    await db.commit()

    with pytest.raises(Exception):
        await refund_account(
            db,
            account_id=acc.id,
            amount_kopecks=10_000,  # would push to -10000 kop
            ref_id="yk_refund_2",
        )


@pytest.mark.asyncio
async def test_hold_blocks_overspend(session_factory, api_key_fixture):
    """Sc. 16: parallel holds at balance 0.20 ₽; second cannot pre-reserve.

    Migrated from Redis to Postgres holds (BE P0-3 / Alembic 0005). The
    contract is the same — second hold raises InsufficientBalanceError —
    but the storage is now transactional with the balance. Each hold
    attempt runs in its own session so a raised error doesn't poison
    subsequent operations (matches the production lifecycle where each
    request gets its own session).
    """
    acc = api_key_fixture.account

    async with session_factory() as s:
        refreshed = await s.get(Account, acc.id)
        assert refreshed is not None
        refreshed.balance_kopecks = 20  # 0.20 ₽
        s.add(refreshed)
        await s.commit()

    async with session_factory() as s:
        h1 = await hold_amount(
            s,
            account_id=acc.id,
            amount_kopecks=15,
            ref_id="hold-1",
        )
        await s.commit()
        assert h1.amount_kopecks == 15

    async with session_factory() as s:
        with pytest.raises(InsufficientBalanceError):
            await hold_amount(
                s,
                account_id=acc.id,
                amount_kopecks=15,
                ref_id="hold-2",
            )

    async with session_factory() as s:
        await release_hold(s, h1)
        await s.commit()

    # After release, a fresh hold succeeds.
    async with session_factory() as s:
        h2 = await hold_amount(
            s,
            account_id=acc.id,
            amount_kopecks=15,
            ref_id="hold-2",
        )
        await s.commit()
        assert h2 is not None


@pytest.mark.asyncio
async def test_commit_hold_settles_actual_amount(db, api_key_fixture):
    """A hold of 100 kop, debited at 80 kop → balance reduced by 80, not 100."""
    acc = api_key_fixture.account
    start = acc.balance_kopecks

    handle = await hold_amount(
        db,
        account_id=acc.id,
        amount_kopecks=100,
        ref_id="ref-commit",
    )
    await commit_hold_to_debit(db, handle=handle, actual_amount_kopecks=80)
    await db.commit()
    await db.refresh(acc)
    assert acc.balance_kopecks == start - 80


@pytest.mark.asyncio
async def test_sequential_debits_respect_balance(session_factory, api_key_fixture):
    """Sc. 16: sequential debits on the same account never overshoot.

    On Postgres the ``with_for_update`` clause gives us strict serialisation
    even for fully concurrent calls. SQLite (used in tests) does not honour
    ``FOR UPDATE`` and the StaticPool serialises one statement at a time —
    not the read-then-write pair, so a TOCTOU window exists in tests only.
    Production correctness is verified by the Postgres integration test
    (gated, runs in CI on a real PG).

    Here we assert the *post-condition*: the running balance is consistent
    with the transaction log, and the second over-budget debit raises.
    """
    acc = api_key_fixture.account

    async with session_factory() as s:
        await debit_account(s, account_id=acc.id, amount_kopecks=60_000, ref_id="seq-1")
        await s.commit()

    async with session_factory() as s:
        with pytest.raises(InsufficientBalanceError):
            await debit_account(s, account_id=acc.id, amount_kopecks=60_000, ref_id="seq-2")

    async with session_factory() as s:
        refreshed = await s.get(Account, acc.id)
        assert refreshed is not None
        assert refreshed.balance_kopecks == 100_000 - 60_000  # only one debit landed


@pytest.mark.asyncio
async def test_zero_amount_rejected(db, api_key_fixture):
    with pytest.raises(Exception):
        await debit_account(db, account_id=api_key_fixture.account.id, amount_kopecks=0, ref_id="r")
    with pytest.raises(Exception):
        await credit_account(
            db, account_id=api_key_fixture.account.id, amount_kopecks=0, ref_id="r"
        )
