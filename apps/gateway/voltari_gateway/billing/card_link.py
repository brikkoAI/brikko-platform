"""Card linking flow — verify card via 1 ₽ + auto-refund + welcome 100 ₽.

Pay-per-use → subscription pivot (CEO 2026-05-15, BRIEF_v2_pivot.md). The
PAYG tariff is reduced to **welcome credits only**:

  * 100 ₽ at signup (existing flow, see ``billing.anonymize_billing``)
  * 100 ₽ when the user links a card (this flow)

Card linking serves three goals:

  1. Activator. A linked card halves the friction for the eventual Pro/Team
     subscription click (one tap to subscribe vs. card entry flow).
  2. Onboarding signal. Card-linked users have higher conversion to paid;
     we want to track the funnel step explicitly.
  3. Trust mechanism. ЮKassa's verification charge (1 ₽) confirms the
     card is real + the issuing bank approves the merchant. We refund
     the 1 ₽ immediately so the user isn't out of pocket.

Why 1 ₽ and not a 0 ₽ "verify" call
-----------------------------------

ЮKassa does not expose a free "tokenize-only" endpoint. ``save_payment_method=true``
needs an actual capture to mint a ``payment_method.id``. The smallest
practical charge is 1 ₽. We refund it as soon as the webhook fires.

Idempotency strategy
--------------------

Two layers:

* ``account.autorefill_pm_id IS NOT NULL`` — the "card already linked" gate
  rejects duplicate POSTs to ``/v1/billing/link-card``.
* ``Transaction(ref_id="welcome_card_link:{account_id}")`` — the UNIQUE on
  (account_id, ref_id) is the backstop. Even if the
  ``autorefill_pm_id`` check is bypassed (concurrent webhooks), the credit
  can land **at most once** per account_id.

The combination means: a user can NOT receive the 100 ₽ welcome by linking,
removing the card, and re-linking — the ref_id is account-scoped, not
payment-scoped.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.db.models import Account, Transaction, TransactionKind
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


# --- Public constants ---------------------------------------------------------

#: Amount we charge during card-link verification (refunded immediately on
#: webhook). Minimal practical value — ЮKassa rejects 0 ₽ payments.
CARD_LINK_VERIFY_AMOUNT_KOPECKS: Final[int] = 100  # 1 ₽

#: Welcome credit granted on successful card link. CEO 2026-05-15 — second
#: half of the "200 ₽ welcome" envelope (the first 100 ₽ lands at signup,
#: see ``billing.anonymize_billing.ANONYMIZE_WELCOME_BONUS_KOPECKS``).
WELCOME_CARD_LINK_BONUS_KOPECKS: Final[int] = 10_000  # 100 ₽

#: Marker in webhook metadata so the handler routes the 1 ₽ payment through
#: the card-link flow instead of treating it as a regular topup.
CARD_LINK_PURPOSE: Final[str] = "card_link_verification"

#: Stable ref_id template for the welcome credit transaction. Account-scoped
#: so a user can not "rotate cards" to re-claim the welcome.
WELCOME_CARD_LINK_REF_ID: Final[str] = "welcome_card_link:{account_id}"

#: Stable ref_id template for the refund of the 1 ₽ verification charge.
#: Payment-scoped because each card-link attempt produces its own refund row.
CARD_LINK_REFUND_REF_ID: Final[str] = "card_link_refund:{payment_id}"


async def has_card_already_linked(db: AsyncSession, account: Account) -> bool:
    """Return True if this account already has ``autorefill_pm_id`` set."""
    return account.autorefill_pm_id is not None


async def has_received_card_link_welcome(
    db: AsyncSession, account_id: uuid.UUID
) -> bool:
    """Return True if the account already redeemed the card-link welcome.

    Independent of ``autorefill_pm_id`` — we want to block re-grant even if
    the user removed their card. The ref_id pin is account-scoped, so this
    is a precise existence check on the transactions ledger.
    """
    ref_id = WELCOME_CARD_LINK_REF_ID.format(account_id=account_id)
    existing = (
        await db.execute(
            select(Transaction.id).where(
                Transaction.account_id == account_id,
                Transaction.ref_id == ref_id,
            )
        )
    ).first()
    return existing is not None


async def credit_card_link_welcome(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    payment_method_id: str,
) -> tuple[bool, int]:
    """Save ``payment_method_id`` + credit 100 ₽. Caller must commit.

    Returns
    -------
    (granted, new_balance_kopecks)
        ``granted=True`` iff this call actually wrote a new welcome credit
        row. False on idempotent replay (UNIQUE collision on ref_id).
        ``new_balance_kopecks`` is the balance after the call — useful for
        the API response.

    Idempotency: ref_id is account-scoped. Two concurrent webhooks for the
    same account collide on the UNIQUE(account_id, ref_id); the loser sees
    IntegrityError, returns ``granted=False``, and the caller treats the
    payment as already-processed.

    Side effects in this single DB transaction:

      1. ``account.autorefill_pm_id = payment_method_id``
      2. ``account.balance_kopecks += 10_000``
      3. INSERT INTO transactions (kind=TOPUP, ref_id=welcome_card_link:{id})

    The caller is responsible for ``await db.commit()`` so this composes
    with a wider request-scope transaction (e.g. the webhook handler
    writes a ``processed_webhooks`` row in the same commit).
    """
    account = (
        await db.execute(select(Account).where(Account.id == account_id).with_for_update())
    ).scalar_one_or_none()
    if account is None:
        log.warning("card_link_welcome_no_account", account_id=str(account_id))
        return False, 0

    # Always save the pm_id — even on a replay, the value is identical.
    # Idempotent set: ЮKassa returns the same payment_method.id on a
    # retried webhook for the same payment_id.
    account.autorefill_pm_id = payment_method_id

    ref_id = WELCOME_CARD_LINK_REF_ID.format(account_id=account_id)
    existing = (
        await db.execute(
            select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.ref_id == ref_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Idempotent replay — pm_id was set above (no-op if already set), but
        # we do NOT credit again.
        log.info(
            "card_link_welcome_replay",
            account_id=str(account_id),
            existing_tx_id=str(existing.id),
        )
        return False, account.balance_kopecks

    account.balance_kopecks = (account.balance_kopecks or 0) + WELCOME_CARD_LINK_BONUS_KOPECKS
    tx = Transaction(
        account_id=account_id,
        type=TransactionKind.TOPUP,
        amount_kopecks=WELCOME_CARD_LINK_BONUS_KOPECKS,
        ref_id=ref_id,
        meta={"kind": "welcome_card_link", "payment_method_id": payment_method_id},
    )
    db.add(tx)
    try:
        await db.flush()
    except IntegrityError:
        # Race: a concurrent webhook landed first. Roll back our +100 ₽ and
        # treat as an idempotent replay. The other caller's commit is the
        # canonical credit.
        await db.rollback()
        log.info(
            "card_link_welcome_race_lost",
            account_id=str(account_id),
        )
        return False, 0

    log.info(
        "card_link_welcome_granted",
        account_id=str(account_id),
        amount_kopecks=WELCOME_CARD_LINK_BONUS_KOPECKS,
        new_balance=account.balance_kopecks,
    )
    return True, account.balance_kopecks


__all__ = [
    "CARD_LINK_PURPOSE",
    "CARD_LINK_REFUND_REF_ID",
    "CARD_LINK_VERIFY_AMOUNT_KOPECKS",
    "WELCOME_CARD_LINK_BONUS_KOPECKS",
    "WELCOME_CARD_LINK_REF_ID",
    "credit_card_link_welcome",
    "has_card_already_linked",
    "has_received_card_link_welcome",
]
