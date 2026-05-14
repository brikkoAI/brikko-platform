"""account_holds — pre-flight balance reservations stored transactionally in Postgres

Revision ID: 0005_account_holds
Revises: 0004_account_settings
Create Date: 2026-04-29

Why a new table instead of Redis (the old hold_amount used a Redis SETNX +
INCRBY counter):

* Holds participate in the same transaction as the balance read. With Redis,
  ``balance`` was read from Postgres and ``sum_holds`` from Redis — there was
  no way to lock the pair atomically, so two concurrent requests could each
  see ``balance >= sum_holds + amount`` and both pass pre-flight. The DB
  ``SELECT FOR UPDATE`` only kicked in at debit time, after the upstream
  call had been issued (and paid for in OpenAI tokens).

* On a Redis outage we silently degraded to a no-op hold (logged warning).
  In production this means a brief Redis blip lets two requests through
  the pre-flight gate. Postgres is already on the critical path for every
  request — making it the source of truth for holds removes one failure
  surface.

* We can use Postgres' ``ON CONFLICT DO NOTHING`` for idempotency on
  ``ref_id`` instead of Redis SETNX semantics, keeping all billing
  logic in one storage.

Schema notes:

* ``ref_id`` is the same correlation id passed into ``hold_amount`` (typically
  the gateway request_id). UNIQUE per (account_id, ref_id) so two callers
  with the same ref_id share the hold rather than create duplicates.

* ``expires_at`` lets a janitor sweep stale holds left behind by a crashed
  worker. Default TTL = 120s (set by the application, not the DB).

* No ``status`` column. A hold either exists (active) or has been deleted
  (committed or released). Keeping the table small lets a SUM(amount_kopecks)
  WHERE account_id=... be served entirely from a small index page.

Idempotent on retry: if ``account_holds`` already exists we skip create.
This matches the pattern used by 0002/0003/0004 for SQLite/Postgres parity.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.models import GUID  # type: ignore[import-not-found]

revision: str = "0005_account_holds"
down_revision: str | None = "0004_account_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Idempotent: skip if table already exists (re-run safety, matches 0002-0004
    # pattern). Same logic for indexes — IF NOT EXISTS isn't supported by
    # batch_alter_table, so we check the inspector first.
    if "account_holds" in inspector.get_table_names():
        return

    op.create_table(
        "account_holds",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "account_id",
            GUID(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ref_id", sa.String(255), nullable=False),
        sa.Column("amount_kopecks", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_kopecks > 0", name="ck_account_holds_amount_positive"),
        sa.UniqueConstraint("account_id", "ref_id", name="uq_account_holds_account_ref"),
    )
    op.create_index("ix_account_holds_account_id", "account_holds", ["account_id"])
    # Janitor index: ORDER BY expires_at ASC LIMIT N for the cleanup cron.
    op.create_index("ix_account_holds_expires_at", "account_holds", ["expires_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "account_holds" not in inspector.get_table_names():
        return
    op.drop_index("ix_account_holds_expires_at", table_name="account_holds")
    op.drop_index("ix_account_holds_account_id", table_name="account_holds")
    op.drop_table("account_holds")
