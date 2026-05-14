"""billing — autorefill columns on accounts + processed_webhooks idempotency table

Revision ID: 0002_billing
Revises: 0001_initial
Create Date: 2026-04-29

Adds:
* ``accounts.autorefill_enabled`` (bool, default false) — master switch.
* ``accounts.autorefill_pm_id`` (varchar 128, null) — ЮKassa saved-card id.
* ``accounts.autorefill_threshold_kopecks`` (BigInteger, null) — refill below.
* ``accounts.autorefill_topup_kopecks`` (BigInteger, null) — refill amount.
* ``processed_webhooks`` table — belt-and-braces idempotency for webhooks.
  We already enforce idempotency via ``transactions.ref_id`` but for
  non-financial webhook events (e.g. ``payment.canceled``) we still want a
  duplicate-detection record without touching the ledger.
* Composite ``UNIQUE(account_id, ref_id)`` on ``transactions`` — so the
  database itself rejects two webhooks crediting the same payment_id twice
  (defence in depth — application-side check is in
  ``billing/engine.py::credit_account``).

All DDL goes through ``migration_helpers`` so a partial run survives
re-execution. See helpers module for the trade-offs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    add_column_idempotent,
    create_index_idempotent,
    create_table_idempotent,
    create_unique_constraint_idempotent,
    has_constraint,
    has_index,
)

revision: str = "0002_billing"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # accounts: autorefill columns. ``batch=True`` for SQLite makes the
    # add_column work despite SQLite's lack of true ALTER TABLE.
    add_column_idempotent(
        "accounts",
        sa.Column(
            "autorefill_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column("autorefill_pm_id", sa.String(128), nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column("autorefill_threshold_kopecks", sa.BigInteger, nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column("autorefill_topup_kopecks", sa.BigInteger, nullable=True),
        batch=is_sqlite,
    )

    # processed_webhooks — idempotency for non-financial events
    create_table_idempotent(
        "processed_webhooks",
        sa.Column("payment_id", sa.String(255), primary_key=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="yookassa"),
    )
    create_index_idempotent(
        "ix_processed_webhooks_first_seen", "processed_webhooks", ["first_seen_at"]
    )

    # Defence in depth — DB enforces (account_id, ref_id) uniqueness.
    # Existing rows with NULL ref_id are kept; the partial constraint applies
    # only when ref_id IS NOT NULL on Postgres. SQLite has no partial unique;
    # in tests we accept the rare collision.
    if is_sqlite:
        # SQLite: full unique constraint via batch_alter_table.
        create_unique_constraint_idempotent(
            "uq_transactions_account_ref",
            "transactions",
            ["account_id", "ref_id"],
            batch=True,
        )
    else:
        # Postgres: partial unique index that ignores NULL ref_id rows.
        # has_index check makes this idempotent.
        if not has_index("transactions", "uq_transactions_account_ref"):
            op.execute(
                sa.text(
                    "CREATE UNIQUE INDEX uq_transactions_account_ref "
                    "ON transactions (account_id, ref_id) WHERE ref_id IS NOT NULL"
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        if has_constraint("transactions", "uq_transactions_account_ref"):
            with op.batch_alter_table("transactions") as batch:
                batch.drop_constraint("uq_transactions_account_ref", type_="unique")
    else:
        op.execute(sa.text("DROP INDEX IF EXISTS uq_transactions_account_ref"))

    if has_index("processed_webhooks", "ix_processed_webhooks_first_seen"):
        op.drop_index("ix_processed_webhooks_first_seen", table_name="processed_webhooks")
    op.drop_table("processed_webhooks")

    if is_sqlite:
        with op.batch_alter_table("accounts") as batch:
            batch.drop_column("autorefill_topup_kopecks")
            batch.drop_column("autorefill_threshold_kopecks")
            batch.drop_column("autorefill_pm_id")
            batch.drop_column("autorefill_enabled")
    else:
        op.drop_column("accounts", "autorefill_topup_kopecks")
        op.drop_column("accounts", "autorefill_threshold_kopecks")
        op.drop_column("accounts", "autorefill_pm_id")
        op.drop_column("accounts", "autorefill_enabled")
