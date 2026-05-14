"""api_keys: expires_at TIMESTAMPTZ NULL (TD-009)

Revision ID: 0008_api_keys_expires_at
Revises: 0007_processed_webhooks_dlq
Create Date: 2026-04-29

Why
---

TD-009: every API key is currently bessrochny (forever). For SOC2 / ISO27001
key-rotation policy, plus self-service "expire in 90 days" UX, we add an
optional ``expires_at`` column. NULL preserves the existing behaviour — a
key without an explicit expiry never expires, so this migration is fully
backwards-compatible (no key is automatically expired).

The ``require_api_key`` middleware (see
``voltari_gateway.auth.middleware._verify_against_db``) now rejects with
401 ``api_key_expired`` if ``expires_at IS NOT NULL AND expires_at < NOW()``.

Migration is idempotent through ``add_column_idempotent`` so a partial-run
crash is safe to retry.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    add_column_idempotent,
    create_index_idempotent,
)

revision: str = "0008_api_keys_expires_at"
down_revision: str | None = "0007_processed_webhooks_dlq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    add_column_idempotent(
        "api_keys",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        batch=is_sqlite,
    )

    # Index on (status, expires_at) — supports the auth fast-path query
    # ``WHERE key_prefix=:p AND status IN (...) AND (expires_at IS NULL OR
    # expires_at > NOW())`` so an expiry filter doesn't force a sequential
    # scan as the keys table grows.
    create_index_idempotent(
        "ix_api_keys_status_expires",
        "api_keys",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.drop_index("ix_api_keys_status_expires", table_name="api_keys")

    if is_sqlite:
        with op.batch_alter_table("api_keys") as bop:
            bop.drop_column("expires_at")
    else:
        op.drop_column("api_keys", "expires_at")
