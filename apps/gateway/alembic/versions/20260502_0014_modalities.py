"""Sprint M1 — modality expansion: STT (Whisper) + embeddings.

Revision ID: 0014_modalities
Revises: 0013_analytics_gaps
Create Date: 2026-05-02

Why
---

Two new endpoints land in this sprint:

* ``POST /v1/audio/transcriptions`` — OpenAI Whisper proxy, billed per
  minute of audio.
* ``POST /v1/embeddings`` — OpenAI text-embedding-3-{small,large} proxy,
  billed per input token.

Both write to the existing ``usage_events`` table. To keep the analytics
layer honest we add two columns:

* ``modality`` — {"chat", "stt", "embeddings"}. Lets the dashboard split
  revenue by surface area without scanning model strings (matching the
  same anti-pattern the Sprint-11 ``provider`` backfill replaced).
* ``unit``     — {"token", "minute"}. Tells SQL whether ``input_tokens``
  is meaningful (token-billed rows) or ``cost_kopecks`` is the only
  number that matters (minute-billed STT).

Both columns get sensible defaults — ``modality='chat'``, ``unit='token'``
— so legacy rows written before this migration carry the truthful tag
and the per-call cost-aggregate SUMs across the table stay correct.

Approach
--------

* Add columns as NOT NULL with a server-side default. Postgres backfills
  every existing row in a single ALTER (no UPDATE pass needed). SQLite
  with ``batch_alter_table`` does the column rebuild and applies the
  default during the copy. Either way, no Python-side backfill loop.

* Composite index on ``(modality, created_at)`` for the per-modality
  revenue dashboard. Mirrors the
  ``ix_usage_events_provider_created`` pattern from 0013.

All DDL goes through the idempotent helpers — partial reruns survive.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    add_column_idempotent,
    create_index_idempotent,
    has_column,
)

revision: str = "0014_modalities"
down_revision: str | None = "0013_analytics_gaps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # Step 1 — add as nullable with a server default. The default backfills
    # existing rows (Postgres rewrites NULLs to the default at ALTER time;
    # SQLite batch-rebuild applies it during the copy).
    add_column_idempotent(
        "usage_events",
        sa.Column(
            "modality",
            sa.String(length=16),
            nullable=False,
            server_default="chat",
        ),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "usage_events",
        sa.Column(
            "unit",
            sa.String(length=16),
            nullable=False,
            server_default="token",
        ),
        batch=is_sqlite,
    )

    # Step 2 — drop the server_default once data is in place. New writes
    # always set the value at the application layer (UsageEvent ORM
    # default + handler-level explicit set), so we don't want a stale
    # DDL default lying around to mask future bugs.
    if not is_sqlite:
        op.alter_column(
            "usage_events",
            "modality",
            existing_type=sa.String(length=16),
            server_default=None,
        )
        op.alter_column(
            "usage_events",
            "unit",
            existing_type=sa.String(length=16),
            server_default=None,
        )

    # Step 3 — index for the per-modality dashboard.
    create_index_idempotent(
        "ix_usage_events_modality_created",
        "usage_events",
        ["modality", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.drop_index(
        "ix_usage_events_modality_created",
        table_name="usage_events",
        if_exists=True,
    )

    if is_sqlite:
        with op.batch_alter_table("usage_events") as bop:
            if has_column("usage_events", "unit"):
                bop.drop_column("unit")
            if has_column("usage_events", "modality"):
                bop.drop_column("modality")
    else:
        if has_column("usage_events", "unit"):
            op.drop_column("usage_events", "unit")
        if has_column("usage_events", "modality"):
            op.drop_column("usage_events", "modality")
