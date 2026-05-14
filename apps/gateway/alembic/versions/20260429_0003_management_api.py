"""management_api — auth foundation: email verification, password reset, welcome credits log, email invites

Revision ID: 0003_management_api
Revises: 0002_billing
Create Date: 2026-04-29

Adds:

* ``users.verification_token`` (text, nullable) — single-use email-verification token (hashed).
* ``users.verification_sent_at`` (timestamp, nullable) — last time we emitted a verify link.
* ``users.password_reset_token`` (text, nullable) — single-use password-reset token (hashed).
* ``users.password_reset_sent_at`` (timestamp, nullable) — last time we emitted a reset link.
  ``email_verified`` already exists (initial schema).

* ``welcome_credits_log`` — anti-abuse for the 200 ₽ welcome bonus.
  Keyed by ``email_hash`` (SHA-256 of normalised email) so we don't store
  PII in plaintext, can't be defeated by ``delete-account + signup``
  with the same email. ``ip_hash`` for forensics, not for blocking.

* ``email_invites`` — pending invites a workspace owner sent to a colleague.
  Token is stored as ``token_hash`` (only the plaintext is emailed, never persisted).
  ``role`` is stored as plain text (must be a valid ``seat_role_enum`` value);
  we add a CHECK to enforce that without coupling to the live SQLAlchemy enum.

All DDL goes through ``migration_helpers``.
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

revision: str = "0003_management_api"
down_revision: str | None = "0002_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # --- users: verification + reset columns ---
    add_column_idempotent(
        "users",
        sa.Column("verification_token", sa.Text, nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "users",
        sa.Column("verification_sent_at", sa.DateTime(timezone=True), nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "users",
        sa.Column("password_reset_token", sa.Text, nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "users",
        sa.Column("password_reset_sent_at", sa.DateTime(timezone=True), nullable=True),
        batch=is_sqlite,
    )

    # --- welcome_credits_log ---
    create_table_idempotent(
        "welcome_credits_log",
        sa.Column("email_hash", sa.String(64), primary_key=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=True),
    )
    create_index_idempotent("ix_welcome_credits_granted_at", "welcome_credits_log", ["granted_at"])

    # --- email_invites ---
    create_table_idempotent(
        "email_invites",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "account_id",
            GUID(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "invited_by_user_id",
            GUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('admin','member')", name="ck_email_invites_role"),
    )
    create_index_idempotent("ix_email_invites_account_id", "email_invites", ["account_id"])
    create_index_idempotent("ix_email_invites_email", "email_invites", ["email"])
    create_index_idempotent(
        "ix_email_invites_token_hash", "email_invites", ["token_hash"], unique=True
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.drop_index("ix_email_invites_token_hash", table_name="email_invites")
    op.drop_index("ix_email_invites_email", table_name="email_invites")
    op.drop_index("ix_email_invites_account_id", table_name="email_invites")
    op.drop_table("email_invites")

    op.drop_index("ix_welcome_credits_granted_at", table_name="welcome_credits_log")
    op.drop_table("welcome_credits_log")

    if is_sqlite:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("password_reset_sent_at")
            batch.drop_column("password_reset_token")
            batch.drop_column("verification_sent_at")
            batch.drop_column("verification_token")
    else:
        op.drop_column("users", "password_reset_sent_at")
        op.drop_column("users", "password_reset_token")
        op.drop_column("users", "verification_sent_at")
        op.drop_column("users", "verification_token")
