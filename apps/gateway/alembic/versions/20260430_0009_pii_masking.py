"""accounts.pii_masking_enabled BOOL DEFAULT FALSE (Sprint 4 Поток M)

Revision ID: 0009_pii_masking
Revises: 0008_api_keys_expires_at
Create Date: 2026-04-30

Why
---

Sprint 4 / V2 USP — легальный 152-ФЗ proxy. Перед отправкой prompt'а
в любой иностранный провайдер (OpenAI/Anthropic/Google) gateway маскирует
персональные данные клиента (ФИО, телефон, email, паспорт, ИНН, СНИЛС, карты)
на placeholder'ы и хранит mapping в Redis с TTL ~1ч. После ответа провайдера —
раз-маскирует обратно для конечного пользователя.

Триггеры маскирования (любой ИЛИ):

* ``Account.pii_masking_enabled = TRUE`` — глобально включено для аккаунта
  (тариф ``pro_privacy`` / enterprise по 152-ФЗ).
* Header ``X-PII-Protect: true`` или поле ``pii_protect: true`` в request body
  — per-request opt-in для аккаунтов без флага.

Колонка nullable=False с server_default='false' чтобы существующие аккаунты
получили явное FALSE без NULL-головной боли.

Migration is idempotent through ``add_column_idempotent``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import add_column_idempotent

revision: str = "0009_pii_masking"
down_revision: str | None = "0008_api_keys_expires_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    add_column_idempotent(
        "accounts",
        sa.Column(
            "pii_masking_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        batch=is_sqlite,
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        with op.batch_alter_table("accounts") as bop:
            bop.drop_column("pii_masking_enabled")
    else:
        op.drop_column("accounts", "pii_masking_enabled")
