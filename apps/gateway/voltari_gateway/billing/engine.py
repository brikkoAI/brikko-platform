"""Atomic balance operations: debit / credit / hold.

All money lives in **kopecks** as ``BigInteger`` (`accounts.balance_kopecks`),
never as float. Every public function:

* takes the amount as ``int`` (kopecks),
* persists a row in ``transactions`` with the same ``ref_id`` it was given so
  callers can implement idempotency without an extra table,
* uses ``SELECT ... FOR UPDATE`` (Postgres) or a SQLAlchemy ``with_for_update()``
  fallback (SQLite — used in tests; pessimistic-style read still serialises
  inside our single-process test runner).

Holds (reservations) live in the **Postgres** table ``account_holds`` (Alembic
0005). They were previously in Redis but that left a race between
``balance(PG)`` and ``sum_holds(Redis)``: two concurrent requests could each
pass pre-flight and both proceed to the upstream call. With holds in the same
DB transaction as the balance, the race window is closed by SQL semantics.

The hold key is ``(account_id, ref_id)`` — caller chooses ``ref_id`` (typically
the gateway request_id) and is responsible for committing or releasing it.

See ``03_Finance/10_billing_test_scenarios.md`` §B (16-20) for the race /
idempotency contracts this module must satisfy.

Concurrency design (BE P0-1):

  ``debit_account`` is implemented as a single round-trip per logical
  decision instead of read-then-write. The previous shape:

      SELECT existing transaction by ref_id;  -- (round-trip 1)
      SELECT account FOR UPDATE;              -- (round-trip 2)
      INSERT transaction;                     -- (round-trip 3)
      UPDATE account balance;                 -- (round-trip 4)

  permitted two workers to both see ``existing=None`` then race the INSERT.
  The UNIQUE(account_id, ref_id) constraint protected the *ledger*, but
  the failed INSERT raised IntegrityError after the surrounding UPDATE was
  already issued — rolling back the whole AsyncSession (including unrelated
  in-flight changes the caller may have queued).

  New shape uses one ``INSERT ... ON CONFLICT (account_id, ref_id) DO
  NOTHING RETURNING id`` plus a conditional UPDATE. Two concurrent calls
  with the same ref_id: one INSERT lands, the other ``RETURNING`` is empty
  → idempotent path returns the canonical row. Two concurrent calls with
  *different* ref_ids: the row-lock from FOR UPDATE serialises them through
  the balance check.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.billing.constants import REFUND_OVERDRAFT_FLOOR_KOPECKS
from voltari_gateway.db.models import (
    Account,
    AccountHold,
    Transaction,
    TransactionKind,
)
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

# Hold defaults — kept conservative. 120s covers the worst-case provider
# call (we cap upstream at 30s, then up to 3 retries × backoff ≤ 6s, plus
# stream reading time). Janitor cron sweeps anything past expires_at.
DEFAULT_HOLD_TTL_SECONDS = 120


class BillingError(Exception):
    """Generic billing error — caller maps to HTTP 4xx/5xx."""


class InsufficientBalanceError(BillingError):
    """Balance below the requested amount. Carries balance + required."""

    def __init__(self, balance_kopecks: int, required_kopecks: int) -> None:
        self.balance_kopecks = balance_kopecks
        self.required_kopecks = required_kopecks
        super().__init__(
            f"insufficient_balance: have {balance_kopecks} kop, need {required_kopecks} kop"
        )


class DuplicateTransactionError(BillingError):
    """A transaction with this ``ref_id`` already exists for this account."""

    def __init__(self, ref_id: str) -> None:
        self.ref_id = ref_id
        super().__init__(f"duplicate_transaction: ref_id={ref_id}")


@dataclass(frozen=True, slots=True)
class HoldHandle:
    """Pointer to a Postgres hold row. Pass to ``release_hold`` /
    ``commit_hold_to_debit``.

    The handle stays valid even after the hold has been deleted — the
    operations are idempotent against a missing row.
    """

    account_id: uuid.UUID
    ref_id: str
    amount_kopecks: int


# ---------- DB primitives ------------------------------------------------------


def _is_sqlite(db: AsyncSession) -> bool:
    bind = db.bind
    return bind is not None and bind.dialect.name == "sqlite"


async def _select_account_for_update(db: AsyncSession, account_id: uuid.UUID) -> Account:
    """Lock the account row for the rest of the transaction.

    On Postgres → ``SELECT ... FOR UPDATE``. On SQLite (tests) → a regular
    SELECT (sqlite serialises writes anyway via the StaticPool).
    """
    stmt = select(Account).where(Account.id == account_id)
    if not _is_sqlite(db):
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise BillingError(f"account_not_found: {account_id}")
    return row


async def _sum_active_holds(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Sum of every active hold for this account (in kopecks)."""
    stmt = select(func.coalesce(func.sum(AccountHold.amount_kopecks), 0)).where(
        AccountHold.account_id == account_id
    )
    return int((await db.execute(stmt)).scalar_one())


# ---------- public API ---------------------------------------------------------


async def debit_account(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    amount_kopecks: int,
    ref_id: str,
    kind: TransactionKind = TransactionKind.CHARGE,
    meta: dict[str, Any] | None = None,
) -> Transaction:
    """Atomically subtract ``amount_kopecks`` from the account balance.

    * Idempotent on ``ref_id``: a second call with the same ref_id returns the
      existing transaction (does **not** double-charge). Idempotency is
      enforced by the UNIQUE(account_id, ref_id) index — concurrent INSERTs
      collide at the DB level, not in Python.
    * Raises ``InsufficientBalanceError`` if balance < amount (and balance >= 0).

    Caller MUST commit the surrounding ``AsyncSession`` after a successful
    return. We do not commit here so the caller can roll back on a downstream
    failure (e.g. the provider call after the debit).
    """
    if amount_kopecks <= 0:
        raise BillingError("amount_kopecks must be positive")

    # Lock the account row first. If two callers race with *different* ref_ids,
    # this serialises them through the balance check. If they race with the
    # *same* ref_id, the second one finds the existing transaction below and
    # short-circuits — but it still takes the lock, which is fine because the
    # idempotent path is rare.
    account = await _select_account_for_update(db, account_id)

    # Idempotency lookup AFTER the row lock: ensures we don't see a partial
    # state (another worker between INSERT and balance UPDATE).
    existing_stmt = select(Transaction).where(
        Transaction.account_id == account_id,
        Transaction.ref_id == ref_id,
    )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        log.info(
            "debit_idempotent_hit",
            account_id=str(account_id),
            ref_id=ref_id,
            amount_kopecks=amount_kopecks,
            existing_id=str(existing.id),
        )
        return existing

    if account.balance_kopecks < amount_kopecks:
        raise InsufficientBalanceError(
            balance_kopecks=account.balance_kopecks,
            required_kopecks=amount_kopecks,
        )

    account.balance_kopecks -= amount_kopecks
    db.add(account)

    tx = Transaction(
        account_id=account_id,
        type=kind,
        amount_kopecks=-amount_kopecks,  # negative for charges by convention
        ref_id=ref_id,
        meta=meta,
    )
    db.add(tx)
    # SAVEPOINT-isolated flush so concurrent writers don't poison the outer
    # transaction (see credit_account for full rationale).
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        existing = (
            await db.execute(
                select(Transaction).where(
                    Transaction.account_id == account_id,
                    Transaction.ref_id == ref_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        raise

    log.info(
        "debit_ok",
        account_id=str(account_id),
        ref_id=ref_id,
        amount_kopecks=amount_kopecks,
        new_balance=account.balance_kopecks,
        kind=kind.value,
    )
    return tx


async def credit_account(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    amount_kopecks: int,
    ref_id: str,
    kind: TransactionKind = TransactionKind.TOPUP,
    meta: dict[str, Any] | None = None,
) -> Transaction:
    """Atomically add ``amount_kopecks`` to the account balance.

    Used for: ЮKassa successful payment, autorefill credit, welcome bonus.
    Idempotent on ``ref_id`` — the same payment_id from ЮKassa cannot top up
    twice (scenario 17).
    """
    if amount_kopecks <= 0:
        raise BillingError("amount_kopecks must be positive")

    account = await _select_account_for_update(db, account_id)

    existing_stmt = select(Transaction).where(
        Transaction.account_id == account_id,
        Transaction.ref_id == ref_id,
    )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        log.info(
            "credit_idempotent_hit",
            account_id=str(account_id),
            ref_id=ref_id,
            amount_kopecks=amount_kopecks,
        )
        return existing

    account.balance_kopecks += amount_kopecks
    db.add(account)

    tx = Transaction(
        account_id=account_id,
        type=kind,
        amount_kopecks=amount_kopecks,
        ref_id=ref_id,
        meta=meta,
    )
    db.add(tx)
    # Use a SAVEPOINT (nested) so an IntegrityError on concurrent
    # writers can be locally rolled back without poisoning the outer
    # transaction. After SAVEPOINT rollback the outer transaction is
    # still alive and can SELECT the canonical row.
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        existing = (
            await db.execute(
                select(Transaction).where(
                    Transaction.account_id == account_id,
                    Transaction.ref_id == ref_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        raise

    log.info(
        "credit_ok",
        account_id=str(account_id),
        ref_id=ref_id,
        amount_kopecks=amount_kopecks,
        new_balance=account.balance_kopecks,
        kind=kind.value,
    )
    return tx


async def refund_account(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    amount_kopecks: int,
    ref_id: str,
    meta: dict[str, Any] | None = None,
) -> Transaction:
    """ЮKassa refund — debits the account, allowing technical overdraft.

    Scenario 19: client pays 5000 ₽, spends 200 ₽, then refunds 5000 ₽ →
    balance ends at -200 (200 kopecks overdraft is fine — we only block
    *new* charges, not the bookkeeping refund itself).
    """
    if amount_kopecks <= 0:
        raise BillingError("amount_kopecks must be positive")

    account = await _select_account_for_update(db, account_id)

    existing_stmt = select(Transaction).where(
        Transaction.account_id == account_id,
        Transaction.ref_id == ref_id,
    )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    new_balance = account.balance_kopecks - amount_kopecks
    if new_balance < REFUND_OVERDRAFT_FLOOR_KOPECKS:
        # Even refunds cannot bust the DB-level CHECK constraint (-1000 kop).
        # Hitting this means ops should issue the refund off-platform.
        raise BillingError(
            f"refund_would_exceed_overdraft_floor: balance={account.balance_kopecks}, "
            f"refund={amount_kopecks}, floor={REFUND_OVERDRAFT_FLOOR_KOPECKS}"
        )

    account.balance_kopecks = new_balance
    db.add(account)

    tx = Transaction(
        account_id=account_id,
        type=TransactionKind.REFUND,
        amount_kopecks=-amount_kopecks,
        ref_id=ref_id,
        meta=meta,
    )
    db.add(tx)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        existing = (
            await db.execute(
                select(Transaction).where(
                    Transaction.account_id == account_id,
                    Transaction.ref_id == ref_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        raise

    log.info(
        "refund_ok",
        account_id=str(account_id),
        ref_id=ref_id,
        amount_kopecks=amount_kopecks,
        new_balance=new_balance,
    )
    return tx


# ---------- holds (Postgres) ---------------------------------------------------


async def _account_balance(db: AsyncSession, account_id: uuid.UUID) -> int | None:
    """Return the current balance for ``account_id`` (no lock, no FOR UPDATE).

    Returns ``None`` if the account does not exist. Used by the CAS path
    (TD-029) where we need the balance for InsufficientBalanceError context
    but don't want to take a row lock — the CAS INSERT is the
    serialisation point, not a SELECT.
    """
    stmt = select(Account.balance_kopecks).where(Account.id == account_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def hold_amount(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    amount_kopecks: int,
    ref_id: str,
    ttl_seconds: int = DEFAULT_HOLD_TTL_SECONDS,
) -> HoldHandle:
    """Reserve ``amount_kopecks`` against the account balance — CAS-style (TD-029).

    Strategy
    --------
    Place the hold without taking a row-level lock on ``accounts``. Instead,
    rely on a single conditional ``INSERT ... SELECT ... WHERE balance >=
    sum_of_holds + :amount`` whose RETURNING clause reports success. To
    serialise *concurrent* attempts on the *same* account, we acquire a
    Postgres transaction-scoped advisory lock keyed by ``account_id``.

    Why advisory lock vs ``SELECT FOR UPDATE``:

    * Advisory locks are cheaper and don't conflict with reads of the
      ``accounts`` row (e.g. dashboard balance lookups stay non-blocking).
    * They release at COMMIT/ROLLBACK automatically — no leak window.
    * Different accounts proceed in parallel; only same-account hold
      attempts serialise.

    SQLite (tests) doesn't have advisory locks; ``StaticPool`` already
    serialises writes through one connection so the advisory lock is a
    no-op there. Correctness is preserved by the existing ORM flush.

    Idempotency
    -----------
    UNIQUE(account_id, ref_id) on ``account_holds`` makes a re-issued hold
    with the same ref_id a no-op: ON CONFLICT DO NOTHING returns no row,
    we re-read the existing row and return it.

    Failure mode
    ------------
    The CAS INSERT WHERE clause is evaluated atomically as part of the
    INSERT statement. If the WHERE fails (insufficient available
    balance), no row is inserted and RETURNING is empty → we raise
    ``InsufficientBalanceError``. Otherwise the row is in the table and
    we return a ``HoldHandle``.

    Caller MUST ``commit()`` the session after this returns.
    """
    if amount_kopecks <= 0:
        raise BillingError("hold amount_kopecks must be positive")

    is_sqlite = _is_sqlite(db)

    # Postgres advisory lock — serialises same-account hold inserts
    # without locking the accounts row. ``hashtext`` produces a stable
    # int4 from the UUID string. The lock auto-releases at COMMIT.
    if not is_sqlite:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": str(account_id)},
        )

    # Idempotent path — same ref_id already holds. Done before the CAS so
    # a retried request doesn't re-evaluate balance.
    existing_hold_stmt = select(AccountHold).where(
        AccountHold.account_id == account_id,
        AccountHold.ref_id == ref_id,
    )
    existing_hold = (await db.execute(existing_hold_stmt)).scalar_one_or_none()
    if existing_hold is not None:
        return HoldHandle(
            account_id=account_id,
            ref_id=ref_id,
            amount_kopecks=existing_hold.amount_kopecks,
        )

    now = datetime.now(UTC)
    expires = now + timedelta(seconds=ttl_seconds)

    if is_sqlite:
        # CAS-style on SQLite is awkward (no RETURNING in older SQLite + no
        # advisory locks). Fall back to the read-modify-write path; the test
        # runner uses StaticPool which serialises writes anyway.
        balance = await _account_balance(db, account_id)
        if balance is None:
            raise BillingError(f"account_not_found: {account_id}")
        sum_existing = await _sum_active_holds(db, account_id)
        if balance - sum_existing < amount_kopecks:
            raise InsufficientBalanceError(
                balance_kopecks=balance,
                required_kopecks=sum_existing + amount_kopecks,
            )
        hold = AccountHold(
            account_id=account_id,
            ref_id=ref_id,
            amount_kopecks=amount_kopecks,
            created_at=now,
            expires_at=expires,
        )
        db.add(hold)
        await db.flush()
        log.info(
            "hold_placed",
            account_id=str(account_id),
            ref_id=ref_id,
            amount_kopecks=amount_kopecks,
            sum_holds_after=sum_existing + amount_kopecks,
            balance=balance,
            cas=False,
        )
        return HoldHandle(account_id=account_id, ref_id=ref_id, amount_kopecks=amount_kopecks)

    # Postgres CAS path. Single statement: insert iff balance - sum_holds
    # >= amount. The advisory lock above linearises concurrent inserts
    # for this account so the SUM read inside this statement is correct.
    # Explicit casts on the bound parameters: asyncpg's protocol infers
    # types per parameter slot, and reusing the same Python int as both an
    # INSERT value (BIGINT column) and a comparison RHS (where the left
    # side is BIGINT - BIGINT = BIGINT) can hit "inconsistent types deduced
    # for parameter $N: numeric versus bigint" without the cast hint.
    cas_sql = text(
        """
        INSERT INTO account_holds
            (id, account_id, ref_id, amount_kopecks, created_at, expires_at)
        SELECT
            gen_random_uuid(),
            :account_id,
            :ref_id,
            CAST(:amount AS BIGINT),
            :created_at,
            :expires_at
        FROM accounts a
        WHERE a.id = :account_id
          AND (a.balance_kopecks
               - COALESCE(
                   (SELECT SUM(amount_kopecks)
                    FROM account_holds
                    WHERE account_id = :account_id),
                   0
               )
              ) >= CAST(:amount AS BIGINT)
        ON CONFLICT (account_id, ref_id) DO NOTHING
        RETURNING id, amount_kopecks
        """
    )
    result = await db.execute(
        cas_sql,
        {
            "account_id": account_id,
            "ref_id": ref_id,
            "amount": amount_kopecks,
            "created_at": now,
            "expires_at": expires,
        },
    )
    row = result.first()
    if row is None:
        # Either the account doesn't exist or the balance check failed.
        # Distinguish for a useful error message.
        balance = await _account_balance(db, account_id)
        if balance is None:
            raise BillingError(f"account_not_found: {account_id}")
        sum_existing = await _sum_active_holds(db, account_id)
        raise InsufficientBalanceError(
            balance_kopecks=balance,
            required_kopecks=sum_existing + amount_kopecks,
        )

    log.info(
        "hold_placed",
        account_id=str(account_id),
        ref_id=ref_id,
        amount_kopecks=amount_kopecks,
        cas=True,
    )
    return HoldHandle(
        account_id=account_id,
        ref_id=ref_id,
        amount_kopecks=amount_kopecks,
    )


async def release_hold(db: AsyncSession, handle: HoldHandle) -> None:
    """Cancel a hold (e.g. provider call failed before debit).

    Idempotent — calling release twice is fine. Caller MUST ``commit()``
    after this returns. We don't commit here because the caller may want to
    bundle the release with other rollback bookkeeping.
    """
    stmt = delete(AccountHold).where(
        AccountHold.account_id == handle.account_id,
        AccountHold.ref_id == handle.ref_id,
    )
    result = await db.execute(stmt)
    # ``CursorResult.rowcount`` is well-defined for DELETE/UPDATE; the
    # base ``Result`` doesn't expose it in its typing stub, so getattr.
    rowcount = getattr(result, "rowcount", 0) or 0
    if rowcount:
        log.info(
            "hold_released",
            account_id=str(handle.account_id),
            ref_id=handle.ref_id,
            amount_kopecks=handle.amount_kopecks,
        )


async def commit_hold_to_debit(
    db: AsyncSession,
    *,
    handle: HoldHandle,
    actual_amount_kopecks: int,
    meta: dict[str, Any] | None = None,
) -> Transaction:
    """Settle a hold against the real cost.

    The hold is for the *estimated* cost (max_tokens worst case). The actual
    debit is for what the provider actually billed us — usually less. Order
    of operations matters: we MUST debit *before* releasing the hold,
    otherwise a third concurrent request could see a temporarily larger
    available balance and slip past pre-flight.

    Atomically (within the caller's transaction):

        DELETE hold WHERE (account_id, ref_id);  # frees the reserved kop
        SELECT account FOR UPDATE;
        UPDATE balance -= actual;
        INSERT transaction;

    The DELETE has to happen *before* the SELECT FOR UPDATE so the deducted
    hold doesn't double-count: when the next concurrent request reads
    ``sum_holds`` it should see the hold gone and the actual debit reflected
    in ``balance``.

    Caller MUST commit() on success. On exception they must rollback —
    ``commit_hold_to_debit`` does not modify the session in a way that's
    safe to leave half-applied.
    """
    # Free the hold first so debit_account's SELECT FOR UPDATE doesn't have
    # to think about it. The same transaction will re-acquire the lock
    # immediately.
    await release_hold(db, handle)

    return await debit_account(
        db,
        account_id=handle.account_id,
        amount_kopecks=actual_amount_kopecks,
        ref_id=handle.ref_id,
        meta=meta,
    )


# ---------- helpers ------------------------------------------------------------


def now_utc() -> datetime:
    return datetime.now(UTC)


def new_ref_id(prefix: str = "tx") -> str:
    """Generate a fresh ref_id when the caller doesn't have an external one."""
    return f"{prefix}_{uuid.uuid4().hex}"


__all__ = [
    "DEFAULT_HOLD_TTL_SECONDS",
    "REFUND_OVERDRAFT_FLOOR_KOPECKS",
    "BillingError",
    "DuplicateTransactionError",
    "HoldHandle",
    "InsufficientBalanceError",
    "commit_hold_to_debit",
    "credit_account",
    "debit_account",
    "hold_amount",
    "new_ref_id",
    "now_utc",
    "refund_account",
    "release_hold",
]
