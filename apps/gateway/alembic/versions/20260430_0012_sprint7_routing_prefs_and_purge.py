"""Sprint 7 — routing preferences + PII purge tombstone.

Revision ID: 0012_sprint7_routing_purge
Revises: 0011_sprint6_security
Create Date: 2026-04-30

Why
---

Sprint 7 ships three features:

* **Account closure cron + PII purge** — schema for closure already
  landed in Alembic 0011 (``closure_*`` + ``closed_at``). What was
  missing is ``pii_purged_at`` — the timestamp set by the cron when
  the account row's PII has been wiped. The row stays as a tombstone
  for compliance (152-ФЗ requires keeping financial transactions for
  five years; we anonymise email/passwords/telegram_id but keep
  ``Account``/``Transaction`` lineage intact).

* **Routing preferences** — per-account routing policy
  (`routing_mode`/`routing_strategy`/`routing_allowed_providers`/
  `routing_allowed_models`). Default is ``mode='smart',
  strategy='cheap', allowed=NULL/NULL`` so existing accounts keep
  their pre-Sprint-7 behaviour bit-for-bit (CEO 30.04 §10 #6:
  default for new accounts also stays ``cheap`` to keep COGS
  predictable for PAYG).

* **Data export** — schema already in 0011 (``data_exports`` table).
  No DDL change here.

Both columns are added through the idempotent helpers so a partially
applied previous run doesn't trip on rerun.

Trade-offs / dialect
--------------------

* ``routing_allowed_providers`` / ``routing_allowed_models`` use
  :class:`sa.JSON` (portable). On Postgres SQLAlchemy maps that to
  ``JSONB`` because the model declares ``with_variant(JSONB(), "postgresql")``
  on ``JSONType``. The migration uses plain ``sa.JSON()`` — at-rest type
  is ``JSONB`` on PG, ``JSON`` on SQLite (tests).
* No CHECK constraint on ``routing_mode`` / ``routing_strategy`` strings:
  the application-layer Pydantic enum is the source of truth and we
  prefer fast schema iteration over DB-level enum types here. We may
  promote to PG ENUMs in V2 if multiple services start writing to the
  column directly (currently only the gateway does).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    add_column_idempotent,
)

revision: str = "0012_sprint7_routing_purge"
down_revision: str | None = "0011_sprint6_security"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ----------------------------------------------------------------------------
# upgrade
# ----------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # ------------------------------------------------------------------
    # 1) accounts — routing preferences (Sprint 7 spec §3.1)
    # ------------------------------------------------------------------
    add_column_idempotent(
        "accounts",
        sa.Column(
            "routing_mode",
            sa.String(length=16),
            nullable=False,
            server_default="smart",
        ),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        # CEO 30.04 §10 #6: default stays 'cheap' even for new accounts —
        # changing to 'smart' would lift COGS 30-50% on PAYG.
        sa.Column(
            "routing_strategy",
            sa.String(length=16),
            nullable=False,
            server_default="cheap",
        ),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column(
            "routing_allowed_providers",
            sa.JSON(),
            nullable=True,
        ),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column(
            "routing_allowed_models",
            sa.JSON(),
            nullable=True,
        ),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column(
            "routing_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        batch=is_sqlite,
    )

    # ------------------------------------------------------------------
    # 2) accounts — pii_purged_at tombstone (Sprint 7 closure cron)
    # ------------------------------------------------------------------
    add_column_idempotent(
        "accounts",
        sa.Column(
            "pii_purged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        batch=is_sqlite,
    )


# ----------------------------------------------------------------------------
# downgrade
# ----------------------------------------------------------------------------


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        with op.batch_alter_table("accounts") as bop:
            bop.drop_column("pii_purged_at")
            bop.drop_column("routing_updated_at")
            bop.drop_column("routing_allowed_models")
            bop.drop_column("routing_allowed_providers")
            bop.drop_column("routing_strategy")
            bop.drop_column("routing_mode")
    else:
        op.drop_column("accounts", "pii_purged_at")
        op.drop_column("accounts", "routing_updated_at")
        op.drop_column("accounts", "routing_allowed_models")
        op.drop_column("accounts", "routing_allowed_providers")
        op.drop_column("accounts", "routing_strategy")
        op.drop_column("accounts", "routing_mode")
