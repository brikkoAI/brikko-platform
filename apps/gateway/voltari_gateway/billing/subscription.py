"""Subscription billing — Pro 290 ₽/mo, Team 1490 ₽/mo.

Pay-per-use → subscription pivot (CEO 2026-05-15, BRIEF_v2_pivot.md update).
The PAYG tariff is reduced to welcome credits only. Recurring access is
only available via a monthly subscription that auto-charges the linked card.

Phase 1 (this module) implements:

  * Constants for the two paid tiers (Pro/Team) and helper to map
    ``tier → kopecks``.
  * ``is_subscription_active(account)`` — single source of truth for the
    "skip billing on /v1/anonymize" check.
  * ``activate_subscription`` — webhook-side: ``payment.succeeded`` for a
    subscription charge sets ``tier`` + ``active_until = now + 30d``.

Phase 2 (NOT in this module yet):

  * Cron job for monthly recurring charges (re-uses
    ``yookassa.charge_recurring`` with the saved ``autorefill_pm_id``).
  * Email reminders 3 days before ``active_until``.
  * Failed-payment retry / dunning.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.db.models import Account, Transaction, TransactionKind
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


# --- Public constants ---------------------------------------------------------

#: Tier strings stored in ``account.subscription_tier``. Mirrored in the
#: SubscribeRequest pydantic enum on the API side.
TIER_PAYG: Final[str] = "payg"
TIER_PRO: Final[str] = "pro"
TIER_TEAM: Final[str] = "team"

PAID_TIERS: Final[frozenset[str]] = frozenset({TIER_PRO, TIER_TEAM})

#: Subscription price per tier, in kopecks (CEO 2026-05-15).
TIER_PRICE_KOPECKS: Final[dict[str, int]] = {
    TIER_PRO: 29_000,  # 290 ₽
    TIER_TEAM: 149_000,  # 1490 ₽
}

#: Subscription period — 30 days, calendar-month approximation. The Phase 2
#: cron re-charges at ``active_until``; if a customer subscribes on the
#: 31st, the next charge lands on the 30th of the next month, then drifts
#: back to the 31st where present. Simpler than calendar-month arithmetic.
SUBSCRIPTION_PERIOD_DAYS: Final[int] = 30

#: Reference prefix for the subscription ledger row.
#: Format: ``sub:{tier}:{account_id}:{period_yyyymmdd}``.
SUB_REF_ID_TEMPLATE: Final[str] = "sub:{tier}:{account_id}:{period}"


Tier = Literal["pro", "team"]


@dataclass(frozen=True)
class SubscriptionState:
    """Read-side projection of the three subscription columns.

    Returned by ``GET /v1/billing/subscription``. ``card_linked`` is
    populated from the existing ``autorefill_pm_id`` column — both
    flows share that handle.
    """

    tier: str
    active_until: datetime | None
    canceled_at: datetime | None
    card_linked: bool
    card_last4: str | None  # NULL — populated in Phase 2 from ЮKassa get_payment_method


# --- Predicates ---------------------------------------------------------------


def is_subscription_active(account: Account, *, now: datetime | None = None) -> bool:
    """Return True iff the account has a live paid subscription right now.

    Used by ``/v1/anonymize`` to skip the per-request billing gate — active
    subscriptions are unlimited.

    A tier is "active" when:
      1. ``subscription_tier`` is one of the paid tiers (Pro/Team), AND
      2. ``subscription_active_until`` is in the future.

    A subscription with ``canceled_at`` set is STILL active until
    ``active_until`` elapses — the user paid for the period and we honour it.
    """
    if account.subscription_tier not in PAID_TIERS:
        return False
    if account.subscription_active_until is None:
        return False
    now = now or datetime.now(UTC)
    # Defensive: ``active_until`` may come back from SQLite as naive datetime.
    active_until = account.subscription_active_until
    if active_until.tzinfo is None:
        active_until = active_until.replace(tzinfo=UTC)
    return active_until > now


def price_for_tier(tier: str) -> int:
    """Lookup helper — raises ValueError on unknown tier."""
    if tier not in TIER_PRICE_KOPECKS:
        raise ValueError(f"unknown subscription tier: {tier!r}")
    return TIER_PRICE_KOPECKS[tier]


def build_sub_ref_id(*, tier: str, account_id: uuid.UUID, period_start: datetime) -> str:
    """Stable, idempotent ref_id for a subscription period transaction.

    Same (account, tier, period) → same ref_id → the UNIQUE on
    transactions.(account_id, ref_id) protects against double-charge from
    a webhook replay.
    """
    period_str = period_start.strftime("%Y%m%d")
    return SUB_REF_ID_TEMPLATE.format(tier=tier, account_id=account_id, period=period_str)


# --- State mutations ----------------------------------------------------------


async def activate_subscription(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    tier: str,
    payment_id: str,
    paid_at: datetime | None = None,
) -> bool:
    """Mark an account's subscription as active for 30 days. Caller commits.

    Called from the webhook handler when ``payment.succeeded`` lands for
    a ``subscription_charge`` payment. Idempotent on the (account_id,
    ref_id) UNIQUE: a replayed webhook does not double-credit nor
    double-extend ``active_until``.

    Behaviour:

      * If there is NO existing subscription row with this ref_id:
          - tier := requested tier
          - active_until := max(active_until_today, now) + 30d
              (rolling-over an in-flight subscription gives the user the
              remainder of their current period plus 30 fresh days; an
              expired or NULL active_until is just now + 30d)
          - canceled_at := NULL (re-subscription clears any prior cancel)
          - INSERT Transaction(SUBSCRIPTION, +price, ref_id=sub:tier:id:period)

      * If the ref_id already exists (replay): no-op, return False.

    Returns True iff this call actually activated.
    """
    if tier not in PAID_TIERS:
        raise ValueError(f"cannot activate non-paid tier {tier!r}")

    now = paid_at or datetime.now(UTC)
    price_kopecks = price_for_tier(tier)
    ref_id = build_sub_ref_id(tier=tier, account_id=account_id, period_start=now)

    # Idempotency check before mutating Account: a replayed webhook should
    # leave the account row exactly as it was.
    existing = (
        await db.execute(
            select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.ref_id == ref_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        log.info(
            "subscription_activate_replay",
            account_id=str(account_id),
            tier=tier,
            ref_id=ref_id,
        )
        return False

    account = (
        await db.execute(select(Account).where(Account.id == account_id).with_for_update())
    ).scalar_one_or_none()
    if account is None:
        log.warning("subscription_activate_no_account", account_id=str(account_id))
        return False

    # Roll forward — if the user is upgrading mid-period, preserve their
    # remaining time. Tz-aware compare to avoid SQLite-naive surprises.
    current_until = account.subscription_active_until
    if current_until is not None and current_until.tzinfo is None:
        current_until = current_until.replace(tzinfo=UTC)
    base = max(current_until, now) if (current_until and current_until > now) else now
    new_active_until = base + timedelta(days=SUBSCRIPTION_PERIOD_DAYS)

    account.subscription_tier = tier
    account.subscription_active_until = new_active_until
    account.subscription_canceled_at = None  # re-subscription wipes cancel

    tx = Transaction(
        account_id=account_id,
        type=TransactionKind.SUBSCRIPTION,
        amount_kopecks=price_kopecks,
        ref_id=ref_id,
        meta={
            "kind": "subscription_charge",
            "tier": tier,
            "yookassa_payment_id": payment_id,
            "period_start": now.isoformat(),
            "period_end": new_active_until.isoformat(),
        },
    )
    db.add(tx)
    await db.flush()

    log.info(
        "subscription_activated",
        account_id=str(account_id),
        tier=tier,
        active_until=new_active_until.isoformat(),
        payment_id=payment_id,
    )
    return True


async def cancel_subscription(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    now: datetime | None = None,
) -> bool:
    """Set ``canceled_at = now`` without changing ``active_until``. Caller commits.

    The subscription keeps working through the end of the paid period (CEO
    2026-05-15 — pro-rata refund is out of scope, "use what you paid for"
    is the policy). Phase 2 cron will then NOT auto-renew at active_until.

    Returns True iff cancel was applied. False if the account has no active
    paid subscription (no-op, caller can return 400 or 200 as appropriate).
    """
    account = (
        await db.execute(select(Account).where(Account.id == account_id).with_for_update())
    ).scalar_one_or_none()
    if account is None:
        return False
    if account.subscription_tier not in PAID_TIERS:
        return False
    if account.subscription_canceled_at is not None:
        # Already cancelled — keep timestamps stable, but tell the caller
        # nothing changed.
        return False

    account.subscription_canceled_at = now or datetime.now(UTC)
    await db.flush()
    log.info(
        "subscription_cancelled",
        account_id=str(account_id),
        tier=account.subscription_tier,
        active_until=(
            account.subscription_active_until.isoformat()
            if account.subscription_active_until
            else None
        ),
    )
    return True


__all__ = [
    "PAID_TIERS",
    "SUBSCRIPTION_PERIOD_DAYS",
    "SUB_REF_ID_TEMPLATE",
    "TIER_PAYG",
    "TIER_PRICE_KOPECKS",
    "TIER_PRO",
    "TIER_TEAM",
    "SubscriptionState",
    "Tier",
    "activate_subscription",
    "build_sub_ref_id",
    "cancel_subscription",
    "is_subscription_active",
    "price_for_tier",
]
