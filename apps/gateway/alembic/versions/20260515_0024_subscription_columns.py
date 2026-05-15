"""subscription_columns — add subscription_tier/active_until/canceled_at to accounts.

Revision ID: 0024_subscription_columns
Revises: 0023_smart_router_v2_flag
Create Date: 2026-05-15

Pay-per-use → subscription pivot (CEO decision 2026-05-15, BRIEF_v2_pivot.md
update). PAYG is reduced to "welcome credits only" (200 ₽ total — 100 ₽
signup + 100 ₽ card-link). The only path to paid usage is a monthly
subscription: Pro 290 ₽/mo or Team 1490 ₽/mo. ``accounts.tariff`` (legacy
enum: PAYG / PRO / PRO_PRIVACY / TEAM / BUSINESS / BUSINESS_PLUS) keeps
existing entitlement semantics — these three new columns are a separate,
orthogonal axis that drives the subscription **billing** state:

  subscription_tier         : 'payg' | 'pro' | 'team' (default 'payg')
  subscription_active_until : datetime | NULL — paid through this moment
  subscription_canceled_at  : datetime | NULL — set on user cancel; the
                              tier keeps working until active_until

The lifecycle is:

  signup            → tier='payg'  active_until=NULL  canceled_at=NULL
  buy Pro           → tier='pro'   active_until=now+30d, canceled_at=NULL
  user clicks Cancel→ tier='pro'   active_until=now+15d, canceled_at=set
  cron Phase 2      → tier='payg'  active_until=NULL,    canceled_at=NULL
  (when active_until elapses with canceled_at set)

Dialect notes
-------------

* Postgres: ``ALTER TABLE accounts ADD COLUMN`` with a constant DEFAULT is
  a metadata-only operation since PG 11. Safe on a populated table.
* SQLite: the helper routes through ``batch_alter_table`` if necessary.

Down-migration drops all three columns. Idempotent — re-running the
upgrade after a partial apply is safe (each column add is gated on
``has_column``).
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    add_column_idempotent,
)

revision: str = "0024_subscription_columns"
down_revision: str | None = "0023_smart_router_v2_flag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # subscription_tier — NOT NULL, defaults to 'payg' so existing rows
    # land on a sensible state without a backfill UPDATE.
    add_column_idempotent(
        "accounts",
        sa.Column(
            "subscription_tier",
            sa.String(length=16),
            nullable=False,
            server_default="payg",
        ),
        batch=is_sqlite,
    )

    # subscription_active_until — nullable; NULL means "no active sub".
    add_column_idempotent(
        "accounts",
        sa.Column(
            "subscription_active_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        batch=is_sqlite,
    )

    # subscription_canceled_at — nullable; non-NULL means user clicked Cancel
    # (the tier still works through active_until).
    add_column_idempotent(
        "accounts",
        sa.Column(
            "subscription_canceled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        batch=is_sqlite,
    )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        with op.batch_alter_table("accounts") as bop:
            for col in (
                "subscription_canceled_at",
                "subscription_active_until",
                "subscription_tier",
            ):
                # Partial-apply tolerance: the column may already be gone.
                with contextlib.suppress(Exception):
                    bop.drop_column(col)
    else:
        for col in (
            "subscription_canceled_at",
            "subscription_active_until",
            "subscription_tier",
        ):
            with contextlib.suppress(Exception):
                op.drop_column("accounts", col)
