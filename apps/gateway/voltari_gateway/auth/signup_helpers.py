"""Shared building blocks for password signup + OAuth signup.

Why a helper module
-------------------

Per CEO 2026-05-06: OAuth signup must NOT be a parallel pathway to
password signup. Both flows must use the same:

* User+Account row creation logic.
* Welcome credit grant (200 ₽, idempotent on email_hash).
* Audit hooks.

The password endpoint in ``api/auth.py:signup`` predates this helper —
it inlines everything. Rather than rip it apart in this PR (large diff,
risk of breaking signup tests), we extract the *common* primitives here
and have the OAuth callback call them. Future TD: refactor the password
``signup()`` to call ``create_user_and_primary_account()`` too — covered
by ``apps/gateway/TECH_DEBT.md``.

What lives here
---------------

* :func:`create_user_and_primary_account` — User + Account rows. Does
  NOT commit. Returns the in-session pair so the caller can write
  audit / OAuth identity rows in the same transaction.
* :func:`grant_welcome_credit_by_email` — same primitive used by
  ``/verify-email``, factored out so both call sites share the
  email-hash idempotency key.

Both helpers are caller-commit. The atomicity contract is "the welcome
credit + the account creation + the OAuth identity all land in one
transaction, or none of them do".
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.billing.anonymize_billing import ANONYMIZE_WELCOME_BONUS_KOPECKS
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    Transaction,
    TransactionKind,
    User,
    WelcomeCreditsLog,
)
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

# Mirrors ``api/auth.py:WELCOME_CREDIT_KOPECKS``. Single source of truth
# moved here so the welcome-credit grant doesn't fork between flows.
WELCOME_CREDIT_KOPECKS: Final[int] = 20_000


def normalise_email(raw: str) -> str:
    """Lower-case + strip — matches User.email storage rules."""
    return raw.strip().lower()


def email_hash(email: str) -> str:
    """SHA-256 of normalised email — used as the welcome-credit dedup PK."""
    return hashlib.sha256(normalise_email(email).encode("utf-8")).hexdigest()


def ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


async def create_user_and_primary_account(
    db: AsyncSession,
    *,
    email: str,
    password_hash: str | None,
    email_verified: bool,
    acquisition_channel: str | None = None,
    utm_source: str | None = None,
    utm_medium: str | None = None,
    utm_campaign: str | None = None,
) -> tuple[User, Account]:
    """Create a User + their primary Account.

    ``password_hash`` may be ``None`` for OAuth-only signup. The DB column
    is currently NOT NULL (legacy from password-only signup), so when the
    caller has no password we generate a *known-unusable* placeholder
    hash — ``argon2id$…`` shaped so the schema is happy but no input can
    ever match it. This keeps the column nullable=False semantics intact
    without a schema migration in the same PR.

    Returns the (user, account) pair. Caller MUST flush/commit; this
    helper does neither.
    """
    user = User(
        email=normalise_email(email),
        password_hash=password_hash if password_hash else _unusable_password_hash(),
        email_verified=email_verified,
    )
    db.add(user)
    await db.flush()  # populate user.id

    account = Account(
        owner_id=user.id,
        # Same convention as password signup: local-part of email.
        name=email.split("@")[0] or "Personal",
        # Anonymize welcome bonus (BRIEF v2 §5, CEO 2026-05-14). The legacy
        # verify-email 200 ₽ welcome stays a separate flow; this 100 ₽ is
        # specifically for the pay-per-use /v1/anonymize ledger and lands
        # at account creation so the client sees it immediately.
        balance_kopecks=ANONYMIZE_WELCOME_BONUS_KOPECKS,
        tariff=Tariff.PAYG,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
        acquisition_channel=acquisition_channel,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
    )
    db.add(account)
    await db.flush()

    # Ledger row for the anonymize signup bonus. Separate ref_id from the
    # verify-email welcome 200 ₽ — never collides on UNIQUE(account_id, ref_id).
    db.add(
        Transaction(
            account_id=account.id,
            type=TransactionKind.TOPUP,
            amount_kopecks=ANONYMIZE_WELCOME_BONUS_KOPECKS,
            ref_id=f"welcome-anonymize:{account.id}",
            meta={"kind": "welcome_anonymize"},
        )
    )
    await db.flush()
    return user, account


async def grant_welcome_credit_by_email(
    db: AsyncSession,
    *,
    user: User,
    email: str,
    ip: str | None,
) -> bool:
    """Insert into ``welcome_credits_log`` (PK=email_hash) + top up balance.

    Returns True iff this call actually credited. Idempotent against a
    concurrent grant (UNIQUE PK) and against delete-and-resignup with
    the same email (PK is the email hash, not user_id).

    Caller is responsible for ``await db.commit()``.
    """
    eh = email_hash(email)
    ih = ip_hash(ip)

    try:
        await db.execute(
            insert(WelcomeCreditsLog).values(
                email_hash=eh,
                ip_hash=ih,
                granted_at=datetime.now(UTC),
            )
        )
        await db.flush()
    except IntegrityError:
        # PK conflict → already credited.
        await db.rollback()
        return False

    # Top up the user's primary account.
    result = await db.execute(
        select(Account)
        .where(Account.owner_id == user.id)
        .order_by(Account.created_at.asc())
        .limit(1)
    )
    account = result.scalar_one_or_none()
    if account is None:
        log.error("welcome_credit_no_account", user_id=str(user.id))
        return False

    account.balance_kopecks = (account.balance_kopecks or 0) + WELCOME_CREDIT_KOPECKS
    db.add(
        Transaction(
            account_id=account.id,
            type=TransactionKind.TOPUP,
            amount_kopecks=WELCOME_CREDIT_KOPECKS,
            ref_id=f"welcome:{user.id}",
            meta={"kind": "welcome", "email_hash": eh},
        )
    )
    return True


def _unusable_password_hash() -> str:
    """An argon2id-shaped hash that no input can match.

    OAuth-only users have no password and must complete
    ``/forgot-password`` before they can switch to password login.
    The hash format is intentionally well-formed (so schema validators
    pass) but the salt+digest are deterministic placeholders, which
    means ``verify_password(any_password, this_hash)`` is always False.
    """
    return (
        "$argon2id$v=19$m=65536,t=2,p=2$b2F1dGgtb25seQ$XX0000000000000000000000000000000000000000"
    )


__all__ = [
    "WELCOME_CREDIT_KOPECKS",
    "create_user_and_primary_account",
    "email_hash",
    "grant_welcome_credit_by_email",
    "ip_hash",
    "normalise_email",
]
