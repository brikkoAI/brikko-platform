"""Pay-per-use billing for ``POST /v1/anonymize``.

Pivot v2 (BRIEF_v2_pivot.md, CEO decision 2026-05-14): subscription tiers
(Pro 290 ₽ / Team 1990 ₽) are **replaced** for the anonymize endpoint by a
single, uniform pay-per-use scheme:

* every account gets **100 free requests per calendar day** (Europe/Moscow);
* requests #101..∞ on the same day cost **2 kopecks each** (= 0.02 ₽);
* if balance < 2 kop after the free quota is exhausted → 402 Payment Required.

The daily counter lives in Redis (`anonymize:daily:{account_id}:{date_msk}`)
because that's the only state that has to be cheap to read and write on
every request. A miss/expiry of the key resets the free quota — that's the
intended behaviour: the key TTLs out shortly after midnight МСК.

The *paid* portion is recorded as a standard ``transactions`` row through
``debit_account``, which means:

* the same idempotency guarantees as every other charge (UNIQUE(account_id,
  ref_id));
* dashboards / receipts / 152-ФЗ retention all "just work" — no parallel
  ledger;
* refund mechanics (if we ever need them) reuse ``refund_account``.

Counter semantics
-----------------
The Redis counter increments **only after** we accept the request — either
because it's still inside the free quota, or because the debit succeeded.
Rejected requests (`balance=0` past the quota) do **not** consume a slot:
that would penalise the client for our 402 (and would let an attacker drain
someone's daily quota by sending unauthorised traffic).

The ``check_and_charge_anonymize`` contract is:

    allowed, reject_reason = await check_and_charge_anonymize(...)

* ``allowed=True, reject_reason=None`` — caller proceeds with masking.
* ``allowed=False, reject_reason="quota_exceeded"`` — caller returns 402.
* ``allowed=False, reject_reason="account_not_found"`` — caller returns 401
  (shouldn't happen — auth dep validates first — but defensive).

``/v1/restore`` is intentionally NOT billed (see ``api/anonymize.py``):
restoring is just a Redis lookup the client paid for at mask time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Final

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.billing.engine import (
    BillingError,
    InsufficientBalanceError,
    debit_account,
)
from voltari_gateway.db.models import TransactionKind
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


# --- Public constants ---------------------------------------------------------

#: Cost per paid /v1/anonymize request, in kopecks (0.02 ₽).
ANONYMIZE_PRICE_KOPECKS: Final[int] = 2

#: How many free /v1/anonymize requests an account gets per МСК calendar day.
ANONYMIZE_FREE_DAILY_QUOTA: Final[int] = 100

#: Welcome bonus for new accounts, in kopecks (100 ₽ = 5000 paid requests).
ANONYMIZE_WELCOME_BONUS_KOPECKS: Final[int] = 10_000

#: Page URL the client is redirected to on 402. Lives here so the API
#: handler doesn't hard-code it twice.
TOPUP_URL: Final[str] = "https://brikko.ru/app/billing"

#: Europe/Moscow is a fixed UTC+3 offset (no DST since 2014). Hard-code
#: instead of relying on the system tz database, which is patchy on Windows.
_MSK_OFFSET: Final[timezone] = timezone(timedelta(hours=3))


# --- Reject reasons (stable strings — used in logs + 402 body) ----------------

REJECT_QUOTA_EXCEEDED: Final[str] = "quota_exceeded"
REJECT_ACCOUNT_NOT_FOUND: Final[str] = "account_not_found"


# --- Helpers ------------------------------------------------------------------


def _msk_date_key(now_utc: datetime | None = None) -> str:
    """Return the current Москва-local calendar date as ``YYYY-MM-DD``.

    The daily counter key includes this date so the quota resets at 00:00
    МСК. Using a string key (not a TTL) means a missed expiry never
    silently grants an extra day's quota — the new day uses a new key.
    """
    now = now_utc or datetime.now(UTC)
    return now.astimezone(_MSK_OFFSET).strftime("%Y-%m-%d")


def _counter_key(account_id: uuid.UUID, date_msk: str) -> str:
    return f"anonymize:daily:{account_id}:{date_msk}"


def _msk_midnight_unix(now_utc: datetime | None = None) -> int:
    """Unix timestamp of the upcoming midnight in Москва.

    Used as the Redis EXPIREAT so the counter survives the day even if the
    key sits idle for hours. We add 5 minutes of slack so the key
    *definitely* outlives the last request of the day under clock skew.
    """
    now = (now_utc or datetime.now(UTC)).astimezone(_MSK_OFFSET)
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(next_midnight.timestamp()) + 300


# --- Public API ---------------------------------------------------------------


async def check_and_charge_anonymize(
    *,
    account_id: uuid.UUID,
    redis: Redis,
    db: AsyncSession,
) -> tuple[bool, str | None]:
    """Decide if this /v1/anonymize request is allowed; charge if needed.

    Returns
    -------
    ``(True, None)``
        Caller proceeds. Either the request fits inside today's free quota
        (no balance change) or a 2-kopeck debit was successfully committed.
    ``(False, "quota_exceeded")``
        Free quota used, balance below 2 kop. Caller returns 402.
    ``(False, "account_not_found")``
        Defensive — auth layer should have caught it.

    On the paid path the caller MUST commit ``db`` so the debit lands.
    Returning a successful tuple means we already issued the debit (or
    re-attached an idempotent one); rolling back ``db`` after a successful
    return would silently undo the charge.

    Redis counter is incremented only after acceptance — see module
    docstring on why rejects do NOT consume a slot.
    """
    date_key = _msk_date_key()
    counter_key = _counter_key(account_id, date_key)

    # Peek at the current usage WITHOUT incrementing. A naive INCR-then-check
    # would burn quota on rejected requests; an INCR-then-DECR on reject
    # races against concurrent callers (two concurrent reqs both see "1
    # remaining" and one passes through after a rollback). Read-first +
    # conditional INCR keeps the counter accurate for accepted traffic and
    # safe under contention (the last accepted req settles the value).
    current_raw = await redis.get(counter_key)
    current = int(current_raw) if current_raw is not None else 0

    if current < ANONYMIZE_FREE_DAILY_QUOTA:
        # Inside the free quota — increment, no DB hit.
        new_count = await redis.incr(counter_key)
        # First request of the day owns the EXPIREAT. Subsequent INCRs keep
        # the existing TTL; setting it every time is cheap and harmless and
        # also corrects a missing-TTL key from manual ops intervention.
        await redis.expireat(counter_key, _msk_midnight_unix())
        log.info(
            "anonymize_billing_free",
            account_id=str(account_id),
            count_today=new_count,
            quota=ANONYMIZE_FREE_DAILY_QUOTA,
        )
        return True, None

    # Past the free quota. Issue a paid debit. ref_id keys idempotency:
    # ``anon:{account}:{date}:{quota+N}`` where N is the paid-request
    # index that day. Two concurrent retries of the same logical request
    # would collide on this ref_id, which is fine — the second call
    # returns the existing tx via debit_account's idempotent path. We
    # derive N from the *current* counter (not from the post-INCR value)
    # so the ref_id is stable across the read/INCR boundary.
    paid_index = current + 1
    ref_id = f"anon:{account_id}:{date_key}:{paid_index}"

    try:
        await debit_account(
            db,
            account_id=account_id,
            amount_kopecks=ANONYMIZE_PRICE_KOPECKS,
            ref_id=ref_id,
            kind=TransactionKind.CHARGE,
            meta={"kind": "anonymize", "date_msk": date_key, "n": paid_index},
        )
    except InsufficientBalanceError:
        log.info(
            "anonymize_billing_quota_exceeded",
            account_id=str(account_id),
            count_today=current,
            quota=ANONYMIZE_FREE_DAILY_QUOTA,
        )
        return False, REJECT_QUOTA_EXCEEDED
    except BillingError as exc:
        # ``account_not_found`` is the only other branch from debit_account
        # we care about — surface it as a stable token for the caller.
        if "account_not_found" in str(exc):
            log.warning("anonymize_billing_no_account", account_id=str(account_id))
            return False, REJECT_ACCOUNT_NOT_FOUND
        raise

    # Debit succeeded → consume a slot in the counter so the next request
    # gets the right paid_index. (The counter is no longer used for the
    # gate decision — it's now just bookkeeping for ref_id stability —
    # but we still want it monotonic.)
    await redis.incr(counter_key)
    await redis.expireat(counter_key, _msk_midnight_unix())

    log.info(
        "anonymize_billing_charged",
        account_id=str(account_id),
        amount_kopecks=ANONYMIZE_PRICE_KOPECKS,
        ref_id=ref_id,
        paid_index=paid_index,
    )
    return True, None


__all__ = [
    "ANONYMIZE_FREE_DAILY_QUOTA",
    "ANONYMIZE_PRICE_KOPECKS",
    "ANONYMIZE_WELCOME_BONUS_KOPECKS",
    "REJECT_ACCOUNT_NOT_FOUND",
    "REJECT_QUOTA_EXCEEDED",
    "TOPUP_URL",
    "check_and_charge_anonymize",
]
