"""smart_router_v2_account_flag — per-account opt-in for Smart Router v2.

Revision ID: 0023_smart_router_v2_flag
Revises: 0021_mcp_scopes_v2
Create Date: 2026-05-13

Sprint S1 — Smart Router v2 scaffolding (design doc 2026-05-12, §6 + Q7).

What this migration does
------------------------

Adds a single boolean column ``accounts.smart_router_v2_enabled`` defaulting
to ``FALSE``. The column is the per-account opt-in for the v2 routing
pipeline; ``SMART_ROUTER_V2_ENABLED`` (env var, see
``voltari_gateway.config.Settings``) is the global override.

The runtime helper ``router.pipeline.should_use_pipeline(env_flag,
account_flag)`` evaluates ``env_flag OR account_flag``, so:

* Both off → request takes the legacy v1 code path (today's behaviour).
* Account flag on → that account uses v2 (admin opt-in).
* Env flag on → every account uses v2 (global rollout).

No data backfill needed — the column starts ``FALSE`` for every
existing account, preserving current behaviour.

Why not an env-only flag
------------------------

Per CEO 2026-05-13 (design doc Q7), rollout is "flag-off prod +
per-account admin flip". Per-account granularity lets us:

* Pilot on the CEO's own account first.
* Enable for 2-3 friendly clients before global rollout.
* Roll back per-account if a specific customer's traffic shows a
  regression — without disabling the pipeline globally.

Dialect notes
-------------

* Postgres: ``ALTER TABLE accounts ADD COLUMN ... DEFAULT FALSE`` is a
  metadata-only operation (no row rewrite) since PG 11. Safe on a
  production table with M+ rows.
* SQLite: ``ALTER TABLE`` adds the column at the end. The
  ``add_column_idempotent`` helper handles partial-reapply via batch mode.

Down-migration
--------------

``downgrade()`` drops the column. Idempotent — if the column doesn't
exist (partial upgrade) we skip the drop.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    add_column_idempotent,
)

revision: str = "0023_smart_router_v2_flag"
down_revision: str | None = "0022_mcp_enum_types_backfill"
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
            "smart_router_v2_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
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
            bop.drop_column("smart_router_v2_enabled")
    else:
        op.drop_column("accounts", "smart_router_v2_enabled")
