"""Background autorefill loop.

Started from FastAPI ``lifespan``. Every ``ARF_INTERVAL_SECONDS`` (default
300 = 5 minutes) it scans the ``accounts`` table for rows with
``autorefill_enabled = true`` and balance below the per-tariff threshold,
then charges the saved card via ЮKassa recurring API.

Trade-off vs Celery / RQ: a single in-process asyncio task is enough for
solo-stage. The state (last-run, last-attempt, lock) lives in Redis; if the
process dies between attempts, the next instance picks up cleanly. When we
hit ~50 accounts/min on this loop, migrate to a separate worker.

Concurrency: a Redis lock (``billing:autorefill:lock``) ensures that even if
two app replicas run simultaneously, only one fires the cron tick. TTL is
long enough that a crashed worker doesn't deadlock the loop forever.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voltari_gateway.billing.engine import credit_account
from voltari_gateway.billing.yookassa import YooKassaClient, YooKassaError
from voltari_gateway.db.models import Account, Tariff, TransactionKind
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

ARF_LOCK_KEY = "billing:autorefill:lock"
ARF_LOCK_TTL_SECONDS = 240
ARF_DEFAULT_INTERVAL_SECONDS = 300
ARF_BACKOFF_KEY = "billing:autorefill:backoff:{account_id}"
ARF_MAX_FAILURES_PER_DAY = 3


@dataclass
class AutorefillSettings:
    """Per-account fields. Stored in Account JSON column ``autorefill_meta``
    (added by Alembic 002). The flag itself is a typed column.

    * ``enabled``: master switch (column ``autorefill_enabled``).
    * ``payment_method_id``: ЮKassa saved-card handle (column ``autorefill_pm_id``).
    * ``threshold_kopecks``: refill when balance drops below this.
    * ``topup_kopecks``: how much to charge on each refill.
    """

    enabled: bool
    payment_method_id: str | None
    threshold_kopecks: int
    topup_kopecks: int


def _settings_from_account(account: Account) -> AutorefillSettings:
    """Project Account → AutorefillSettings.

    Defaults (per tariff) match ``02_pricing_tiers.md`` minimum top-up:
    PAYG=500₽, Pro/Team/Business=2000₽.
    """
    threshold_default = {
        Tariff.PAYG: 100_00,  # 100 ₽
        Tariff.PRO: 500_00,  # 500 ₽
        Tariff.TEAM: 1_000_00,  # 1 000 ₽
        Tariff.BUSINESS: 5_000_00,
        Tariff.BUSINESS_PLUS: 10_000_00,
    }
    topup_default = {
        Tariff.PAYG: 500_00,
        Tariff.PRO: 2_000_00,
        Tariff.TEAM: 5_000_00,
        Tariff.BUSINESS: 20_000_00,
        Tariff.BUSINESS_PLUS: 50_000_00,
    }
    return AutorefillSettings(
        enabled=bool(getattr(account, "autorefill_enabled", False)),
        payment_method_id=getattr(account, "autorefill_pm_id", None),
        threshold_kopecks=int(
            getattr(account, "autorefill_threshold_kopecks", None)
            or threshold_default[account.tariff]
        ),
        topup_kopecks=int(
            getattr(account, "autorefill_topup_kopecks", None) or topup_default[account.tariff]
        ),
    )


async def _candidate_accounts(db: AsyncSession) -> list[Account]:
    """Find accounts that need a refill *right now*.

    Sprint 5 perf: push the "balance below own threshold" predicate into
    SQL so we don't load every autorefill-enabled account into Python and
    filter there. The previous shape was O(N_enabled) on every tick — fine
    for 50 customers, expensive at 500. The new shape is O(N_due).

    Note: ``autorefill_threshold_kopecks`` is nullable (per-tariff
    fallback in ``_settings_from_account``). We keep the Python-side
    fallback path for rows where the column is NULL so customers who
    didn't customise their threshold still trip on the per-tariff
    default. SQL handles the explicit-threshold case (the common one).
    """
    stmt = select(Account).where(
        Account.autorefill_enabled.is_(True),
        Account.autorefill_pm_id.isnot(None),
    )
    # If a custom threshold was set, filter in SQL — saves a Python pass
    # over the rest of the row set on every tick. Rows with NULL
    # threshold fall through and get the per-tariff default check below.
    sql_filtered = stmt.where(
        (Account.autorefill_threshold_kopecks.is_(None))
        | (Account.balance_kopecks < Account.autorefill_threshold_kopecks)
    )
    rows = (await db.execute(sql_filtered)).scalars().all()
    out: list[Account] = []
    for acc in rows:
        s = _settings_from_account(acc)
        if not s.enabled or not s.payment_method_id:
            continue
        if acc.balance_kopecks < s.threshold_kopecks:
            out.append(acc)
    return out


async def _circuit_open(redis: Redis | None, account_id: uuid.UUID) -> bool:
    """Per-account daily failure counter so we don't hammer ЮKassa on a dead card."""
    if redis is None:
        return False
    key = ARF_BACKOFF_KEY.format(account_id=account_id)
    val = await redis.get(key)
    try:
        n = int(val) if val is not None else 0
    except ValueError:
        n = 0
    return n >= ARF_MAX_FAILURES_PER_DAY


async def _record_failure(redis: Redis | None, account_id: uuid.UUID) -> None:
    if redis is None:
        return
    key = ARF_BACKOFF_KEY.format(account_id=account_id)
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, 24 * 60 * 60)


async def _try_refill_one(
    *,
    db: AsyncSession,
    yookassa: YooKassaClient,
    redis: Redis | None,
    account: Account,
) -> bool:
    s = _settings_from_account(account)
    if not s.payment_method_id:
        return False

    if await _circuit_open(redis, account.id):
        log.info("autorefill_circuit_open", account_id=str(account.id))
        return False

    try:
        result = await yookassa.charge_recurring(
            account_id=account.id,
            amount_kopecks=s.topup_kopecks,
            payment_method_id=s.payment_method_id,
            description="Brikko autorefill",
            metadata={"trigger": "autorefill"},
        )
    except YooKassaError as exc:
        await _record_failure(redis, account.id)
        log.warning(
            "autorefill_yookassa_failed",
            account_id=str(account.id),
            error=str(exc),
        )
        return False

    if result.status not in {"succeeded", "waiting_for_capture"}:
        # "pending" / "canceled" — do not credit yet. Webhook will land later.
        log.info(
            "autorefill_payment_pending",
            account_id=str(account.id),
            payment_id=result.payment_id,
            status=result.status,
        )
        return False

    # Credit immediately if status=succeeded; otherwise let the webhook do it
    # (idempotent on payment_id).
    if result.status == "succeeded":
        try:
            await credit_account(
                db,
                account_id=account.id,
                amount_kopecks=result.amount_kopecks,
                ref_id=result.payment_id,
                kind=TransactionKind.AUTOREFILL,
                meta={"source": "yookassa_recurring"},
            )
            await db.commit()
        except Exception:
            await db.rollback()
            log.exception("autorefill_credit_failed", account_id=str(account.id))
            return False

    log.info(
        "autorefill_ok",
        account_id=str(account.id),
        payment_id=result.payment_id,
        amount_kopecks=result.amount_kopecks,
    )
    return True


async def autorefill_tick(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    yookassa: YooKassaClient,
    redis: Redis | None,
) -> int:
    """One pass of the cron. Returns the number of accounts refilled."""
    refilled = 0
    async with session_factory() as db:
        candidates = await _candidate_accounts(db)
        if not candidates:
            return 0
    for acc in candidates:
        async with session_factory() as db:
            ok = await _try_refill_one(db=db, yookassa=yookassa, redis=redis, account=acc)
            if ok:
                refilled += 1
    return refilled


async def _acquire_lock(redis: Redis | None) -> bool:
    if redis is None:
        return True
    return bool(await redis.set(ARF_LOCK_KEY, "1", nx=True, ex=ARF_LOCK_TTL_SECONDS))


async def autorefill_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    yookassa: YooKassaClient,
    redis: Redis | None,
    interval_seconds: int = ARF_DEFAULT_INTERVAL_SECONDS,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Long-running task that fires ``autorefill_tick`` on an interval.

    Uses a Redis-backed lock so multi-replica deployments only refill once.
    A clean shutdown is achieved by setting ``stop_event``.
    """
    log.info("autorefill_loop_started", interval=interval_seconds)
    while True:
        if stop_event is not None and stop_event.is_set():
            log.info("autorefill_loop_stopped")
            return
        try:
            if await _acquire_lock(redis):
                refilled = await autorefill_tick(
                    session_factory=session_factory,
                    yookassa=yookassa,
                    redis=redis,
                )
                if refilled:
                    log.info("autorefill_tick_done", refilled=refilled)
        except Exception:
            log.exception("autorefill_tick_error")

        try:
            if stop_event is not None:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                return  # stop_event signalled
            else:
                await asyncio.sleep(interval_seconds)
        except TimeoutError:
            continue
