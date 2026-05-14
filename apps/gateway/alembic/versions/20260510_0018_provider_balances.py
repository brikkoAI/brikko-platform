"""provider_balances — admin balance monitoring (Sprint 14).

Revision ID: 0018_provider_balances
Revises: 0017_gateway_request_log
Create Date: 2026-05-10

Sprint 14 (CEO 2026-05-10) — admin страница «Балансы провайдеров».

Источник: 06_Operations/2026-05-10-provider-balances/01-design.md (TBD).

Rationale (короткое summary):
* CEO нужно одно место чтобы видеть остатки на upstream-аккаунтах. Если
  у DeepSeek/OpenAI/Anthropic закончатся деньги — gateway начнёт отдавать
  502 клиентам, что недопустимо для legal-AI-platform.
* Три источника данных (3 значения ``fetch_method``):
  - ``api``    — публичный balance endpoint (DeepSeek, Sber, Moonshot).
  - ``manual`` — вручную через POST endpoint (OpenAI/Anthropic/Together…
                 у них нет публичного API для баланса).
  - ``scrape`` — Playwright scraping (Phase 2, отдельный сервис).
* ``balance_rub_kopecks`` — нормализация под единый ₽-runway. NULL для
  токенных балансов (Sber GigaChat) — токены не пересчитываются в рубли.
* ``burn_rate_kopecks_per_day`` / ``runway_days`` — computed at refresh
  time из ``usage_events`` за последние 7 дней. Хранятся в строке (а не
  считаются on-the-fly), чтобы UI отдавал snapshot одним SELECT'ом.
* ``raw_response`` JSONB — для диагностики если вылетит ошибка парсинга
  или провайдер сменил формат. Хранится только последний.

Уникальный constraint на ``provider`` (один ряд на провайдера). Upsert
делаем через ``ON CONFLICT (provider) DO UPDATE`` в service.py (PG)
либо MERGE-эмуляцию для SQLite (тесты).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from voltari_gateway.db.migration_helpers import (
    create_index_idempotent,
    create_table_idempotent,
)

revision: str = "0018_provider_balances"
down_revision: str | None = "0017_gateway_request_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # JSONB on Postgres, JSON on SQLite (tests). Mirrors the JSONType
    # pattern in voltari_gateway.db.models.
    json_type: sa.types.TypeEngine[object] = (
        JSONB().with_variant(sa.JSON(), "sqlite") if is_postgres else sa.JSON()
    )

    create_table_idempotent(
        "provider_balances",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        # NUMERIC(18,6) — достаточная точность для $5.234567 (DeepSeek
        # отдаёт total_balance строкой с 4-6 знаками после точки).
        sa.Column("balance_native", sa.Numeric(18, 6), nullable=True),
        sa.Column("balance_currency", sa.String(length=8), nullable=True),
        sa.Column("balance_rub_kopecks", sa.BigInteger(), nullable=True),
        sa.Column(
            "last_fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetch_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("fetch_method", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("raw_response", json_type, nullable=True),
        sa.Column("burn_rate_kopecks_per_day", sa.BigInteger(), nullable=True),
        sa.Column("runway_days", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("provider", name="uq_provider_balances_provider"),
        sa.CheckConstraint(
            "fetch_status IN ('ok','error','manual','pending')",
            name="ck_provider_balances_fetch_status",
        ),
        sa.CheckConstraint(
            "fetch_method IN ('api','manual','scrape')",
            name="ck_provider_balances_fetch_method",
        ),
    )

    # Lookups: admin endpoint всегда читает все строки — индекс по provider
    # уже есть через UNIQUE. Сортировка по last_fetched_at используется
    # внутри service.py для определения «давно не обновлялось».
    create_index_idempotent(
        "ix_provider_balances_last_fetched_at",
        "provider_balances",
        ["last_fetched_at"],
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS provider_balances")
