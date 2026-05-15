"""subscription_renewal_tracking — add dunning columns + reminders log table.

Revision ID: 0025_renewal_tracking
Revises: 0024_subscription_columns
Create Date: 2026-05-15

Phase 2 of the pay-per-use → subscription pivot (CEO 2026-05-15). Phase 1
shipped the lifecycle columns (``subscription_tier`` / ``active_until`` /
``canceled_at``); this revision wires in the bits the cron jobs need to
do safe retries and idempotent reminders:

  accounts.renewal_retry_count    : INT NOT NULL DEFAULT 0
      Number of consecutive renewal-charge failures. Reset to 0 on success.
      After 3 fails the cron downgrades the tier to 'payg' and emails the
      user with a "link a new card" CTA.

  accounts.renewal_last_failed_at : TIMESTAMPTZ NULL
      Timestamp of the most recent failed renewal attempt — drives the
      Stripe-style retry schedule (T+0 / T+24h / T+72h).

  subscription_reminders_sent     : audit / idempotency table
      Records "we already emailed this account about this renewal
      period". Without this table, two ``send_renewal_reminders`` ticks
      in the same day (e.g. crash-restart of the cron container) would
      spam the customer twice. Period anchor is the
      ``subscription_active_until`` *date* — even if active_until
      jitters by a few seconds between calls (it shouldn't, but be
      defensive), the date-precision key stays stable.

Dialect notes
-------------

* Postgres: ``ADD COLUMN`` with a constant DEFAULT is metadata-only on
  PG 11+. The new table is a tiny audit log.
* SQLite: column adds go through ``add_column_idempotent(batch=True)``
  on accounts; the new table is a vanilla CREATE TABLE.

Downgrade drops the columns + table. Idempotent — re-running ``upgrade``
after a partial apply is safe (each operation checks existence first).
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    add_column_idempotent,
    create_index_idempotent,
    create_table_idempotent,
    has_table,
)

revision: str = "0025_renewal_tracking"
down_revision: str | None = "0024_subscription_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    add_column_idempotent(
        "accounts",
        sa.Column(
            "renewal_retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        batch=is_sqlite,
    )

    add_column_idempotent(
        "accounts",
        sa.Column(
            "renewal_last_failed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        batch=is_sqlite,
    )

    # Audit / idempotency log: (account_id, period_date, reminder_type) is
    # the natural key for "did we already remind this user". We model it
    # as UNIQUE so a duplicate INSERT raises IntegrityError that the cron
    # catches and treats as "already sent".
    create_table_idempotent(
        "subscription_reminders_sent",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "account_id",
            sa.dialects.postgresql.UUID(as_uuid=True) if not is_sqlite else sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reminder_type",
            sa.String(32),
            nullable=False,
            comment="renewal_t_minus_3 | renewal_failed | renewal_downgraded",
        ),
        # Date-precision anchor — see module docstring for why.
        sa.Column(
            "period_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "account_id",
            "reminder_type",
            "period_date",
            name="uq_subscription_reminders_sent",
        ),
    )

    # Lookup index for the cron sweep ("did we already send for this
    # account/period?"). The UNIQUE above already provides a btree, but a
    # named index makes the EXPLAIN plan obvious.
    create_index_idempotent(
        "ix_subscription_reminders_sent_account",
        "subscription_reminders_sent",
        ["account_id", "period_date"],
    )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if has_table("subscription_reminders_sent"):
        with contextlib.suppress(Exception):
            op.drop_index(
                "ix_subscription_reminders_sent_account",
                table_name="subscription_reminders_sent",
            )
        with contextlib.suppress(Exception):
            op.drop_table("subscription_reminders_sent")

    if is_sqlite:
        with op.batch_alter_table("accounts") as bop:
            for col in ("renewal_last_failed_at", "renewal_retry_count"):
                with contextlib.suppress(Exception):
                    bop.drop_column(col)
    else:
        for col in ("renewal_last_failed_at", "renewal_retry_count"):
            with contextlib.suppress(Exception):
                op.drop_column("accounts", col)
