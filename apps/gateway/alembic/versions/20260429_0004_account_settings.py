"""account_settings — JSONB settings column for free-form per-account preferences

Revision ID: 0004_account_settings
Revises: 0003_management_api
Create Date: 2026-04-29

Adds:

* ``accounts.settings`` (JSONB on PG, JSON on SQLite) — open-ended per-account
  preferences blob: notifications config, dashboard widgets, locale etc.

  We deliberately keep the schema open instead of normalising into columns —
  ``PATCH /v1/account/settings`` validates the shape, and we never query into
  it from SQL. ``DEFAULT '{}'`` so old rows show up as empty objects without
  any code branches for NULL.

DDL goes through ``migration_helpers`` for idempotency.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from voltari_gateway.db.migration_helpers import add_column_idempotent

revision: str = "0004_account_settings"
down_revision: str | None = "0003_management_api"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    json_type = sa.JSON() if is_sqlite else JSONB()
    # ``server_default`` makes the migration safe against rows that already
    # exist — every account starts with an empty object, no NULLs to handle.
    server_default = sa.text("'{}'") if is_sqlite else sa.text("'{}'::jsonb")

    add_column_idempotent(
        "accounts",
        sa.Column(
            "settings",
            json_type,
            nullable=False,
            server_default=server_default,
        ),
        batch=is_sqlite,
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        with op.batch_alter_table("accounts") as batch:
            batch.drop_column("settings")
    else:
        op.drop_column("accounts", "settings")
