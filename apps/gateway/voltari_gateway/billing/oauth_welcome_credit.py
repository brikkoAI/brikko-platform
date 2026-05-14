"""200 ₽ welcome credit on first OAuth code via the ``studio`` client.

Idempotency strategy
--------------------

* ``Account.is_studio_user`` is the single source of truth.
* The Transaction row uses ``ref_id="studio-welcome-{account_id}"`` —
  the existing UNIQUE on (account_id, ref_id) is a backstop against any
  race that bypasses the flag check.
* The flag → flip + transaction insert run inside the SAME caller
  transaction as the OAuth code creation so a rolled-back consent
  never leaves a stranded credit.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.db.models import Account, Transaction, TransactionKind
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

STUDIO_WELCOME_CREDIT_KOPECKS: Final[int] = 20_000  # 200 ₽
STUDIO_CLIENT_ID: Final[str] = "studio"


async def grant_studio_welcome_credit_if_first_time(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    client_id: str,
) -> bool:
    """Grant 200 ₽ once per account if the Studio client onboards them.

    Returns True iff this call actually credited.

    Caller is responsible for ``await db.commit()``.
    """
    if client_id != STUDIO_CLIENT_ID:
        return False

    account = await db.get(Account, account_id)
    if account is None:
        log.warning("studio_welcome_no_account", account_id=str(account_id))
        return False
    if account.is_studio_user:
        return False  # Already onboarded; flag is the fast path.

    # Flip the flag and record the transaction. The UNIQUE on
    # (account_id, ref_id) protects against the rare race where two
    # OAuth flows complete in parallel before the flag commit lands.
    account.is_studio_user = True
    account.balance_kopecks = (account.balance_kopecks or 0) + STUDIO_WELCOME_CREDIT_KOPECKS

    txn = Transaction(
        account_id=account_id,
        type=TransactionKind.TOPUP,
        amount_kopecks=STUDIO_WELCOME_CREDIT_KOPECKS,
        ref_id=f"studio-welcome-{account_id}",
        meta={"kind": "studio_welcome", "user_id": str(user_id)},
    )
    db.add(txn)
    try:
        await db.flush()
    except IntegrityError:
        # Race: the parallel call won. Roll back our flag/balance change
        # by re-reading + clearing — the parallel call already credited.
        await db.rollback()
        log.info("studio_welcome_race_won_by_other", account_id=str(account_id))
        return False
    return True
