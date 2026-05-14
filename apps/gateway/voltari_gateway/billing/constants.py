"""Money-related constants — single source of truth for billing thresholds.

Why this module exists
----------------------

Numbers like the refund overdraft floor and the (BigInteger-encoded) sign
convention for ledger entries used to be duplicated across:

* the SQLAlchemy model (CheckConstraint),
* the alembic migration (raw SQL),
* the Python billing engine (defensive checks).

Anyone touching one place forgot the other (BE P0-16). The DB and the code
silently disagreed; the bug only surfaced as a database CHECK violation
weeks later when an edge-case refund reached production.

Putting them here doesn't *guarantee* alignment — the migration's CHECK
constraint is still raw SQL — but it makes the divergence loud:

* The constraint name in the DB is also defined here, so anyone grepping
  for the constant finds the migration, the model, and the engine logic
  in one shot.
* The Python code uses the constant directly. A "drift" between
  migration and code now means changing this file without changing the
  migration, which is a clear review signal.

Design notes
------------

* All amounts are in **kopecks** (BigInteger). We never store rubles as
  floats anywhere in the system.
* Sign convention for ``transactions.amount_kopecks``:

    * ``topup`` / ``subscription`` / ``autorefill`` → strictly **> 0**
    * ``charge``                                    → strictly **< 0**
    * ``refund``                                    → **!= 0** (refunds
      can be either direction depending on which side initiated; the
      typical case is negative, but ЮKassa correction events post the
      original sign).

  See ``ck_transactions_amount_sign`` in alembic 0006 for the SQL
  encoding of this rule.
"""

from __future__ import annotations

from typing import Final

# ---- Refund overdraft -------------------------------------------------------

# Allowed technical overdraft after a refund. Matches the
# ``balance_check`` CHECK constraint on ``accounts``: balance can dip up
# to 10 ₽ below zero so a refund-flow doesn't bust the constraint mid-
# transaction. Hard-stop for new charges is enforced separately in code
# (caller checks balance > 0 before creating a hold).
#
# Used by:
#   * ``voltari_gateway/billing/engine.py::refund_account``
#   * ``voltari_gateway/db/models.py::Account.__table_args__``
#   * alembic migration 0001 (CHECK constraint on accounts)
#
# If you change this, update all three. A future migration could lift it
# to a configuration row, but right now consistency > flexibility.
REFUND_OVERDRAFT_FLOOR_KOPECKS: Final[int] = -1000


# ---- Transaction sign constraint --------------------------------------------

# Name of the CHECK constraint that enforces the sign convention on
# ``transactions.amount_kopecks``. Defined in alembic 0006. Exposed as a
# constant so tests can assert against it by name without string-matching
# the SQL.
TRANSACTION_AMOUNT_SIGN_CONSTRAINT: Final[str] = "ck_transactions_amount_sign"


__all__ = [
    "REFUND_OVERDRAFT_FLOOR_KOPECKS",
    "TRANSACTION_AMOUNT_SIGN_CONSTRAINT",
]
