"""mcp_tokens — MCP server token management (Sprint MCP S1).

Revision ID: 0019_mcp_tokens
Revises: 0018_provider_balances
Create Date: 2026-05-11

Sprint MCP S1 (CEO 2026-05-11) — Brikko-MCP gateway (Model Context Protocol).

Rationale (короткое summary):
* Brikko запускает MCP-сервер `api.brikko.ru/mcp` для интеграции с Claude
  Desktop / Cursor / Continue / Zed. Это **отдельный канал** от обычных
  ``sk-brk-*`` ключей: MCP tools имеют другой surface (read-only-ish: статус
  баланса, статистика usage, рекомендация модели), не могут вызывать
  ``/v1/chat/completions``. Совмещать в одной таблице ``api_keys`` было бы
  плохо: пришлось бы расширять enum scope каждый раз, плюс пользователь
  не должен случайно подключить production-ключ к Claude Desktop (где он
  пойдёт в логи и MCP traffic).
* Префикс плейнтекста: ``mcp-brk-`` (12 чарактеров prefix в БД =
  ``mcp-brk-aB12``). Помечается визуально в logs / screenshots как MCP.
* Argon2id хеш (точно так же как ``api_keys.key_hash``), последовательность
  с остальной кодовой базой. Никаких bcrypt — один hash на проекте.
* Scopes: enum строк ``read_account | read_usage | recommend_model``. По
  одному scope на token (S1 KISS); если понадобится комбинация — V2 сделаем
  bitmask или JSON. Сейчас CEO/dev/agency-кейс покрывается тремя tools.
* Soft-revoke (``revoked_at IS NOT NULL``) — точно так же как ``api_keys``.
  Foreign key cascade удалит токен если аккаунт удаляется.

Schema mirrors ``api_keys`` структурно, но с **отдельной** таблицей чтобы:
- разные tariff-лимиты применять;
- разные surface scopes без enum-collision;
- разные audit-events / metrics labels в логах.

Idempotent через ``create_table_idempotent`` — partial-run crash safe.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    create_index_idempotent,
    create_table_idempotent,
)

revision: str = "0019_mcp_tokens"
down_revision: str | None = "0018_provider_balances"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # UUID-as-CHAR(36) on SQLite, UUID-native on Postgres — mirrors GUID()
    # custom type used in voltari_gateway.db.models. We declare String(36)
    # here so the migration runs identically on both — application code
    # bridges via the GUID TypeDecorator at ORM level.
    uuid_type: sa.types.TypeEngine[object] = (
        sa.dialects.postgresql.UUID(as_uuid=True) if is_postgres else sa.String(length=36)
    )

    create_table_idempotent(
        "mcp_tokens",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "account_id",
            uuid_type,
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        # Argon2id hash of plaintext. Plaintext is shown once at create time
        # and discarded — never re-stored.
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        # First 14 chars of plaintext for UI display + cheap candidate
        # lookup before the expensive argon2 verify (mirrors api_keys).
        sa.Column("token_prefix", sa.String(length=32), nullable=False),
        # Single scope per token (S1 KISS). Enum-as-string at DB level so
        # adding new scopes is a no-op DDL change (Postgres) / trivial check
        # constraint update (SQLite).
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        # Opt-in expiry — same UX as ``api_keys.expires_at`` (NULL = never).
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "scope IN ('read_account','read_usage','recommend_model')",
            name="ck_mcp_tokens_scope",
        ),
        sa.CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_mcp_tokens_status",
        ),
    )

    # Hot path: bearer-auth lookup goes by ``token_prefix``. Account-scoped
    # listing in the dashboard goes by ``account_id``. Status filter speeds
    # up "show active only" toggle.
    create_index_idempotent(
        "ix_mcp_tokens_token_prefix",
        "mcp_tokens",
        ["token_prefix"],
    )
    create_index_idempotent(
        "ix_mcp_tokens_account_id",
        "mcp_tokens",
        ["account_id"],
    )
    create_index_idempotent(
        "ix_mcp_tokens_status",
        "mcp_tokens",
        ["status"],
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mcp_tokens")
