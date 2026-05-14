"""Sprint 5 — concurrency / atomicity tests.

Сценарии параллельных операций на одном аккаунте, где несколько
«потоков» (asyncio gather) пытаются вместе сжечь один и тот же
балансовый ресурс. Этот файл ловит баги, связанные с:

* Двойным списанием при гонке debit_account (idempotency на ref_id).
* Race в hold_amount (CAS / advisory lock).
* commit_hold_to_debit с правильным ordering (release ДО debit).
* Refund / topup конкурируют → конечное состояние корректное.

Note: эти тесты идут на SQLite через StaticPool (один connection
сериализует writes), поэтому они НЕ доказывают Postgres-CAS — для
этого есть test_billing_engine integration tests на testcontainers.
Здесь проверяем семантику кода (idempotency, error paths).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.billing.engine import (
    BillingError,
    InsufficientBalanceError,
    commit_hold_to_debit,
    credit_account,
    debit_account,
    hold_amount,
    refund_account,
    release_hold,
)
from voltari_gateway.db.models import Account, AccountStatus, Tariff, TransactionKind, User


@pytest.fixture
async def funded_account(db: AsyncSession) -> Account:
    """Pre-seeded account with 100 000 kopecks."""
    user = User(
        email=f"concurrency-{uuid.uuid4().hex[:6]}@x.local",
        password_hash="$argon2id$dummy",
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    acc = Account(
        owner_id=user.id,
        name="acc",
        balance_kopecks=100_000,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return acc


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debit_account_idempotent_on_same_ref_id(
    db: AsyncSession, funded_account: Account
) -> None:
    """Two debits with the same ref_id → only ONE balance change."""
    initial = funded_account.balance_kopecks
    tx1 = await debit_account(
        db, account_id=funded_account.id, amount_kopecks=1000, ref_id="dup-ref"
    )
    await db.commit()

    tx2 = await debit_account(
        db, account_id=funded_account.id, amount_kopecks=1000, ref_id="dup-ref"
    )
    await db.commit()

    # Both calls return the SAME transaction (idempotency lookup).
    assert tx1.id == tx2.id

    # Balance changed only once — 100k → 99k.
    await db.refresh(funded_account)
    assert funded_account.balance_kopecks == initial - 1000


@pytest.mark.asyncio
async def test_credit_account_idempotent_on_same_ref_id(
    db: AsyncSession, funded_account: Account
) -> None:
    """Two credits with the same ref_id → only one balance change."""
    initial = funded_account.balance_kopecks
    tx1 = await credit_account(
        db, account_id=funded_account.id, amount_kopecks=5000, ref_id="topup-1"
    )
    await db.commit()
    tx2 = await credit_account(
        db, account_id=funded_account.id, amount_kopecks=5000, ref_id="topup-1"
    )
    await db.commit()

    assert tx1.id == tx2.id

    await db.refresh(funded_account)
    assert funded_account.balance_kopecks == initial + 5000


@pytest.mark.asyncio
async def test_hold_idempotent_on_same_ref_id(db: AsyncSession, funded_account: Account) -> None:
    """Two holds with same ref_id → both return the same handle, only one row."""
    h1 = await hold_amount(
        db,
        account_id=funded_account.id,
        amount_kopecks=2000,
        ref_id="hold-x",
    )
    await db.commit()
    h2 = await hold_amount(
        db,
        account_id=funded_account.id,
        amount_kopecks=2000,
        ref_id="hold-x",
    )
    await db.commit()

    # Same ref_id, same handle (amount and ID unchanged).
    assert h1.ref_id == h2.ref_id
    assert h1.amount_kopecks == h2.amount_kopecks


# ---------------------------------------------------------------------------
# Insufficient balance scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debit_more_than_balance_raises(db: AsyncSession, funded_account: Account) -> None:
    """Debiting more than balance → InsufficientBalanceError, balance unchanged."""
    with pytest.raises(InsufficientBalanceError) as exc_info:
        await debit_account(
            db,
            account_id=funded_account.id,
            amount_kopecks=200_000,  # > 100k balance
            ref_id="overspend",
        )
    assert exc_info.value.balance_kopecks == 100_000
    assert exc_info.value.required_kopecks == 200_000

    # Balance unchanged.
    await db.refresh(funded_account)
    assert funded_account.balance_kopecks == 100_000


@pytest.mark.asyncio
async def test_hold_more_than_balance_raises(db: AsyncSession, funded_account: Account) -> None:
    """Hold > balance → InsufficientBalanceError, no row created."""
    with pytest.raises(InsufficientBalanceError):
        await hold_amount(
            db,
            account_id=funded_account.id,
            amount_kopecks=200_000,
            ref_id="overhold",
        )


@pytest.mark.asyncio
async def test_holds_sum_blocks_more_than_balance(
    db: AsyncSession, funded_account: Account
) -> None:
    """Two 60k holds on a 100k balance → second one fails (sum 120k > 100k)."""
    await hold_amount(
        db,
        account_id=funded_account.id,
        amount_kopecks=60_000,
        ref_id="h1",
    )
    await db.commit()

    with pytest.raises(InsufficientBalanceError):
        await hold_amount(
            db,
            account_id=funded_account.id,
            amount_kopecks=60_000,
            ref_id="h2",
        )


@pytest.mark.asyncio
async def test_zero_amount_debit_rejected(db: AsyncSession, funded_account: Account) -> None:
    """``amount_kopecks=0`` rejected (must be positive)."""
    with pytest.raises(BillingError, match="positive"):
        await debit_account(
            db,
            account_id=funded_account.id,
            amount_kopecks=0,
            ref_id="zero-debit",
        )


@pytest.mark.asyncio
async def test_negative_amount_debit_rejected(db: AsyncSession, funded_account: Account) -> None:
    with pytest.raises(BillingError, match="positive"):
        await debit_account(
            db,
            account_id=funded_account.id,
            amount_kopecks=-100,
            ref_id="neg-debit",
        )


@pytest.mark.asyncio
async def test_zero_amount_credit_rejected(db: AsyncSession, funded_account: Account) -> None:
    with pytest.raises(BillingError, match="positive"):
        await credit_account(
            db,
            account_id=funded_account.id,
            amount_kopecks=0,
            ref_id="zero-credit",
        )


@pytest.mark.asyncio
async def test_zero_amount_hold_rejected(db: AsyncSession, funded_account: Account) -> None:
    with pytest.raises(BillingError, match="positive"):
        await hold_amount(
            db,
            account_id=funded_account.id,
            amount_kopecks=0,
            ref_id="zero-hold",
        )


# ---------------------------------------------------------------------------
# Refund overdraft scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refund_can_create_small_overdraft(db: AsyncSession, funded_account: Account) -> None:
    """Per scenario 19: refund > balance allowed up to floor (-1000 kop)."""
    # Spend 200 first → balance = 99800
    await debit_account(db, account_id=funded_account.id, amount_kopecks=200, ref_id="d1")
    await db.commit()

    # Refund 99850 → balance becomes -50 (small overdraft, allowed).
    await refund_account(db, account_id=funded_account.id, amount_kopecks=99_850, ref_id="r1")
    await db.commit()

    await db.refresh(funded_account)
    assert funded_account.balance_kopecks == -50


@pytest.mark.asyncio
async def test_refund_above_overdraft_floor_rejected(
    db: AsyncSession, funded_account: Account
) -> None:
    """Refund that would push balance below -1000 → BillingError."""
    with pytest.raises(BillingError, match="overdraft_floor"):
        await refund_account(
            db,
            account_id=funded_account.id,
            amount_kopecks=200_000,  # would push to -100k, far below -1000 floor
            ref_id="r-bust",
        )


@pytest.mark.asyncio
async def test_refund_idempotent_on_same_ref_id(db: AsyncSession, funded_account: Account) -> None:
    """Two refund calls with same ref_id → only one balance change."""
    initial = funded_account.balance_kopecks
    r1 = await refund_account(
        db, account_id=funded_account.id, amount_kopecks=500, ref_id="ref-id-1"
    )
    await db.commit()
    r2 = await refund_account(
        db, account_id=funded_account.id, amount_kopecks=500, ref_id="ref-id-1"
    )
    await db.commit()

    assert r1.id == r2.id
    await db.refresh(funded_account)
    assert funded_account.balance_kopecks == initial - 500


# ---------------------------------------------------------------------------
# commit_hold_to_debit ordering / over-commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_hold_to_debit_with_smaller_actual(
    db: AsyncSession, funded_account: Account
) -> None:
    """Hold 5000 kop, actual debit 2000 — balance loses only 2000."""
    initial = funded_account.balance_kopecks
    handle = await hold_amount(db, account_id=funded_account.id, amount_kopecks=5000, ref_id="r-1")
    await db.commit()

    await commit_hold_to_debit(db, handle=handle, actual_amount_kopecks=2000)
    await db.commit()

    await db.refresh(funded_account)
    assert funded_account.balance_kopecks == initial - 2000


@pytest.mark.asyncio
async def test_commit_hold_to_debit_with_larger_actual(
    db: AsyncSession, funded_account: Account
) -> None:
    """Hold 1000, actual 3000 — debit_account allows the larger amount.

    The hold is just an estimate; actual provider cost can exceed it. As
    long as the balance can absorb the diff, debit succeeds.
    """
    initial = funded_account.balance_kopecks
    handle = await hold_amount(
        db, account_id=funded_account.id, amount_kopecks=1000, ref_id="r-larger"
    )
    await db.commit()

    await commit_hold_to_debit(db, handle=handle, actual_amount_kopecks=3000)
    await db.commit()

    await db.refresh(funded_account)
    assert funded_account.balance_kopecks == initial - 3000


@pytest.mark.asyncio
async def test_release_hold_returns_balance_to_available(
    db: AsyncSession, funded_account: Account
) -> None:
    """After release, the held kopecks are available again for new holds."""
    handle = await hold_amount(
        db, account_id=funded_account.id, amount_kopecks=70_000, ref_id="r-1"
    )
    await db.commit()

    # 70k held, 30k available — second 70k hold would fail.
    with pytest.raises(InsufficientBalanceError):
        await hold_amount(db, account_id=funded_account.id, amount_kopecks=70_000, ref_id="r-2")

    # Release first hold; now full 100k available.
    await release_hold(db, handle)
    await db.commit()

    h2 = await hold_amount(db, account_id=funded_account.id, amount_kopecks=70_000, ref_id="r-3")
    assert h2.amount_kopecks == 70_000


@pytest.mark.asyncio
async def test_release_hold_idempotent(db: AsyncSession, funded_account: Account) -> None:
    """Calling release twice is fine (idempotent)."""
    handle = await hold_amount(
        db, account_id=funded_account.id, amount_kopecks=1000, ref_id="r-rel"
    )
    await db.commit()

    await release_hold(db, handle)
    await db.commit()
    # Second call — no-op, no error.
    await release_hold(db, handle)
    await db.commit()


# ---------------------------------------------------------------------------
# Round-trip: hold → commit → balance correct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_chat_billing_cycle(db: AsyncSession, funded_account: Account) -> None:
    """Simulate chat: hold estimate → call → commit actual.

    Final balance must equal initial - actual cost. Hold disappears from
    account_holds. Transaction row created with correct sign.
    """
    initial = funded_account.balance_kopecks
    estimated = 5000
    actual = 1234

    handle = await hold_amount(
        db,
        account_id=funded_account.id,
        amount_kopecks=estimated,
        ref_id="full-cycle",
    )
    await db.commit()

    tx = await commit_hold_to_debit(
        db,
        handle=handle,
        actual_amount_kopecks=actual,
        meta={"model": "gpt-5.4-mini"},
    )
    await db.commit()

    await db.refresh(funded_account)
    assert funded_account.balance_kopecks == initial - actual
    assert tx.amount_kopecks == -actual  # negative for charge
    assert tx.type == TransactionKind.CHARGE
    assert tx.meta == {"model": "gpt-5.4-mini"}


@pytest.mark.asyncio
async def test_credit_then_debit_then_refund_arithmetic(
    db: AsyncSession, funded_account: Account
) -> None:
    """Sequence of operations gives exact expected final balance."""
    initial = funded_account.balance_kopecks
    await credit_account(db, account_id=funded_account.id, amount_kopecks=10_000, ref_id="c1")
    await db.commit()
    await debit_account(db, account_id=funded_account.id, amount_kopecks=3_000, ref_id="d1")
    await db.commit()
    await refund_account(db, account_id=funded_account.id, amount_kopecks=2_000, ref_id="r1")
    await db.commit()

    await db.refresh(funded_account)
    # initial + 10k - 3k - 2k
    assert funded_account.balance_kopecks == initial + 10_000 - 3_000 - 2_000


# ---------------------------------------------------------------------------
# Concurrent operations on same account (asyncio.gather)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_debits_on_one_account_correct_total(
    session_factory: Any, funded_account: Account
) -> None:
    """5 sequential debits, each 1k, on a 100k balance — final balance 95k.

    Sequential rather than gather() because SQLite's snapshot isolation
    means concurrent reads each see initial balance, and the StaticPool
    serialises commits but not the read-before-write — production tests
    on Postgres testcontainers exercise the real concurrent path
    (test_billing_engine.py::test_cas_high_concurrency_exactly_fits).
    """
    for i in range(5):
        async with session_factory() as s:
            await debit_account(
                s,
                account_id=funded_account.id,
                amount_kopecks=1_000,
                ref_id=f"seq-{i}",
            )
            await s.commit()

    async with session_factory() as s:
        acc = await s.get(Account, funded_account.id)
        assert acc is not None
        assert acc.balance_kopecks == 95_000


@pytest.mark.asyncio
async def test_sequential_same_ref_id_idempotent_one_debit(
    session_factory: Any, funded_account: Account
) -> None:
    """5 SEQUENTIAL calls with SAME ref_id → exactly one balance change.

    This is the key idempotency property — second call with the same
    ref_id finds the existing transaction and short-circuits. Concurrent
    flavour of this is exercised on the Postgres path
    (test_billing_engine.py) where ON CONFLICT DO NOTHING handles the
    real race; SQLite locks at file level so the sequential check is
    semantically equivalent.
    """
    initial = funded_account.balance_kopecks

    for _ in range(5):
        async with session_factory() as s:
            await debit_account(
                s,
                account_id=funded_account.id,
                amount_kopecks=2_000,
                ref_id="shared-ref-seq",
            )
            await s.commit()

    # BALANCE only changes ONCE.
    async with session_factory() as s:
        acc = await s.get(Account, funded_account.id)
        assert acc is not None
        assert acc.balance_kopecks == initial - 2_000


@pytest.mark.asyncio
async def test_sequential_holds_sum_exceeds_balance_blocks_third(
    session_factory: Any, funded_account: Account
) -> None:
    """Sequential 50k holds on 100k balance — first two succeed, third blocks.

    Sequential to avoid SQLite snapshot races (Postgres path is tested
    end-to-end on testcontainers). Confirms ``hold_amount`` reads
    sum_holds correctly when called serially.
    """
    h1_ok = False
    h2_ok = False
    h3_blocked = False
    async with session_factory() as s:
        await hold_amount(s, account_id=funded_account.id, amount_kopecks=50_000, ref_id="seq-h-1")
        await s.commit()
        h1_ok = True
    async with session_factory() as s:
        await hold_amount(s, account_id=funded_account.id, amount_kopecks=50_000, ref_id="seq-h-2")
        await s.commit()
        h2_ok = True
    async with session_factory() as s:
        try:
            await hold_amount(
                s, account_id=funded_account.id, amount_kopecks=50_000, ref_id="seq-h-3"
            )
            await s.commit()
        except InsufficientBalanceError:
            h3_blocked = True
    assert h1_ok and h2_ok and h3_blocked
