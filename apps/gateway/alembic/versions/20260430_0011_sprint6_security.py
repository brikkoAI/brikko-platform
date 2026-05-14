"""Sprint 6 — security & privacy hardening.

Revision ID: 0011_sprint6_security
Revises: 0010_telegram_chat_id
Create Date: 2026-04-30

Why
---

Sprint 6 ships:

* **2FA / TOTP** — RFC 6238 codes + recovery codes (бeкап, hashed only).
* **Sessions API** — мульти-устройство список + ревок других сессий.
  Mirror в Postgres ``sessions`` рядом с Redis whitelist (Redis быстро,
  Postgres — для UI и audit). Истина = Redis (whitelist), Postgres = вид.
* **Data exports** (152-ФЗ ст. 14, GDPR Art. 20) — таблица учёта запросов
  и сгенерированных архивов.
* **Audit log** — single source of truth для всех security событий
  (login, 2FA on/off, password change, key rotate, tariff change, closure).
  Не использует full-text retention (90 days hot в PG, потом archive в S3
  или ClickHouse — TODO Sprint 7).
* **Account closure** (152-ФЗ ст. 21) — мягкое удаление с 30-дневным
  grace period + cancel; cron purge — отдельная задача (TD Sprint 7).

Также:

* ``tariff_enum`` получает значение ``pro_privacy`` (новый тариф между
  Pro и Team — PII-маскинг включён).
* ``accounts.tariff_active_until`` — когда расчётный период
  заканчивается; влияет на UI (показывает «осталось N дней»).

Idempotency / dialect
---------------------

Все DDL — через ``migration_helpers.add_column_idempotent`` /
``create_table_idempotent`` так что ``alembic upgrade head`` после
полу-успешного предыдущего прогона не падает с ``DuplicateColumn``.
SQLite (тесты) идёт через batch_alter_table; ENUM-расширение пропускается
(SQLite SAEnum использует CHECK, новые значения подхватываются ORM
после bump).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    add_column_idempotent,
    create_index_idempotent,
    create_table_idempotent,
)
from voltari_gateway.db.models import GUID

revision: str = "0011_sprint6_security"
down_revision: str | None = "0010_telegram_chat_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ----------------------------------------------------------------------------
# upgrade
# ----------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    is_postgres = bind.dialect.name == "postgresql"

    # ------------------------------------------------------------------
    # 1) Extend tariff_enum with 'pro_privacy' (PG only).
    #    SQLite SAEnum is mapped to a CHECK constraint at table definition;
    #    SAEnum re-emits the constraint at table create from the live
    #    Tariff enum — so when models.Tariff gains PRO_PRIVACY the check
    #    in fresh DBs picks it up automatically. Existing SQLite test DBs
    #    are dropped between sessions, so no migration needed.
    # ------------------------------------------------------------------
    if is_postgres:
        # Postgres won't add a value if it already exists when ALTER TYPE
        # ... ADD VALUE IF NOT EXISTS is used (PG 12+). We are on 16+ in
        # production so the modifier is safe.
        op.execute(sa.text("ALTER TYPE tariff_enum ADD VALUE IF NOT EXISTS 'pro_privacy'"))

    # ------------------------------------------------------------------
    # 2) users — TOTP columns
    # ------------------------------------------------------------------
    add_column_idempotent(
        "users",
        sa.Column("totp_secret_encrypted", sa.LargeBinary(), nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "users",
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "users",
        # JSON list of bcrypt-hashed recovery codes; '[]' = enabled but consumed.
        sa.Column("totp_recovery_codes_hashed", sa.JSON(), nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "users",
        sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True),
        batch=is_sqlite,
    )

    # ------------------------------------------------------------------
    # 3) accounts — closure + tariff period
    # ------------------------------------------------------------------
    add_column_idempotent(
        "accounts",
        sa.Column("tariff_active_until", sa.DateTime(timezone=True), nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column("closure_requested_at", sa.DateTime(timezone=True), nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column("closure_scheduled_at", sa.DateTime(timezone=True), nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column("closure_reason", sa.String(length=512), nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        batch=is_sqlite,
    )
    create_index_idempotent(
        "ix_accounts_closure_scheduled_at",
        "accounts",
        ["closure_scheduled_at"],
    )

    # ------------------------------------------------------------------
    # 4) sessions — mirror of Redis refresh-JTI whitelist for the UI
    # ------------------------------------------------------------------
    create_table_idempotent(
        "sessions",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "user_id",
            GUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("refresh_jti", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        # Free-form so we can lazily fill ``device``/``browser``/``location``.
        sa.Column("device_label", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "refresh_jti", name="uq_sessions_user_jti"),
    )
    create_index_idempotent("ix_sessions_user_id", "sessions", ["user_id"])
    create_index_idempotent("ix_sessions_refresh_jti", "sessions", ["refresh_jti"])
    create_index_idempotent("ix_sessions_revoked_at", "sessions", ["revoked_at"])

    # ------------------------------------------------------------------
    # 5) data_exports — 152-ФЗ Art. 14 + GDPR Art. 20
    # ------------------------------------------------------------------
    create_table_idempotent(
        "data_exports",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "user_id",
            GUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            GUID(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 'pending' | 'processing' | 'ready' | 'expired' | 'failed' | 'purged'
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("download_url", sa.String(length=1024), nullable=True),
        sa.Column("storage_path", sa.String(length=512), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','processing','ready','expired','failed','purged')",
            name="ck_data_exports_status",
        ),
    )
    create_index_idempotent("ix_data_exports_user_id", "data_exports", ["user_id"])
    create_index_idempotent("ix_data_exports_account_id", "data_exports", ["account_id"])
    create_index_idempotent("ix_data_exports_expires_at", "data_exports", ["expires_at"])

    # ------------------------------------------------------------------
    # 6) audit_log — central security event ledger
    # ------------------------------------------------------------------
    create_table_idempotent(
        "audit_log",
        sa.Column("id", GUID(), primary_key=True),
        # FKs use SET NULL because audit must outlive the user/account
        # (purge keeps log lines for compliance — only PII is dropped).
        sa.Column(
            "user_id",
            GUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "account_id",
            GUID(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # 'login_ok' | 'login_failed' | '2fa_enabled' | '2fa_disabled' |
        # '2fa_login_ok' | '2fa_login_failed' | 'recovery_code_used' |
        # 'password_changed' | 'password_change_failed' |
        # 'session_revoked' | 'sessions_revoked_all' |
        # 'tariff_changed' | 'tariff_change_denied' |
        # 'account_closure_requested' | 'account_closure_canceled' |
        # 'account_closed' | 'data_export_requested' |
        # 'data_export_downloaded' | 'data_export_unauthorized' |
        # 'email_verify_resent' | 'api_key_created' | 'api_key_revoked'
        sa.Column("action", sa.String(length=64), nullable=False),
        # 'ok' | 'denied' | 'failed'
        sa.Column("outcome", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        # Free-form structured details (target session_id, before/after tariff,
        # recovery_codes_remaining, etc.)
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('ok','denied','failed')",
            name="ck_audit_log_outcome",
        ),
    )
    create_index_idempotent("ix_audit_log_user_id", "audit_log", ["user_id"])
    create_index_idempotent("ix_audit_log_account_id", "audit_log", ["account_id"])
    create_index_idempotent("ix_audit_log_action", "audit_log", ["action"])
    create_index_idempotent("ix_audit_log_created_at", "audit_log", ["created_at"])
    create_index_idempotent("ix_audit_log_user_created", "audit_log", ["user_id", "created_at"])


# ----------------------------------------------------------------------------
# downgrade
# ----------------------------------------------------------------------------


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # Drop tables (reverse order to be FK-safe).
    op.drop_index("ix_audit_log_user_created", table_name="audit_log")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_audit_log_account_id", table_name="audit_log")
    op.drop_index("ix_audit_log_user_id", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("ix_data_exports_expires_at", table_name="data_exports")
    op.drop_index("ix_data_exports_account_id", table_name="data_exports")
    op.drop_index("ix_data_exports_user_id", table_name="data_exports")
    op.drop_table("data_exports")

    op.drop_index("ix_sessions_revoked_at", table_name="sessions")
    op.drop_index("ix_sessions_refresh_jti", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")

    op.drop_index("ix_accounts_closure_scheduled_at", table_name="accounts")

    if is_sqlite:
        with op.batch_alter_table("accounts") as bop:
            bop.drop_column("closed_at")
            bop.drop_column("closure_reason")
            bop.drop_column("closure_scheduled_at")
            bop.drop_column("closure_requested_at")
            bop.drop_column("tariff_active_until")
        with op.batch_alter_table("users") as bop:
            bop.drop_column("totp_enabled_at")
            bop.drop_column("totp_recovery_codes_hashed")
            bop.drop_column("totp_enabled")
            bop.drop_column("totp_secret_encrypted")
    else:
        op.drop_column("accounts", "closed_at")
        op.drop_column("accounts", "closure_reason")
        op.drop_column("accounts", "closure_scheduled_at")
        op.drop_column("accounts", "closure_requested_at")
        op.drop_column("accounts", "tariff_active_until")
        op.drop_column("users", "totp_enabled_at")
        op.drop_column("users", "totp_recovery_codes_hashed")
        op.drop_column("users", "totp_enabled")
        op.drop_column("users", "totp_secret_encrypted")
    # Note: PG ENUM value cannot be removed — leave 'pro_privacy' in place.
    # Downgrade in production is not the path; the value stays harmless.
