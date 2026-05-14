"""Background cron tasks for account closure (Sprint 7).

Two loops live here, both started by ``main.lifespan``:

* ``account_closure_loop`` — every ~hour. Picks accounts whose 30-day
  grace window has elapsed (``closure_scheduled_at < NOW() AND closed_at
  IS NULL``), flips them to ``CLOSED``, revokes API keys / sessions /
  holds, and refunds any positive balance back to the user (via the
  audit log; we do not push money back to ЮKassa — refund route is
  manual, finance team).

* ``account_pii_purge_loop`` — every ~24 h. Picks accounts where
  ``closed_at < NOW() - 1 year AND pii_purged_at IS NULL``, anonymises
  the user's PII (email → ``deleted+{account_id}@brikko.local``,
  password_hash → empty, telegram_chat_id → NULL), and stamps
  ``pii_purged_at``. Transaction history stays — 152-ФЗ requires keeping
  financial operations five years.

Design notes
------------

* Each loop uses a fresh DB session per tick (it is not request-scoped).
  Errors are logged and the loop continues — a transient outage on one
  tick should not stall closure forever.
* The loops emit Sentry messages on the actual mutation events
  (``account_closed`` / ``account_pii_purged``) — those are high-signal,
  low-volume, exactly what we want in the issues view.
* We are deliberately simple here. No locks, no SKIP LOCKED, no
  per-account Redis backoff. If we ever run multiple workers for the
  closure cron, the audit log idempotency check (``not closed_at``)
  keeps things safe — at worst two workers race, one wins, the other
  no-ops.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import sentry_sdk
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.middleware import invalidate_cache_for_key
from voltari_gateway.auth.session import revoke_all_refresh_tokens
from voltari_gateway.db.models import (
    Account,
    AccountHold,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    Session,
    Transaction,
    TransactionKind,
    User,
)
from voltari_gateway.email.client import render_template, send_email
from voltari_gateway.utils.logging import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = get_logger(__name__)

# Time after which a closed account becomes eligible for the PII purge.
# 152-ФЗ Art. 21 doesn't fix a deadline beyond "without undue delay";
# 1 year is consistent with §13 of the closure email and gives compliance
# enough cushion for tax-audit replays of transaction history.
PII_PURGE_AFTER_DAYS: Final[int] = 365


# ----------------------------------------------------------------------------
# Closure: flip account to CLOSED, revoke credentials.
# ----------------------------------------------------------------------------


async def _close_one_account(
    db: AsyncSession,
    redis: Redis | None,
    *,
    account_id: uuid.UUID,
) -> bool:
    """Close a single account. Returns True if the row was actually closed.

    Idempotent — if another worker already set ``closed_at`` we skip.
    """
    account = await db.get(Account, account_id)
    if account is None:
        return False
    if account.closed_at is not None:
        return False
    if account.closure_scheduled_at is None:
        # Defensive — the SELECT below filters this out, but if a race
        # cancelled the closure between SELECT and UPDATE we should not
        # close the account.
        return False
    sched = account.closure_scheduled_at
    if sched.tzinfo is None:
        sched = sched.replace(tzinfo=UTC)
    if sched > datetime.now(UTC):
        return False

    now = datetime.now(UTC)
    user = await db.get(User, account.owner_id)

    # 1. Revoke every active API key. The bearer-auth Redis cache is keyed
    # by the SHA of the plaintext, which we don't have here — but the
    # secondary index ``auth:key:by_id:<id>`` lets us drop the cache
    # entry without scanning. Safe to call when no entry exists.
    keys = (
        (
            await db.execute(
                select(ApiKey).where(
                    ApiKey.account_id == account_id,
                    ApiKey.status != ApiKeyStatus.REVOKED,
                )
            )
        )
        .scalars()
        .all()
    )
    for key in keys:
        key.status = ApiKeyStatus.REVOKED
        key.revoked_at = now
        await invalidate_cache_for_key(redis, key.id)

    # 2. Revoke every refresh token. The Postgres ``sessions`` mirror is
    # tombstoned via ``revoked_at`` so the dashboard sessions list stops
    # showing them.
    if redis is not None and user is not None:
        try:
            await revoke_all_refresh_tokens(redis, user.id)
        except Exception as exc:
            log.warning("closure_revoke_refresh_failed", error=str(exc))
    # Mirror in PG.
    await db.execute(
        update(Session)
        .where(Session.user_id == account.owner_id, Session.revoked_at.is_(None))
        .values(revoked_at=now)
    )

    # 3. Drop active holds. Releasing them at this point is fine — there
    # is no in-flight provider call we need to settle (the account just
    # lost its API keys).
    await db.execute(delete(AccountHold).where(AccountHold.account_id == account_id))

    # 4. Refund positive balance back to the ledger. We do NOT push money
    # to ЮKassa — that's a manual finance-team action. The transaction
    # row gives accounting an unmistakeable hook ("balance zeroed by
    # closure cron at <ts>") and audit can reconcile against the closure
    # event.
    if account.balance_kopecks > 0:
        refund_amount = account.balance_kopecks
        db.add(
            Transaction(
                account_id=account_id,
                type=TransactionKind.REFUND,
                amount_kopecks=-refund_amount,
                ref_id=f"closure_refund:{account_id}",
                meta={"reason": "account_closed", "closed_at": now.isoformat()},
            )
        )
        account.balance_kopecks = 0

    # 5. Flip account state.
    account.status = AccountStatus.CLOSED
    account.closed_at = now

    await write_audit(
        db,
        user_id=account.owner_id,
        account_id=account_id,
        action="account_closed",
        request=None,
        meta={
            "keys_revoked": len(keys),
            "balance_refunded_kop": refund_amount if account.balance_kopecks == 0 else 0,
        },
        severity="warning",
    )
    await db.commit()

    # 6. Confirmation email — best effort.
    if user is not None:
        try:
            body = render_template(
                "account_closure_confirmed.txt",
                requested_at=now.isoformat(),
                closure_at=now.isoformat(),
                settings_security_url="https://brikko.ru/closed",
            )
            await send_email(
                to=user.email,
                subject="Аккаунт Brikko закрыт",
                body=body,
            )
        except Exception as exc:
            log.warning("closure_complete_email_failed", error=str(exc))

    # 7. Sentry — high-signal event, low frequency. Burns roughly one
    # message per closed account, easily under free-tier budget.
    with contextlib.suppress(Exception):  # Sentry-down must not break us
        sentry_sdk.capture_message(
            f"account_closed:{account_id}",
            level="warning",
        )

    return True


async def process_account_closures(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    redis: Redis | None,
) -> int:
    """One sweep: close every account whose grace window expired.

    Returns the number of accounts closed in this run.
    """
    closed = 0
    async with session_factory() as session:
        # Selecting only IDs upfront and looping with per-account commits
        # keeps the transaction footprint small. With 10k closures pending
        # we'd rather fail mid-batch on row 5000 than hold a 10-minute
        # transaction.
        rows = await session.execute(
            select(Account.id).where(
                Account.closure_scheduled_at.is_not(None),
                Account.closed_at.is_(None),
                Account.closure_scheduled_at < datetime.now(UTC),
            )
        )
        account_ids = [r[0] for r in rows.all()]

    for account_id in account_ids:
        async with session_factory() as session:
            try:
                done = await _close_one_account(session, redis, account_id=account_id)
                if done:
                    closed += 1
            except Exception as exc:
                # Per-account failure: log and move on. Next tick retries.
                await session.rollback()
                log.exception(
                    "account_closure_failed",
                    account_id=str(account_id),
                    error=str(exc),
                )
    return closed


async def account_closure_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis | None,
    interval_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """Long-running closure sweeper. Cancellable via ``stop_event``."""
    log.info("account_closure_loop_started", interval_seconds=interval_seconds)
    while not stop_event.is_set():
        try:
            closed = await process_account_closures(session_factory, redis=redis)
            if closed:
                log.info("account_closure_tick", accounts_closed=closed)
        except Exception as exc:
            # Outer guard — should not normally fire, per-account
            # failures are caught above. If it does, the next tick will
            # retry; logging keeps the on-call paged.
            log.exception("account_closure_loop_tick_failed", error=str(exc))

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue

    log.info("account_closure_loop_stopped")


# ----------------------------------------------------------------------------
# PII purge: anonymise account/user PII 1y after closed_at.
# ----------------------------------------------------------------------------


async def _purge_one_account_pii(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
) -> bool:
    """Anonymise PII for a closed account. Returns True if a row was updated."""
    account = await db.get(Account, account_id)
    if account is None or account.closed_at is None or account.pii_purged_at is not None:
        return False

    closed = account.closed_at
    if closed.tzinfo is None:
        closed = closed.replace(tzinfo=UTC)
    if closed >= datetime.now(UTC) - timedelta(days=PII_PURGE_AFTER_DAYS):
        return False

    user = await db.get(User, account.owner_id)
    now = datetime.now(UTC)

    if user is not None:
        # Anonymise — keep the row (audit_log FK / Transaction owner) but
        # drop everything that could identify a natural person.
        # Email: rewrite to a synthetic local-domain placeholder. The
        # ``users.email`` column has a UNIQUE constraint, so we include
        # the account_id to stay collision-free.
        user.email = f"deleted+{account_id}@brikko.local"
        user.password_hash = ""
        user.email_verified = False
        user.verification_token = None
        user.verification_sent_at = None
        user.password_reset_token = None
        user.password_reset_sent_at = None
        user.telegram_chat_id = None
        user.totp_secret_encrypted = None
        user.totp_enabled = False
        user.totp_recovery_codes_hashed = None

    account.pii_purged_at = now

    await write_audit(
        db,
        user_id=account.owner_id,
        account_id=account_id,
        action="account_pii_purged",
        request=None,
        severity="warning",
    )
    await db.commit()

    with contextlib.suppress(Exception):  # Sentry-down must not break us
        sentry_sdk.capture_message(
            f"account_pii_purged:{account_id}",
            level="warning",
        )

    return True


async def process_account_pii_purge(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """One sweep: anonymise PII for accounts closed > PII_PURGE_AFTER_DAYS days ago."""
    purged = 0
    cutoff = datetime.now(UTC) - timedelta(days=PII_PURGE_AFTER_DAYS)
    async with session_factory() as session:
        rows = await session.execute(
            select(Account.id).where(
                Account.closed_at.is_not(None),
                Account.pii_purged_at.is_(None),
                Account.closed_at < cutoff,
            )
        )
        account_ids = [r[0] for r in rows.all()]

    for account_id in account_ids:
        async with session_factory() as session:
            try:
                done = await _purge_one_account_pii(session, account_id=account_id)
                if done:
                    purged += 1
            except Exception as exc:
                await session.rollback()
                log.exception(
                    "account_pii_purge_failed",
                    account_id=str(account_id),
                    error=str(exc),
                )
    return purged


async def account_pii_purge_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    interval_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    log.info("account_pii_purge_loop_started", interval_seconds=interval_seconds)
    while not stop_event.is_set():
        try:
            purged = await process_account_pii_purge(session_factory)
            if purged:
                log.info("account_pii_purge_tick", accounts_purged=purged)
        except Exception as exc:
            log.exception("account_pii_purge_loop_tick_failed", error=str(exc))

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue

    log.info("account_pii_purge_loop_stopped")


__all__ = [
    "PII_PURGE_AFTER_DAYS",
    "account_closure_loop",
    "account_pii_purge_loop",
    "process_account_closures",
    "process_account_pii_purge",
]
