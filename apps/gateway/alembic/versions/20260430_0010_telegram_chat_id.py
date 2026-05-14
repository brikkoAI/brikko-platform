"""users.telegram_chat_id BIGINT NULL (Sprint 4 Поток M)

Revision ID: 0010_telegram_chat_id
Revises: 0009_pii_masking
Create Date: 2026-04-30

Why
---

Sprint 4 / Comply Pack adjacent — TG-бот @VoltariBot для balance/usage/keys/topup
команд + push-алертов (low-balance, failover, new key created). MVP-must-have
по research: «BotHub UI задал ожидание — без TG мы выглядим иностранцами».

Single column ``telegram_chat_id`` on ``users`` (NULL = not linked). Linking
flow: user POSTs ``/v1/account/telegram-link`` → backend mints one-time
token (5 min TTL in Redis) → user runs ``/link <token>`` in @VoltariBot →
bot calls back to gateway with ``chat_id`` + token → gateway sets the column.

BIGINT because Telegram chat_ids can exceed INT_MAX (group chats use
negative numbers like -1001234567890; private chats are positive but
already approaching INT32 limits in 2026).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    add_column_idempotent,
    create_index_idempotent,
)

revision: str = "0010_telegram_chat_id"
down_revision: str | None = "0009_pii_masking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    add_column_idempotent(
        "users",
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        batch=is_sqlite,
    )
    # Sparse index — most users won't link TG initially; this keeps the
    # ``WHERE telegram_chat_id = :id`` lookup (used by webhook → user
    # resolution) cheap as the table grows.
    create_index_idempotent(
        "ix_users_telegram_chat_id",
        "users",
        ["telegram_chat_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.drop_index("ix_users_telegram_chat_id", table_name="users")
    if is_sqlite:
        with op.batch_alter_table("users") as bop:
            bop.drop_column("telegram_chat_id")
    else:
        op.drop_column("users", "telegram_chat_id")
