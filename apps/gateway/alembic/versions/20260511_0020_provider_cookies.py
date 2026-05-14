"""provider_cookies — Playwright cookie metadata (Sprint 14, Phase 2).

Revision ID: 0020_provider_cookies
Revises: 0019_mcp_tokens
Create Date: 2026-05-11

Sprint 14 Phase 2 (CEO 2026-05-11) — scraper-сервис для балансов OpenAI /
Anthropic / Together (нет публичного balance API).

Фаза 1 (0018_provider_balances) ввела ManualAdapter для этих провайдеров.
Фаза 2 заменяет их Playwright-scraper'ом, который логинится cookies'ами CEO
и парсит балансы с дашборда.

Эта таблица хранит **метаданные** о cookies (когда залит, когда последний
раз был validный). Сами cookies — на shared-volume в зашифрованном виде
(Fernet, тот же ENCRYPTION_KEY что и для request_payloads). См.
``voltari_gateway/api/admin_cookies.py``.

Schema rationale:
* ``provider`` — string, **уникальный**. Один ряд на провайдера = последний
  актуальный набор cookies. История перезаписей не хранится — это admin-
  facing operation, аудит идёт в ``audit_log`` через write_audit().
* ``cookie_path`` — относительный путь внутри ``COOKIES_VOLUME_PATH``
  (например ``openai.enc``). Не absolute, чтобы переезды volume не сломали БД.
* ``size_bytes`` — размер зашифрованного blob'а. Sanity-check + heads-up
  если кто-то залил пустой файл.
* ``uploaded_by`` — user UUID (FK на ``users``) того, кто залил. ON DELETE
  SET NULL — пользователь может уйти из платформы, cookies остаются.
* ``last_valid_at`` — апдейтится scraper'ом при успешном scrape (через
  отдельный internal endpoint ``POST /provider_cookies/{provider}/seen``).
  NULL если ещё ни разу не получили валидный ответ.
* ``last_error`` — текст последней ошибки (``cookie_expired``, ``dom_changed``).

Не делаем CASCADE на ``users`` — soft-delete pattern; gone-user cookies
становятся «orphaned but functional» (gateway всё ещё может ими scrape'ить
пока они валидны), UI помечает их как «uploaded by deleted user».
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    create_index_idempotent,
    create_table_idempotent,
)

revision: str = "0020_provider_cookies"
down_revision: str | None = "0019_mcp_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # Portable UUID type (mirrors voltari_gateway/db/models.GUID).
    if is_postgres:
        from sqlalchemy.dialects.postgresql import UUID as PG_UUID

        uuid_type: sa.types.TypeEngine[object] = PG_UUID(as_uuid=True)
    else:
        uuid_type = sa.CHAR(36)

    create_table_idempotent(
        "provider_cookies",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("cookie_path", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "uploaded_by",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_valid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("provider", name="uq_provider_cookies_provider"),
    )

    create_index_idempotent(
        "ix_provider_cookies_uploaded_at",
        "provider_cookies",
        ["uploaded_at"],
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS provider_cookies")
