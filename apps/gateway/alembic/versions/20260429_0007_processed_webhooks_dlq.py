"""processed_webhooks: status + error_message (DLQ for failed webhooks)

Revision ID: 0007_processed_webhooks_dlq
Revises: 0006_transaction_amount_check
Create Date: 2026-04-29

Why
---

BE P0-6: the YooKassa webhook used to ``rollback`` and return **200** on
``BillingError`` (e.g. account_id in metadata doesn't exist). YooKassa
saw 200 → never retries → money landed on its side, our ledger never
recorded the credit, support hears about it from the customer days
later.

The new webhook (Sprint 2) returns **500** on transient/recoverable
``BillingError`` so YooKassa retries N times, with a row in
``processed_webhooks`` carrying ``status='failed'`` + ``error_message``
so an operator has a place to inspect after retries are exhausted.

Migration adds two columns:

* ``status`` (varchar 16, default ``processed``) — ``'processed' | 'failed'``,
  enforced by a CHECK constraint named ``ck_processed_webhooks_status``.
* ``error_message`` (varchar 1024, nullable) — last error captured when
  status flipped to ``failed``. Bounded to keep a single bad payload
  from blowing up storage.

We also add an index on ``status`` so the dashboard query
``SELECT ... WHERE status = 'failed'`` is fast even when the table grows.

The migration is idempotent (uses ``add_column_idempotent`` /
``add_check_constraint_idempotent`` / ``create_index_idempotent``) so
mid-run crashes on rerun.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    add_check_constraint_idempotent,
    add_column_idempotent,
    create_index_idempotent,
    has_constraint,
    has_index,
)

revision: str = "0007_processed_webhooks_dlq"
down_revision: str | None = "0006_transaction_amount_check"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    add_column_idempotent(
        "processed_webhooks",
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="processed",
        ),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "processed_webhooks",
        sa.Column("error_message", sa.String(1024), nullable=True),
        batch=is_sqlite,
    )

    add_check_constraint_idempotent(
        "ck_processed_webhooks_status",
        "processed_webhooks",
        "status IN ('processed','failed')",
        batch=is_sqlite,
    )

    create_index_idempotent("ix_processed_webhooks_status", "processed_webhooks", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if has_index("processed_webhooks", "ix_processed_webhooks_status"):
        op.drop_index("ix_processed_webhooks_status", table_name="processed_webhooks")

    if has_constraint("processed_webhooks", "ck_processed_webhooks_status"):
        if is_sqlite:
            with op.batch_alter_table("processed_webhooks") as bop:
                bop.drop_constraint("ck_processed_webhooks_status", type_="check")
        else:
            op.drop_constraint(
                "ck_processed_webhooks_status",
                "processed_webhooks",
                type_="check",
            )

    if is_sqlite:
        with op.batch_alter_table("processed_webhooks") as bop:
            bop.drop_column("error_message")
            bop.drop_column("status")
    else:
        op.drop_column("processed_webhooks", "error_message")
        op.drop_column("processed_webhooks", "status")
