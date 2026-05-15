"""Subscription Phase 2 — email reminders + dunning notifications.

Three outbound messages:

  * **renewal_t_minus_3** — sent ~72 h before ``active_until`` warning
    the user we're about to charge their saved card. Driven by
    :func:`send_renewal_reminders` (daily cron at 10:00 UTC).
  * **renewal_failed** — sent inside the cron after each *non-final*
    failed charge. Driven by ``EmailDispatcher.send_renewal_failed``,
    called inline from ``subscription_renewal.py``.
  * **renewal_downgraded** — sent when ``MAX_RENEWAL_RETRIES`` is
    exhausted and we flip the user to PAYG. Driven by
    ``EmailDispatcher.send_renewal_downgraded``.

Idempotency is enforced by the ``subscription_reminders_sent`` table
(Alembic 0025). Each row is unique on
``(account_id, reminder_type, period_date)``: a second cron tick on the
same calendar period catches ``IntegrityError`` and treats it as a
no-op.

``card_last4`` masking
---------------------

Phase 1 didn't store ``card_last4`` (the column is reserved on the
``SubscriptionState`` dataclass). We use a generic "**** {last4}"
placeholder where last4 is *not yet available* — set the value from the
ЮKassa ``payment_method.card.last4`` field in the existing webhook
handler when you wire Phase 3. The emails read fine either way.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voltari_gateway.billing.subscription import (
    MAX_RENEWAL_RETRIES,
    PAID_TIERS,
    price_for_tier,
)
from voltari_gateway.db.models import (
    Account,
    SubscriptionReminderSent,
    User,
)
from voltari_gateway.email.client import render_template, send_email
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: T-3 day reminder window. The cron picks rows where
#: ``active_until`` falls between ``now + 72 h`` and ``now + 96 h``. The
#: 24-hour width gives a daily cron a clean slot — even if the cron is
#: delayed by a few hours, the reminder still lands ~3 days ahead.
T_MINUS_3_WINDOW_START_HOURS: int = 72
T_MINUS_3_WINDOW_END_HOURS: int = 96

#: Reminder type strings — keep in sync with the
#: ``subscription_reminders_sent`` table comment. NEVER REUSE a label.
REMINDER_T_MINUS_3 = "renewal_t_minus_3"
REMINDER_FAILED = "renewal_failed"
REMINDER_DOWNGRADED = "renewal_downgraded"

#: Frontend billing URL — used as a CTA in every Phase-2 email. The base
#: domain comes from ``Settings.base_url_frontend`` but the dunning
#: emails always link straight to the billing page, regardless of locale.
_BILLING_PATH = "/app/billing"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tz(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _format_active_until(active_until: datetime) -> str:
    """Human-friendly date for the email body."""
    a = _tz(active_until) or datetime.now(UTC)
    # MSK ≈ UTC+3; we don't import zoneinfo here because the email body
    # only needs day-level precision and UTC date is unambiguous enough
    # for the "in 3 days" framing. Format: 2026-05-18.
    return a.strftime("%Y-%m-%d")


def _tier_title(tier: str) -> str:
    return {"pro": "Pro", "team": "Team"}.get(tier, tier.upper())


def _format_price(tier: str) -> str:
    return f"{price_for_tier(tier) / 100:.0f}"


def _masked_last4(account: Account) -> str:
    """ Phase 1 didn't persist ``card_last4`` — return a generic mask."""
    # Phase 3 should fetch this from ЮKassa once on card-link and persist.
    # Until then, a stable placeholder reads better in the email than ``****``.
    return "**** ****"


def _billing_url() -> str:
    """Resolve the dashboard billing URL. Done lazily so tests can monkeypatch
    ``BASE_URL_FRONTEND`` per-case."""
    from voltari_gateway.config import get_settings

    base = get_settings().base_url_frontend.rstrip("/")
    return f"{base}{_BILLING_PATH}"


# ---------------------------------------------------------------------------
# Idempotent log writer
# ---------------------------------------------------------------------------


async def _claim_reminder_slot(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    reminder_type: str,
    period_date: date,
) -> bool:
    """INSERT a reminder row; return True iff this is a fresh send.

    Catches IntegrityError on the UNIQUE constraint and treats it as
    "already sent for this period" — the caller skips the send. Caller
    commits.
    """
    db.add(
        SubscriptionReminderSent(
            account_id=account_id,
            reminder_type=reminder_type,
            period_date=period_date,
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return False
    return True


# ---------------------------------------------------------------------------
# Public: T-3 reminder cron
# ---------------------------------------------------------------------------


async def send_renewal_reminders(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    send: Callable[[str, str, str], Awaitable[None]] = send_email,
) -> int:
    """Daily sweep — email accounts whose ``active_until`` is 72-96h away.

    Returns the number of emails actually dispatched. The ``send``
    parameter is injectable so tests can capture messages without
    monkeypatching the module-level ``send_email``.
    """
    now = datetime.now(UTC)
    window_start = now + timedelta(hours=T_MINUS_3_WINDOW_START_HOURS)
    window_end = now + timedelta(hours=T_MINUS_3_WINDOW_END_HOURS)

    async with session_factory() as db:
        rows = await db.execute(
            select(Account.id).where(
                Account.subscription_tier.in_(list(PAID_TIERS)),
                Account.subscription_canceled_at.is_(None),
                Account.autorefill_pm_id.is_not(None),
                Account.subscription_active_until.is_not(None),
                Account.subscription_active_until >= window_start,
                Account.subscription_active_until < window_end,
            )
        )
        account_ids = [r[0] for r in rows.all()]

    sent_count = 0
    for account_id in account_ids:
        async with session_factory() as db:
            try:
                account = (
                    await db.execute(
                        select(Account).where(Account.id == account_id)
                    )
                ).scalar_one_or_none()
                if account is None:
                    continue
                active_until = _tz(account.subscription_active_until)
                if active_until is None:
                    continue
                user = (
                    await db.execute(
                        select(User).where(User.id == account.owner_id)
                    )
                ).scalar_one_or_none()
                if user is None or not user.email:
                    continue

                fresh = await _claim_reminder_slot(
                    db,
                    account_id=account.id,
                    reminder_type=REMINDER_T_MINUS_3,
                    period_date=active_until.date(),
                )
                if not fresh:
                    continue

                # Compose + send. The email module swallows SMTP errors —
                # we still mark the slot as claimed so a flaky SMTP doesn't
                # cause repeated retries that *do* eventually go through and
                # spam the user. Phase 3 can move this to a retry queue.
                body = render_template(
                    "subscription_renewal_reminder.txt",
                    tier_title=_tier_title(account.subscription_tier),
                    active_until=_format_active_until(active_until),
                    card_last4=_masked_last4(account),
                    price_rub=_format_price(account.subscription_tier),
                    billing_url=_billing_url(),
                )
                subject_line, body_no_subject = _split_subject(body)
                try:
                    await send(user.email, subject_line, body_no_subject)
                    sent_count += 1
                    await db.commit()
                    log.info(
                        "subscription_reminder_sent",
                        account_id=str(account.id),
                        type=REMINDER_T_MINUS_3,
                        period=active_until.date().isoformat(),
                    )
                except Exception:
                    # Email layer is best-effort; we've already claimed the
                    # idempotency slot, so commit anyway. An operator can
                    # delete the slot manually if a real retry is needed.
                    await db.commit()
                    log.exception(
                        "subscription_reminder_send_failed",
                        account_id=str(account.id),
                    )
            except Exception:
                await db.rollback()
                log.exception(
                    "subscription_reminder_unexpected",
                    account_id=str(account_id),
                )

    if sent_count:
        log.info("subscription_reminder_tick", sent=sent_count)
    return sent_count


def _split_subject(rendered: str) -> tuple[str, str]:
    """Pop the first ``Тема: ...`` line off a rendered template.

    Convention used by every Brikko email template: the first line is
    ``Тема: <subject>`` followed by a blank line. Keeping subject inside
    the template means translators can edit one file, not two.
    """
    lines = rendered.split("\n")
    if lines and lines[0].startswith("Тема: "):
        subject = lines[0][len("Тема: ") :].strip()
        # Drop subject + the leading blank line if present.
        rest = "\n".join(lines[2:]) if len(lines) >= 2 and not lines[1].strip() else "\n".join(lines[1:])
        return subject, rest
    return "Brikko notification", rendered


# ---------------------------------------------------------------------------
# Dispatcher used inline from subscription_renewal.py
# ---------------------------------------------------------------------------


@dataclass
class EmailDispatcher:
    """Thin object that subscription_renewal.py uses to send dunning emails.

    Pulled out of subscription_renewal.py to keep that module pure (state
    mutations only) and avoid a circular import — both modules import
    from subscription.py but only this one imports email/templates.
    """

    session_factory: async_sessionmaker[AsyncSession]
    sender: Callable[[str, str, str], Awaitable[None]] = send_email

    async def send_renewal_failed(
        self,
        *,
        account_id: uuid.UUID,
        attempt: int,
        retry_in_hours: int,
    ) -> bool:
        """Email "we failed to charge, retrying in {retry_in_hours}h".

        Idempotent per ``(account_id, period_date, attempt#)`` — we use a
        synthetic ``reminder_type`` of ``renewal_failed_{attempt}`` so
        each retry slot has its own idempotency record. Three attempts
        → three rows max per period.
        """
        async with self.session_factory() as db:
            account = (
                await db.execute(select(Account).where(Account.id == account_id))
            ).scalar_one_or_none()
            if account is None:
                return False
            active_until = _tz(account.subscription_active_until)
            if active_until is None:
                return False
            user = (
                await db.execute(select(User).where(User.id == account.owner_id))
            ).scalar_one_or_none()
            if user is None or not user.email:
                return False

            rtype = f"{REMINDER_FAILED}_{attempt}"
            fresh = await _claim_reminder_slot(
                db,
                account_id=account.id,
                reminder_type=rtype,
                period_date=active_until.date(),
            )
            if not fresh:
                return False

            body = render_template(
                "subscription_renewal_failed.txt",
                tier_title=_tier_title(account.subscription_tier),
                card_last4=_masked_last4(account),
                price_rub=_format_price(account.subscription_tier),
                retry_in_hours=retry_in_hours,
                billing_url=_billing_url(),
            )
            subject, plain = _split_subject(body)
            with contextlib.suppress(Exception):
                await self.sender(user.email, subject, plain)
            await db.commit()
            log.info(
                "subscription_renewal_failed_email",
                account_id=str(account_id),
                attempt=attempt,
            )
            return True

    async def send_renewal_downgraded(
        self,
        *,
        account_id: uuid.UUID,
        old_tier: str,
    ) -> bool:
        """Email "your subscription was downgraded after {MAX} failures"."""
        async with self.session_factory() as db:
            account = (
                await db.execute(select(Account).where(Account.id == account_id))
            ).scalar_one_or_none()
            if account is None:
                return False
            user = (
                await db.execute(select(User).where(User.id == account.owner_id))
            ).scalar_one_or_none()
            if user is None or not user.email:
                return False

            # Period anchor: today's date. The original ``active_until`` is
            # already wiped by the downgrade caller; using "today" keeps
            # the idempotency record per-day, which is fine because we'll
            # never downgrade the same account twice in one day.
            today = datetime.now(UTC).date()
            fresh = await _claim_reminder_slot(
                db,
                account_id=account.id,
                reminder_type=REMINDER_DOWNGRADED,
                period_date=today,
            )
            if not fresh:
                return False

            body = render_template(
                "subscription_renewal_downgraded.txt",
                tier_title=_tier_title(old_tier),
                card_last4=_masked_last4(account),
                price_rub=_format_price(old_tier),
                billing_url=_billing_url(),
            )
            subject, plain = _split_subject(body)
            with contextlib.suppress(Exception):
                await self.sender(user.email, subject, plain)
            await db.commit()
            log.warning(
                "subscription_downgraded_email",
                account_id=str(account_id),
                old_tier=old_tier,
            )
            return True


__all__ = [
    "REMINDER_DOWNGRADED",
    "REMINDER_FAILED",
    "REMINDER_T_MINUS_3",
    "T_MINUS_3_WINDOW_END_HOURS",
    "T_MINUS_3_WINDOW_START_HOURS",
    "EmailDispatcher",
    "send_renewal_reminders",
]

# Re-export so call sites that import the constant from this module don't
# need a second import path.
_ = MAX_RENEWAL_RETRIES
