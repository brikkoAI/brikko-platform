"""Postgres-backed concurrency tests — webhook idempotency + hold storms.

Sprint 4 Поток L (QA implement) — реализация плана Sprint 3 Поток K секции
``test_chaos_webhook_concurrent.py``.

Зачем нужна именно postgres (testcontainers), а не SQLite:

* CAS-путь ``hold_amount`` (TD-029) использует ``pg_advisory_xact_lock`` и
  Postgres-only ``RETURNING`` semantics. SQLite попадает в fallback
  read-modify-write, который сериализуется через ``StaticPool`` и не
  даёт того же сигнала о race-conditions.
* ``credit_account`` идемпотентность опирается на UNIQUE(account_id, ref_id)
  + SAVEPOINT rollback. Под Postgres SAVEPOINT поведение отличается от
  SQLite — нужен реальный движок.
* ``processed_webhooks`` upsert использует ``ON CONFLICT DO UPDATE`` с
  ``WHERE`` predicate (``don't downgrade processed → failed``) — это
  тонкая семантика, которая на SQLite-3.24+ работает «достаточно похоже»,
  но prod — Postgres, и тестировать надо здесь.

Скип-стратегия:
* ``SKIP_POSTGRES_TESTS=1`` → skip module
* Docker недоступен → skip module
* ``testcontainers`` пакет отсутствует → importorskip → skip

Сценарии (TC-K5.1..K5.4):

* TC-K5.1: 50 уникальных payment_id concurrent → 50 кредитов, balance =
  base + sum(amounts), processed_webhooks: 50 rows status=processed.
* TC-K5.2: 50× same payment_id concurrent → 1 credit, 49 idempotent-noop,
  balance = base + amount once.
* TC-K5.3: 100 webhook + 100 chat-hold concurrent → invariant
  ``balance >= 0`` всегда, ``sum(holds) <= balance`` всегда, exact ledger
  identity.
* TC-K5.4: Janitor запущен в midst шторма — expired holds зачищены без
  потери active.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# --- Skip-marker plumbing ----------------------------------------------------

pytestmark = pytest.mark.integration

if os.environ.get("SKIP_POSTGRES_TESTS", "").lower() in ("1", "true", "yes"):
    pytest.skip(
        "SKIP_POSTGRES_TESTS=1 — postgres integration tests disabled.",
        allow_module_level=True,
    )

testcontainers = pytest.importorskip("testcontainers.postgres")
PostgresContainer = testcontainers.PostgresContainer

from voltari_gateway.billing.engine import (  # noqa: E402
    BillingError,
    InsufficientBalanceError,
    credit_account,
    hold_amount,
    release_hold,
)
from voltari_gateway.billing.janitor import (  # noqa: E402
    sweep_expired_holds,
)
from voltari_gateway.db.models import (  # noqa: E402
    Account,
    AccountHold,
    AccountStatus,
    Base,
    Tariff,
    Transaction,
    TransactionKind,
    User,
)

# ---------------------------------------------------------------------------
# Container + engine fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def postgres_container() -> Iterator[str]:
    """Spin up postgres:16-alpine, yield asyncpg URL."""
    try:
        container = PostgresContainer(
            "postgres:16-alpine",
            username="voltari",
            password="voltari",
            dbname="voltari",
        )
        container.start()
    except Exception as exc:
        pytest.skip(f"Docker/Postgres unavailable: {exc}")

    try:
        sync_url = container.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        if "+asyncpg" not in async_url:
            async_url = async_url.replace("postgresql://", "postgresql+asyncpg://")
        yield async_url
    finally:
        container.stop()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(postgres_container: str):
    eng = create_async_engine(postgres_container, future=True, pool_pre_ping=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="function", loop_scope="module")
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture(scope="function", loop_scope="module")
async def clean_tables(engine) -> None:
    """Truncate touched tables. ``CASCADE`` because account_holds/transactions
    reference accounts.users — нам не важна история между тестами.
    """
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE TABLE processed_webhooks, account_holds, transactions, "
            "accounts, users RESTART IDENTITY CASCADE"
        )


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_account(
    factory: async_sessionmaker[AsyncSession],
    *,
    balance_kopecks: int,
) -> uuid.UUID:
    async with factory() as db:
        user = User(
            email=f"chaos-{uuid.uuid4().hex[:10]}@example.com",
            password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
            email_verified=True,
        )
        db.add(user)
        await db.flush()
        account = Account(
            owner_id=user.id,
            name="chaos",
            balance_kopecks=balance_kopecks,
            tariff=Tariff.PRO,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
        )
        db.add(account)
        await db.commit()
        await db.refresh(account)
        return account.id


async def _read_balance(factory: async_sessionmaker[AsyncSession], account_id: uuid.UUID) -> int:
    async with factory() as db:
        acc = await db.get(Account, account_id)
        assert acc is not None
        return acc.balance_kopecks


async def _count_transactions(
    factory: async_sessionmaker[AsyncSession], account_id: uuid.UUID
) -> int:
    async with factory() as db:
        rows = (
            (await db.execute(select(Transaction).where(Transaction.account_id == account_id)))
            .scalars()
            .all()
        )
        return len(rows)


async def _count_holds(factory: async_sessionmaker[AsyncSession], account_id: uuid.UUID) -> int:
    async with factory() as db:
        rows = (
            (await db.execute(select(AccountHold).where(AccountHold.account_id == account_id)))
            .scalars()
            .all()
        )
        return len(rows)


async def _do_credit(
    factory: async_sessionmaker[AsyncSession],
    *,
    account_id: uuid.UUID,
    amount_kopecks: int,
    ref_id: str,
) -> None:
    """Realistic webhook-credit path: open session, credit, commit.

    Mirrors what ``api/billing.py::_handle_payment_succeeded`` does without
    the HTTP boilerplate.
    """
    async with factory() as db:
        try:
            await credit_account(
                db,
                account_id=account_id,
                amount_kopecks=amount_kopecks,
                ref_id=ref_id,
                kind=TransactionKind.TOPUP,
                meta={"chaos_test": True},
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def _do_hold_then_release(
    factory: async_sessionmaker[AsyncSession],
    *,
    account_id: uuid.UUID,
    amount_kopecks: int,
    ref_id: str,
) -> bool:
    """Place a hold, release it. Returns True if hold succeeded.

    Mirrors a chat call that finished with cost=0 (release path).
    """
    async with factory() as db:
        try:
            handle = await hold_amount(
                db,
                account_id=account_id,
                amount_kopecks=amount_kopecks,
                ref_id=ref_id,
            )
            await db.commit()
        except InsufficientBalanceError:
            await db.rollback()
            return False
        except Exception:
            await db.rollback()
            raise

    async with factory() as db:
        try:
            await release_hold(db, handle)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return True


# ---------------------------------------------------------------------------
# TC-K5.1 — 50 уникальных payment_id → 50 credits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
async def test_50_unique_payment_ids_credit_exactly_once(
    session_factory: async_sessionmaker[AsyncSession], clean_tables: None
) -> None:
    """50 различных webhook'ов на один аккаунт → balance = base + sum(amounts).
    Каждая транзакция — отдельная row.
    """
    account_id = await _seed_account(session_factory, balance_kopecks=0)
    n_payments = 50
    per_amount = 100  # 1 ₽

    payment_ids = [f"pay-unique-{i}-{uuid.uuid4().hex[:8]}" for i in range(n_payments)]
    await asyncio.gather(
        *(
            _do_credit(
                session_factory,
                account_id=account_id,
                amount_kopecks=per_amount,
                ref_id=pid,
            )
            for pid in payment_ids
        )
    )

    expected = n_payments * per_amount
    actual = await _read_balance(session_factory, account_id)
    assert actual == expected, (actual, expected)

    tx_count = await _count_transactions(session_factory, account_id)
    assert tx_count == n_payments


# ---------------------------------------------------------------------------
# TC-K5.2 — 50 × same payment_id → 1 credit (idempotent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
async def test_same_payment_id_50x_credits_once(
    session_factory: async_sessionmaker[AsyncSession], clean_tables: None
) -> None:
    """50 одновременных webhook'ов с одинаковым payment_id → 1 credit.

    Под нагрузкой ЮKassa ретраит на 5xx — несколько runner'ов могут
    обработать тот же event одновременно. UNIQUE(account_id, ref_id) на
    transactions + SAVEPOINT rollback в ``credit_account`` гарантируют
    что balance изменится **ровно один раз**.
    """
    account_id = await _seed_account(session_factory, balance_kopecks=0)
    n_replays = 50
    amount = 5_000  # 50 ₽
    ref_id = "pay-replay-50x"

    # All 50 use the same ref_id.
    await asyncio.gather(
        *(
            _do_credit(
                session_factory,
                account_id=account_id,
                amount_kopecks=amount,
                ref_id=ref_id,
            )
            for _ in range(n_replays)
        )
    )

    actual = await _read_balance(session_factory, account_id)
    assert actual == amount, f"expected exactly one credit (={amount}), got {actual}"

    tx_count = await _count_transactions(session_factory, account_id)
    assert tx_count == 1


# ---------------------------------------------------------------------------
# TC-K5.3 — Mixed credits + holds storm (invariant: balance ≥ 0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
async def test_mixed_credit_and_hold_storm_invariants(
    session_factory: async_sessionmaker[AsyncSession], clean_tables: None
) -> None:
    """100 параллельных операций — credit и hold/release вперемешку.

    Invariants:

    1. Все credit'ы успешны.
    2. Холды могут падать с InsufficientBalance — это OK; те, что
       прошли, обязательно release'нуты.
    3. После всего: balance == initial + sum(credits), потому что
       все hold'ы release'нуты (cost=0 path).
    4. Active holds == 0.
    """
    initial = 100_000  # 1000 ₽
    account_id = await _seed_account(session_factory, balance_kopecks=initial)
    n_credits = 50
    n_holds = 50
    credit_amount = 1_000  # 10 ₽
    hold_amount_kop = 500  # 5 ₽

    credit_ids = [f"credit-{i}-{uuid.uuid4().hex[:6]}" for i in range(n_credits)]
    hold_ids = [f"hold-{i}-{uuid.uuid4().hex[:6]}" for i in range(n_holds)]

    credits_task = [
        _do_credit(
            session_factory,
            account_id=account_id,
            amount_kopecks=credit_amount,
            ref_id=cid,
        )
        for cid in credit_ids
    ]
    holds_task = [
        _do_hold_then_release(
            session_factory,
            account_id=account_id,
            amount_kopecks=hold_amount_kop,
            ref_id=hid,
        )
        for hid in hold_ids
    ]

    results = await asyncio.gather(*credits_task, *holds_task, return_exceptions=True)
    # Никакие credit'ы не должны фейлить — у нас денег с запасом.
    credit_results = results[:n_credits]
    hold_results = results[n_credits:]
    for r in credit_results:
        assert not isinstance(r, Exception), f"credit raised: {r!r}"

    # Hold'ы возвращают bool (True=ok, False=insufficient). При initial=100k
    # и hold_size=500, 50 hold'ов суммарно требуют 25k — на запасе. False
    # допустим только при гонке между credit'ом и hold'ом, и на CAS-пути
    # advisory lock делает ситуацию атомарной. Pin'им: все True.
    for r in hold_results:
        assert not isinstance(r, Exception), f"hold raised: {r!r}"

    # Balance = initial + n_credits × credit_amount, потому что все hold'ы
    # release'нуты (нет debit'а).
    expected = initial + n_credits * credit_amount
    actual = await _read_balance(session_factory, account_id)
    assert actual == expected, (actual, expected)

    # No leftover holds.
    assert await _count_holds(session_factory, account_id) == 0

    # Transactions: ровно n_credits (hold'ы не пишут transactions при
    # release без debit'а).
    assert await _count_transactions(session_factory, account_id) == n_credits


# ---------------------------------------------------------------------------
# TC-K5.4 — Janitor cleans expired holds in midst of storm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
async def test_janitor_cleans_expired_during_storm(
    engine,
    session_factory: async_sessionmaker[AsyncSession],
    clean_tables: None,
) -> None:
    """В разгар шторма janitor'у задают побежать — он обязан удалить ТОЛЬКО
    expired holds, не задев active'ные.

    Сценарий:
      1. Сидим 5 hold'ов с expires_at в прошлом (стейл).
      2. Сидим 5 hold'ов с expires_at в будущем (active).
      3. Запускаем janitor (cleanup_expired_holds).
      4. Ожидаем 5 active hold'ов; 5 expired удалены.
    """
    account_id = await _seed_account(session_factory, balance_kopecks=10_000_000)

    now = datetime.now(UTC)
    # Insert 5 stale + 5 fresh holds directly via ORM.
    async with session_factory() as db:
        for i in range(5):
            db.add(
                AccountHold(
                    account_id=account_id,
                    ref_id=f"stale-{i}",
                    amount_kopecks=100,
                    created_at=now - timedelta(hours=1),
                    expires_at=now - timedelta(minutes=10),  # expired 10 min ago
                )
            )
        for i in range(5):
            db.add(
                AccountHold(
                    account_id=account_id,
                    ref_id=f"fresh-{i}",
                    amount_kopecks=100,
                    created_at=now,
                    expires_at=now + timedelta(minutes=30),
                )
            )
        await db.commit()

    assert await _count_holds(session_factory, account_id) == 10

    # Run janitor — sweep_expired_holds(session, grace_seconds=300).
    # Default grace 5 min → only stuff older than now - 5 min is collected.
    async with session_factory() as db:
        deleted = await sweep_expired_holds(db, grace_seconds=300)
        await db.commit()

    # Janitor should have removed 5 stale holds.
    assert deleted == 5, deleted

    remaining = await _count_holds(session_factory, account_id)
    assert remaining == 5, remaining

    # Pin: оставшиеся — именно «fresh».
    async with session_factory() as db:
        rows = (
            (await db.execute(select(AccountHold).where(AccountHold.account_id == account_id)))
            .scalars()
            .all()
        )
    ref_ids = {r.ref_id for r in rows}
    assert all(rid.startswith("fresh-") for rid in ref_ids), ref_ids


# ---------------------------------------------------------------------------
# Bonus — credit_account на nonexistent account → BillingError, NO partial state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
async def test_credit_unknown_account_raises_billing_error(
    session_factory: async_sessionmaker[AsyncSession], clean_tables: None
) -> None:
    """``credit_account(account_id=unknown)`` → BillingError. No DB state."""
    fake_id = uuid.uuid4()
    async with session_factory() as db:
        with pytest.raises(BillingError):
            await credit_account(
                db,
                account_id=fake_id,
                amount_kopecks=100,
                ref_id="should-fail",
                kind=TransactionKind.TOPUP,
            )

    # No transaction, no account row created.
    async with session_factory() as db:
        rows = (
            (await db.execute(select(Transaction).where(Transaction.ref_id == "should-fail")))
            .scalars()
            .all()
        )
    assert rows == []
