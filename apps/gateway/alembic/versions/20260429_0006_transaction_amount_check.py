"""transaction sign CHECK — refuse ledger rows with the wrong sign

Revision ID: 0006_transaction_amount_check
Revises: 0005_account_holds
Create Date: 2026-04-29

What and why
------------

A swapped sign in ``billing/engine.py`` (refund posted as +amount instead of
-amount) is the kind of bug that destroys a ledger silently — balance
doubles instead of decreases, transaction history reads correct, no
exception fires until the account hits some threshold weeks later.

Backend P0-2 in the 2026-04-29 audit asked for a database-side guard so
the DB itself refuses the impossible rows. The CASE-style CHECK below
mirrors the sign convention documented in ``billing/engine.py`` and
``billing/constants.py``:

* ``topup`` / ``subscription`` / ``autorefill`` → strictly > 0
* ``charge``                                    → strictly < 0
* ``refund``                                    → != 0 (either direction)

Migration safety
----------------

If the table already contains rows that violate the new CHECK, this
migration would fail. We don't have such rows in production yet (Sprint 1
introduced the engine that always writes the correct sign), but for
defence-in-depth we run a sanity SELECT *before* attempting to add the
constraint and abort with a clear error message if anything pre-existing
would block it.

Idempotent on the constraint name (uses ``add_check_constraint_idempotent``)
so a crash mid-migration is recoverable on rerun. SQLite goes through
batch_alter_table because it can't ALTER an existing table to add a
constraint — it rebuilds the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.billing.constants import TRANSACTION_AMOUNT_SIGN_CONSTRAINT
from voltari_gateway.db.migration_helpers import (
    add_check_constraint_idempotent,
    has_constraint,
)

revision: str = "0006_transaction_amount_check"
down_revision: str | None = "0005_account_holds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The constraint condition. Written as a single SQL expression so it works
# unchanged on both SQLite (test) and PostgreSQL (prod). Enum values match
# ``TransactionKind.value`` in ORM models.
_CONDITION = (
    "(type IN ('topup','subscription','autorefill') AND amount_kopecks > 0) "
    "OR (type = 'charge' AND amount_kopecks < 0) "
    "OR (type = 'refund' AND amount_kopecks != 0)"
)


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # Pre-flight: refuse to add the constraint if the table already contains
    # rows that violate it. This gives a clear error the DBA can act on,
    # rather than alembic surfacing an opaque CheckViolation deep in the
    # ALTER TABLE.
    if not is_sqlite:
        bad_rows = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM transactions WHERE NOT ({_CONDITION})")
        ).scalar()
        if bad_rows and int(bad_rows) > 0:
            raise RuntimeError(
                f"Cannot add {TRANSACTION_AMOUNT_SIGN_CONSTRAINT}: "
                f"{bad_rows} pre-existing row(s) violate the new sign rule. "
                "Investigate `transactions` for swapped-sign entries before "
                "rerunning this migration."
            )

    add_check_constraint_idempotent(
        TRANSACTION_AMOUNT_SIGN_CONSTRAINT,
        "transactions",
        _CONDITION,
        batch=is_sqlite,
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if not has_constraint("transactions", TRANSACTION_AMOUNT_SIGN_CONSTRAINT):
        return

    if is_sqlite:
        with op.batch_alter_table("transactions") as bop:
            bop.drop_constraint(TRANSACTION_AMOUNT_SIGN_CONSTRAINT, type_="check")
    else:
        op.drop_constraint(TRANSACTION_AMOUNT_SIGN_CONSTRAINT, "transactions", type_="check")
