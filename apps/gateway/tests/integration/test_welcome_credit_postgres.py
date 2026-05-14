"""Integration tests for the welcome-credit dedup against real PostgreSQL.

The unit tests in ``tests/auth/test_email_verification.py`` exercise the
SQLite path (``IntegrityError`` rollback). On Postgres we want to confirm
the SAME function behaves correctly with the real lock-and-conflict
semantics: a duplicate insert on the ``email_hash`` PK raises
``UniqueViolationError`` (which SQLAlchemy wraps as ``IntegrityError``)
and concurrent inserts serialise such that exactly one wins.

Skip strategy:
* ``SKIP_POSTGRES_TESTS=1`` in env  → skipped (CI without Docker, local dev).
* Docker not reachable             → skipped via ``pytest.skip`` at module import.

Runtime: ~10s for the whole module on a warm Docker daemon (cold pull
of postgres:16 adds ~30s the first time).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
PostgresContainer = testcontainers.PostgresContainer  # type: ignore[attr-defined]

from voltari_gateway.db.models import (  # noqa: E402
    Account,
    AccountStatus,
    Base,
    Tariff,
    Transaction,
    TransactionKind,
    User,
    WelcomeCreditsLog,
)

WELCOME_CREDIT_KOPECKS = 20_000  # 200 ₽


# --- Container + engine fixtures --------------------------------------------


@pytest.fixture(scope="module")
def postgres_container() -> AsyncIterator[str]:
    """Spin up postgres:16, yield an ``asyncpg`` URL, tear it down."""
    try:
        container = PostgresContainer(
            "postgres:16-alpine", username="voltari", password="voltari", dbname="voltari"
        )
        container.start()
    except Exception as exc:  # docker daemon down, image pull failed, etc.
        pytest.skip(f"Docker/Postgres unavailable: {exc}")

    try:
        # testcontainers gives us a ``postgresql+psycopg2://`` URL; rewrite
        # to the asyncpg driver our app uses.
        sync_url = container.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        if "+asyncpg" not in async_url:
            async_url = async_url.replace("postgresql://", "postgresql+asyncpg://")
        yield async_url
    finally:
        container.stop()


# pytest-asyncio 0.23+ требует, чтобы fixture'ы с зависимостью на module-scope
# async fixture использовали тот же event loop. Без `loop_scope="module"` на
# function-scope fixtures получаем "Future attached to a different loop" —
# каждая function-fixture создаёт свой loop, не совместимый с engine'овым.
# См. https://pytest-asyncio.readthedocs.io/en/latest/how-to-guides/run_module_tests_in_same_loop.html
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
    """Truncate the rows we touch between tests so ordering is irrelevant."""
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE TABLE transactions, welcome_credits_log, accounts, users "
            "RESTART IDENTITY CASCADE"
        )


# --- Helpers ----------------------------------------------------------------


def _email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


async def _seed_user(db: AsyncSession, email: str) -> tuple[User, Account]:
    user = User(
        email=email,
        password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name="primary",
        balance_kopecks=0,
        tariff=Tariff.PAYG,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
    )
    db.add(account)
    await db.commit()
    await db.refresh(user)
    await db.refresh(account)
    return user, account


async def _grant_welcome_credit(
    *, factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID, email: str
) -> bool:
    """Mirror the production path in ``api/auth.py::_try_grant_welcome_credit``.

    Each call uses its own session/transaction so concurrency tests reflect
    real request-isolation semantics.
    """
    eh = _email_hash(email)
    async with factory() as db:
        try:
            db.add(
                WelcomeCreditsLog(
                    email_hash=eh,
                    ip_hash=None,
                    granted_at=datetime.now(UTC),
                )
            )
            await db.flush()
        except IntegrityError:
            await db.rollback()
            return False
        # Top up account.
        account = (
            await db.execute(select(Account).where(Account.owner_id == user_id))
        ).scalar_one()
        account.balance_kopecks = (account.balance_kopecks or 0) + WELCOME_CREDIT_KOPECKS
        db.add(
            Transaction(
                account_id=account.id,
                type=TransactionKind.TOPUP,
                amount_kopecks=WELCOME_CREDIT_KOPECKS,
                ref_id=f"welcome:{user_id}",
                meta={"kind": "welcome", "email_hash": eh},
            )
        )
        await db.commit()
        return True


# --- Tests ------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="module")
async def test_first_grant_credits_account(
    session_factory: async_sessionmaker[AsyncSession], clean_tables: None
) -> None:
    """Happy path: first call inserts the dedup row and tops up by 200 ₽."""
    email = "alice@example.com"
    async with session_factory() as db:
        user, account = await _seed_user(db, email)

    granted = await _grant_welcome_credit(factory=session_factory, user_id=user.id, email=email)
    assert granted is True

    async with session_factory() as db:
        refreshed = (await db.execute(select(Account).where(Account.id == account.id))).scalar_one()
        assert refreshed.balance_kopecks == WELCOME_CREDIT_KOPECKS

        log_row = (
            await db.execute(
                select(WelcomeCreditsLog).where(WelcomeCreditsLog.email_hash == _email_hash(email))
            )
        ).scalar_one()
        assert log_row is not None

        tx_count = (
            await db.execute(select(Transaction).where(Transaction.account_id == account.id))
        ).all()
        assert len(tx_count) == 1


@pytest.mark.asyncio(loop_scope="module")
async def test_second_grant_is_noop(
    session_factory: async_sessionmaker[AsyncSession], clean_tables: None
) -> None:
    """Same email twice: PK conflict caught, balance unchanged after first grant."""
    email = "bob@example.com"
    async with session_factory() as db:
        user, account = await _seed_user(db, email)

    first = await _grant_welcome_credit(factory=session_factory, user_id=user.id, email=email)
    second = await _grant_welcome_credit(factory=session_factory, user_id=user.id, email=email)
    assert first is True
    assert second is False

    async with session_factory() as db:
        refreshed = (await db.execute(select(Account).where(Account.id == account.id))).scalar_one()
        assert refreshed.balance_kopecks == WELCOME_CREDIT_KOPECKS

        # Exactly one log row, exactly one Transaction row.
        log_rows = (await db.execute(select(WelcomeCreditsLog))).all()
        tx_rows = (
            await db.execute(select(Transaction).where(Transaction.account_id == account.id))
        ).all()
        assert len(log_rows) == 1
        assert len(tx_rows) == 1


@pytest.mark.asyncio(loop_scope="module")
async def test_concurrent_grants_only_one_wins(
    session_factory: async_sessionmaker[AsyncSession], clean_tables: None
) -> None:
    """N concurrent inserts → exactly one ``True`` returned, balance = 200 ₽.

    This is the test SQLite literally cannot give us — without
    ``ON CONFLICT`` semantics the loser would deadlock or both could win.
    """
    email = "carol@example.com"
    async with session_factory() as db:
        user, account = await _seed_user(db, email)

    n_parallel = 8
    results = await asyncio.gather(
        *(
            _grant_welcome_credit(factory=session_factory, user_id=user.id, email=email)
            for _ in range(n_parallel)
        ),
        return_exceptions=False,
    )

    wins = sum(1 for r in results if r is True)
    assert wins == 1, f"expected exactly 1 winner, got {wins}: {results}"

    async with session_factory() as db:
        refreshed = (await db.execute(select(Account).where(Account.id == account.id))).scalar_one()
        assert refreshed.balance_kopecks == WELCOME_CREDIT_KOPECKS

        tx_rows = (
            await db.execute(select(Transaction).where(Transaction.account_id == account.id))
        ).all()
        assert len(tx_rows) == 1
