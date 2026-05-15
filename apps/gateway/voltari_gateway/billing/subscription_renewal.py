"""Subscription Phase 2 — renewal cron + dunning logic.

This module is the **state-mutating** half of Phase 2. Email composition
lives in :mod:`voltari_gateway.billing.subscription_emails`; this file
just (a) finds accounts due for a charge, (b) calls ЮKassa, (c) updates
``renewal_retry_count`` / ``renewal_last_failed_at``, and (d) downgrades
to PAYG when the dunning ladder is exhausted.

The three cron entrypoints are:

* :func:`renew_due_subscriptions` — slot 1. Runs every hour. Picks
  accounts whose ``subscription_active_until`` falls in the next 24 h
  (and that are not canceled), and fires a recurring charge through the
  saved card.
* :func:`retry_failed_renewals` — slots 2 & 3. Runs every 6 h. Picks
  accounts with ``renewal_retry_count IN (1, 2)`` and replays the charge
  after the per-slot delay elapsed.
* :func:`downgrade_exhausted_subscriptions` — runs alongside the
  retry sweep. Picks accounts with ``renewal_retry_count >=
  MAX_RENEWAL_RETRIES`` and flips them back to PAYG.

Activation itself (``payment.succeeded`` → tier=pro / active_until+=30d)
is handled by the existing webhook path in ``api/billing.py``. The cron
**never** writes ``active_until`` directly: it only initiates the
charge, ЮKassa redrives webhooks until we 200, and the webhook handler
calls ``activate_subscription`` which is idempotent on ``ref_id``.

Concurrency
-----------

We expect **one** cron container per timer (systemd `OnCalendar=hourly`
on a single VPS — see ``infra/systemd/brikko-renewal.timer``). If a
second instance ever runs concurrently (an operator re-runs the
oneshot, two replicas race) idempotency comes from:

1. The per-attempt ``ref_id`` ``renewal:{account_id}:{period}:{attempt}``.
   Each attempt gets a unique key — once attempt 1 has been issued today,
   a second cron run produces the same ref_id and ``charge_recurring``
   succeeds on ЮKassa (its own ``Idempotence-Key`` shorts the second
   call). Even if the second container believes the charge "succeeded",
   ``payment.succeeded`` only lands once.
2. ``with_for_update()`` on the account row during the bump of
   ``renewal_retry_count`` — second writer waits, sees the count = 1, and
   short-circuits.

Failure semantics
-----------------

* **ЮKassa returns 5xx / network error** — treated as a *transient*
  failure. We do NOT increment ``renewal_retry_count``; the cron just
  exits and the next tick retries. Otherwise a flaky ЮKassa burst would
  burn through the three-retry budget in 30 seconds.
* **ЮKassa returns 4xx with status=canceled** (card declined,
  insufficient funds, fraud) — *permanent* failure. We increment
  ``renewal_retry_count`` and stamp ``renewal_last_failed_at``.
* **Payment returns ``status="pending"``** — charge accepted, awaiting
  capture. We do nothing and let the webhook close the loop. The
  account stays at ``retry_count`` whatever it was (typically 0).

Edge cases
----------

* User cancels mid-period — ``subscription_canceled_at IS NOT NULL``.
  ``_candidate_accounts`` filters those out: no renewal attempt is ever
  initiated. The period elapses naturally; the existing webhook /
  subscription-expiry path handles the tier flip back to ``payg`` (or
  active_until just expires and ``is_subscription_active`` flips to
  False on its own).
* User cancels AFTER the cron picked them but BEFORE ЮKassa charged —
  we still fire the charge. If ``payment.succeeded`` lands, the
  ``activate_subscription`` call wipes ``canceled_at`` (CEO 2026-05-15:
  "the user paid; honour the period"). We log this case at INFO.
* User cancels AFTER ЮKassa already charged — too late, the period is
  paid for. UX: dashboard offers a refund-request form (manual, finance
  team) but the cron is not in the loop.
* ЮKassa returns ``payment_method_unknown`` (saved card was deleted on
  their side) — permanent error, counts as a retry slot. After 3 fails
  the downgrade kicks in.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import sentry_sdk
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voltari_gateway.billing.subscription import (
    MAX_RENEWAL_RETRIES,
    PAID_TIERS,
    RENEWAL_ATTEMPT_REF_ID_TEMPLATE,
    RENEWAL_RETRY_DELAY_HOURS,
    SUBSCRIPTION_PERIOD_DAYS,
    TIER_PAYG,
    price_for_tier,
)
from voltari_gateway.billing.yookassa import (
    YooKassaClient,
    YooKassaError,
)
from voltari_gateway.db.models import (
    Account,
    Transaction,
    TransactionKind,
    User,
)
from voltari_gateway.utils.logging import get_logger

if TYPE_CHECKING:
    from voltari_gateway.billing.subscription_emails import EmailDispatcher


log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: How far ahead of ``active_until`` we initiate the renewal charge.
#: 24 h means the first attempt lands inside the user's last day —
#: enough lead time that a ``payment.succeeded`` webhook arrives before
#: ``active_until`` flips ``is_subscription_active`` to False. If the
#: card declines, retry slot 2 (24 h later) typically lands shortly
#: after expiry; the gateway is briefly in "downgrade pending" but the
#: user can re-link a card from the dashboard.
RENEWAL_LOOKAHEAD_HOURS: int = 24

#: Lookback for the retry sweep — accounts whose last failure was more
#: than 7 days ago are stale and should already have been downgraded.
#: We filter them out so a stuck row (missing downgrade due to bug) doesn't
#: keep generating charges forever.
RETRY_LOOKBACK_HOURS: int = 24 * 7


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenewalAttemptResult:
    """Outcome of one renewal-charge attempt against ЮKassa.

    ``status`` mirrors ``YooKassa PaymentURL.status``:
      * ``"succeeded"``   — captured immediately; webhook will confirm
      * ``"pending"``     — accepted, waiting for capture / 3DS / 3rd party
      * ``"canceled"``    — declined (counts as a dunning slot)
      * ``"transient"``   — network / 5xx (does NOT count as a dunning slot)
    """

    status: str
    payment_id: str | None
    error_message: str | None
    attempt_number: int  # 1..MAX_RENEWAL_RETRIES (what slot we just used)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _period_str(active_until: datetime) -> str:
    """Date-precision anchor — see SubscriptionReminderSent docstring."""
    return active_until.strftime("%Y%m%d")


def _build_attempt_ref_id(
    *,
    account_id: uuid.UUID,
    active_until: datetime,
    attempt: int,
) -> str:
    return RENEWAL_ATTEMPT_REF_ID_TEMPLATE.format(
        account_id=account_id,
        period=_period_str(active_until),
        attempt=attempt,
    )


def _tz(value: datetime | None) -> datetime | None:
    """Defensive: SQLite stores naive UTC; re-attach tzinfo."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _record_failure(
    db: AsyncSession,
    *,
    account: Account,
    error_message: str,
    now: datetime,
) -> int:
    """Bump retry counter + stamp last_failed_at. Returns the new count.

    Caller commits. Uses a separate ``with_for_update()`` re-fetch so
    a concurrent cron tick can't double-increment.
    """
    row = (
        await db.execute(
            select(Account).where(Account.id == account.id).with_for_update()
        )
    ).scalar_one()
    row.renewal_retry_count = (row.renewal_retry_count or 0) + 1
    row.renewal_last_failed_at = now
    await db.flush()
    log.warning(
        "subscription_renewal_failed",
        account_id=str(account.id),
        retry_count=row.renewal_retry_count,
        error=error_message[:512],
    )
    return row.renewal_retry_count


async def _attempt_renewal_charge(
    *,
    db: AsyncSession,
    yookassa: YooKassaClient,
    account: Account,
    attempt: int,
    now: datetime,
) -> RenewalAttemptResult:
    """Initiate one ЮKassa recurring charge for the account's subscription.

    Returns a typed result; *does not* commit. The caller commits in its
    own transaction so this function can be reused from a test harness
    that wants to assert pre-/post-conditions.
    """
    tier = account.subscription_tier
    if tier not in PAID_TIERS:
        # Defensive — caller already filtered, but never charge a payg row.
        return RenewalAttemptResult(
            status="canceled",
            payment_id=None,
            error_message=f"tier_not_paid:{tier}",
            attempt_number=attempt,
        )
    pm_id = account.autorefill_pm_id
    if not pm_id:
        return RenewalAttemptResult(
            status="canceled",
            payment_id=None,
            error_message="no_payment_method",
            attempt_number=attempt,
        )

    active_until = _tz(account.subscription_active_until) or now
    amount_kopecks = price_for_tier(tier)
    ref_id = _build_attempt_ref_id(
        account_id=account.id, active_until=active_until, attempt=attempt
    )

    try:
        result = await yookassa.charge_recurring(
            account_id=account.id,
            amount_kopecks=amount_kopecks,
            payment_method_id=pm_id,
            description=f"Brikko {tier.upper()} subscription renewal",
            metadata={
                "account_id": str(account.id),
                "purpose": "subscription_charge",
                "tier": tier,
                "renewal_attempt": str(attempt),
                "renewal_ref_id": ref_id,
            },
        )
    except YooKassaError as exc:
        msg = str(exc)
        # Distinguish 5xx / network from explicit declines. Our YooKassaError
        # message convention: ``yookassa_recurring_status_{code}: {body}`` for
        # HTTP errors, ``yookassa_unreachable: ...`` for transport.
        if "yookassa_unreachable" in msg or "_status_5" in msg or "_status_408" in msg:
            log.warning(
                "subscription_renewal_transient",
                account_id=str(account.id),
                attempt=attempt,
                error=msg[:512],
            )
            return RenewalAttemptResult(
                status="transient",
                payment_id=None,
                error_message=msg[:1024],
                attempt_number=attempt,
            )
        return RenewalAttemptResult(
            status="canceled",
            payment_id=None,
            error_message=msg[:1024],
            attempt_number=attempt,
        )

    status = result.status
    if status not in {"succeeded", "pending", "waiting_for_capture", "canceled"}:
        # Unknown ЮKassa status — be conservative and treat as transient so
        # we retry rather than burn a dunning slot on a spec change.
        status = "transient"

    # Persist an audit ledger row for the attempt with a flat ref_id.
    # We deliberately do NOT credit balance here — the webhook handler is
    # the single writer for the SUBSCRIPTION transaction (activation).
    # This row is purely an audit breadcrumb so the dashboard / billing
    # log can show "we tried, here is what ЮKassa said".
    tx_meta = {
        "kind": "subscription_renewal_attempt",
        "tier": tier,
        "attempt": attempt,
        "yookassa_payment_id": result.payment_id,
        "yookassa_status": status,
    }
    # We piggy-back the audit row on TransactionKind.SUBSCRIPTION when the
    # attempt succeeded *and* status=succeeded so the existing balance
    # CHECK constraint (`subscription => > 0`) is satisfied. For pending /
    # canceled / transient we DO NOT write a transaction (the CHECK would
    # forbid a zero-amount SUBSCRIPTION row). Activation is the canonical
    # write — via the webhook — so this is purely lossless.
    if status == "succeeded":
        db.add(
            Transaction(
                account_id=account.id,
                type=TransactionKind.SUBSCRIPTION,
                amount_kopecks=amount_kopecks,
                ref_id=ref_id,
                meta=tx_meta,
            )
        )
        with contextlib.suppress(IntegrityError):
            # Replay tolerance: a duplicate cron tick that produces the same
            # ref_id is fine — the UNIQUE constraint shorts.
            await db.flush()

    return RenewalAttemptResult(
        status=status,
        payment_id=result.payment_id,
        error_message=None,
        attempt_number=attempt,
    )


# ---------------------------------------------------------------------------
# Public: due-subscription sweep (slot 1, every hour)
# ---------------------------------------------------------------------------


async def _candidate_due_accounts(
    db: AsyncSession, *, now: datetime
) -> list[uuid.UUID]:
    """Accounts whose paid period ends in the next 24 h and need a renewal.

    Filters:
      * Paid tier
      * ``active_until`` between now and now + 24h
      * Not canceled
      * Has a saved card
      * ``renewal_retry_count == 0`` (slot-1 is for fresh renewals;
        retries are handled by :func:`retry_failed_renewals`)
    """
    horizon = now + timedelta(hours=RENEWAL_LOOKAHEAD_HOURS)
    rows = await db.execute(
        select(Account.id).where(
            Account.subscription_tier.in_(list(PAID_TIERS)),
            Account.subscription_canceled_at.is_(None),
            Account.autorefill_pm_id.is_not(None),
            Account.renewal_retry_count == 0,
            Account.subscription_active_until.is_not(None),
            Account.subscription_active_until <= horizon,
            Account.subscription_active_until > now - timedelta(hours=1),
        )
    )
    return [r[0] for r in rows.all()]


async def renew_due_subscriptions(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    yookassa: YooKassaClient,
    email: EmailDispatcher | None = None,
) -> int:
    """Slot-1 of the dunning ladder. Returns count of accounts attempted."""
    now = _utcnow()
    async with session_factory() as db:
        account_ids = await _candidate_due_accounts(db, now=now)

    attempted = 0
    for account_id in account_ids:
        async with session_factory() as db:
            try:
                account = (
                    await db.execute(
                        select(Account)
                        .where(Account.id == account_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if account is None:
                    continue
                # Re-validate post-lock: another cron / API call may have
                # canceled the subscription between the candidate query and
                # the row lock.
                if account.subscription_canceled_at is not None:
                    log.info(
                        "subscription_renewal_skipped_cancelled",
                        account_id=str(account_id),
                    )
                    continue
                if account.renewal_retry_count != 0:
                    # Another worker raced us and already attempted.
                    continue

                result = await _attempt_renewal_charge(
                    db=db,
                    yookassa=yookassa,
                    account=account,
                    attempt=1,
                    now=now,
                )
                attempted += 1
                if result.status in {"canceled"}:
                    await _record_failure(
                        db,
                        account=account,
                        error_message=result.error_message or "declined",
                        now=now,
                    )
                    await db.commit()
                    if email is not None:
                        await email.send_renewal_failed(
                            account_id=account.id,
                            attempt=1,
                            retry_in_hours=RENEWAL_RETRY_DELAY_HOURS[0],
                        )
                else:
                    # succeeded / pending / transient — no counter bump.
                    await db.commit()
            except Exception:
                await db.rollback()
                log.exception(
                    "subscription_renewal_unexpected",
                    account_id=str(account_id),
                )

    if attempted:
        log.info("subscription_renewal_tick", attempted=attempted)
    return attempted


# ---------------------------------------------------------------------------
# Public: failed-renewal retry sweep (slots 2/3, every 6 h)
# ---------------------------------------------------------------------------


def _next_attempt_for(retry_count: int) -> int:
    """Return the next attempt number to use given ``retry_count``."""
    # retry_count semantics: number of *failed* attempts so far.
    # After 1 fail → next attempt is #2; after 2 → #3.
    return retry_count + 1


def _retry_due(account: Account, *, now: datetime) -> bool:
    """Is this account due for its next retry slot?"""
    last = _tz(account.renewal_last_failed_at)
    if last is None:
        return False
    count = account.renewal_retry_count or 0
    if count < 1 or count >= MAX_RENEWAL_RETRIES:
        return False
    delay_hours = RENEWAL_RETRY_DELAY_HOURS[count - 1]
    return now - last >= timedelta(hours=delay_hours)


async def _candidate_retry_accounts(
    db: AsyncSession, *, now: datetime
) -> list[uuid.UUID]:
    """Accounts that have failed at least once but not yet exhausted retries.

    SQL filter is the *cheap* part — we then re-check ``_retry_due`` in
    Python for the exact per-slot delay. Pushing the schedule arithmetic
    into SQL would couple us to dialect-specific interval syntax for no
    real win at our scale.
    """
    cutoff = now - timedelta(hours=RETRY_LOOKBACK_HOURS)
    rows = await db.execute(
        select(Account.id).where(
            Account.subscription_tier.in_(list(PAID_TIERS)),
            Account.subscription_canceled_at.is_(None),
            Account.autorefill_pm_id.is_not(None),
            Account.renewal_retry_count >= 1,
            Account.renewal_retry_count < MAX_RENEWAL_RETRIES,
            Account.renewal_last_failed_at.is_not(None),
            Account.renewal_last_failed_at >= cutoff,
        )
    )
    return [r[0] for r in rows.all()]


async def retry_failed_renewals(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    yookassa: YooKassaClient,
    email: EmailDispatcher | None = None,
) -> int:
    """Re-run dunning slot 2 or 3 for accounts whose previous attempt failed."""
    now = _utcnow()
    async with session_factory() as db:
        account_ids = await _candidate_retry_accounts(db, now=now)

    retried = 0
    for account_id in account_ids:
        async with session_factory() as db:
            try:
                account = (
                    await db.execute(
                        select(Account)
                        .where(Account.id == account_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if account is None:
                    continue
                if not _retry_due(account, now=now):
                    continue
                if account.subscription_canceled_at is not None:
                    continue

                attempt = _next_attempt_for(account.renewal_retry_count)
                result = await _attempt_renewal_charge(
                    db=db,
                    yookassa=yookassa,
                    account=account,
                    attempt=attempt,
                    now=now,
                )
                retried += 1
                if result.status == "canceled":
                    new_count = await _record_failure(
                        db,
                        account=account,
                        error_message=result.error_message or "declined",
                        now=now,
                    )
                    await db.commit()
                    if email is not None:
                        if new_count >= MAX_RENEWAL_RETRIES:
                            # Downgrade email fires from the dedicated sweep;
                            # don't double-send here.
                            pass
                        else:
                            await email.send_renewal_failed(
                                account_id=account.id,
                                attempt=attempt,
                                retry_in_hours=(
                                    RENEWAL_RETRY_DELAY_HOURS[new_count - 1]
                                ),
                            )
                elif result.status in {"succeeded", "pending", "waiting_for_capture"}:
                    # Reset counter on success-y outcomes. The webhook will
                    # land later and bump ``active_until`` — we already know
                    # the charge stuck so it's safe to forgive the dunning.
                    account.renewal_retry_count = 0
                    account.renewal_last_failed_at = None
                    await db.commit()
                else:
                    # transient — leave counter as-is.
                    await db.commit()
            except Exception:
                await db.rollback()
                log.exception(
                    "subscription_retry_unexpected",
                    account_id=str(account_id),
                )

    if retried:
        log.info("subscription_retry_tick", retried=retried)
    return retried


# ---------------------------------------------------------------------------
# Public: downgrade sweep (after dunning exhausted)
# ---------------------------------------------------------------------------


async def downgrade_exhausted_subscriptions(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    email: EmailDispatcher | None = None,
) -> int:
    """Flip accounts that exhausted dunning back to PAYG.

    Runs immediately after :func:`retry_failed_renewals` on the same
    timer; we keep it as a separate function so a manual op-team run
    (``--job=downgrade``) can sweep stuck rows without re-attempting any
    charges.

    Behaviour:
      * Set ``subscription_tier = 'payg'``
      * Wipe ``active_until`` (the user is no longer paid)
      * Reset retry counter + last_failed_at (we're done dunning)
      * Send the "downgraded" email (idempotent on
        ``subscription_reminders_sent``)
      * Audit-log via Sentry (low frequency, high signal)
    """
    async with session_factory() as db:
        rows = await db.execute(
            select(Account.id).where(
                Account.subscription_tier.in_(list(PAID_TIERS)),
                Account.renewal_retry_count >= MAX_RENEWAL_RETRIES,
            )
        )
        account_ids = [r[0] for r in rows.all()]

    downgraded = 0
    for account_id in account_ids:
        async with session_factory() as db:
            try:
                account = (
                    await db.execute(
                        select(Account)
                        .where(Account.id == account_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if account is None:
                    continue
                if account.renewal_retry_count < MAX_RENEWAL_RETRIES:
                    continue  # raced; another worker already reset.
                if account.subscription_tier == TIER_PAYG:
                    continue

                old_tier = account.subscription_tier
                account.subscription_tier = TIER_PAYG
                account.subscription_active_until = None
                account.subscription_canceled_at = None
                account.renewal_retry_count = 0
                account.renewal_last_failed_at = None
                await db.commit()
                downgraded += 1

                if email is not None:
                    await email.send_renewal_downgraded(
                        account_id=account_id,
                        old_tier=old_tier,
                    )

                with contextlib.suppress(Exception):
                    sentry_sdk.capture_message(
                        f"subscription_downgraded:{account_id}:{old_tier}",
                        level="warning",
                    )
                log.warning(
                    "subscription_downgraded",
                    account_id=str(account_id),
                    from_tier=old_tier,
                )
            except Exception:
                await db.rollback()
                log.exception(
                    "subscription_downgrade_unexpected",
                    account_id=str(account_id),
                )

    if downgraded:
        log.info("subscription_downgrade_tick", downgraded=downgraded)
    return downgraded


# ---------------------------------------------------------------------------
# User lookup (used by both renewal cron + emails)
# ---------------------------------------------------------------------------


async def fetch_user_for_account(
    db: AsyncSession, *, account_id: uuid.UUID
) -> User | None:
    """Resolve owner ``User`` row given an account id. None if not found."""
    row = (
        await db.execute(
            select(User)
            .join(Account, Account.owner_id == User.id)
            .where(Account.id == account_id)
        )
    ).scalar_one_or_none()
    return row


__all__ = [
    "RENEWAL_LOOKAHEAD_HOURS",
    "RETRY_LOOKBACK_HOURS",
    "RenewalAttemptResult",
    "downgrade_exhausted_subscriptions",
    "fetch_user_for_account",
    "renew_due_subscriptions",
    "retry_failed_renewals",
]


# Re-export for documentation: the period-rollover constant is owned by
# :mod:`subscription` but call sites in this module reference it via the
# ``activate_subscription`` webhook path. Imported above; the lint here
# is just to give readers one canonical place to find it.
_ = SUBSCRIPTION_PERIOD_DAYS
