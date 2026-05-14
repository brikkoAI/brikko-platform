"""OAuth tables + accounts.is_studio_user.

Revision ID: 0015_oauth_clients_and_codes
Revises: 0014_modalities
Create Date: 2026-05-03

Adds three things:

* ``oauth_clients`` — first-party (currently just ``studio``) and future
  third-party client registry. ``client_id`` is the human-readable PK.
* ``oauth_authorization_codes`` — single-use code storage (hashed). 10-min
  TTL enforced at the application layer; the ``ix_oauth_codes_expires_at``
  index lets a future cron drop expired rows.
* ``accounts.is_studio_user`` — analytics flag set on first OAuth grant
  via ``client_id=studio``. Default ``false`` so legacy rows are
  truthful.

All DDL goes through the idempotent helpers — partial reruns survive.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    add_column_idempotent,
    create_index_idempotent,
    create_table_idempotent,
    has_column,
    has_table,
)
from voltari_gateway.db.models import GUID

revision: str = "0015_oauth_clients_and_codes"
down_revision: str | None = "0014_modalities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # 1) accounts.is_studio_user
    add_column_idempotent(
        "accounts",
        sa.Column(
            "is_studio_user",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        batch=is_sqlite,
    )
    if not is_sqlite:
        op.alter_column(
            "accounts",
            "is_studio_user",
            existing_type=sa.Boolean(),
            server_default=None,
        )

    # 2) oauth_clients
    create_table_idempotent(
        "oauth_clients",
        sa.Column("client_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("allowed_scopes", sa.JSON(), nullable=False),
        sa.Column(
            "is_first_party",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 3) oauth_authorization_codes
    create_table_idempotent(
        "oauth_authorization_codes",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "client_id",
            sa.String(length=64),
            sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
        ),
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
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("redirect_uri", sa.String(length=512), nullable=False),
        sa.Column("code_challenge", sa.String(length=128), nullable=False),
        sa.Column("code_challenge_method", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("code_hash", name="uq_oauth_codes_code_hash"),
        sa.CheckConstraint(
            "code_challenge_method = 'S256'",
            name="ck_oauth_codes_pkce_method_s256",
        ),
    )
    create_index_idempotent(
        "ix_oauth_codes_code_hash",
        "oauth_authorization_codes",
        ["code_hash"],
    )
    create_index_idempotent(
        "ix_oauth_codes_expires_at",
        "oauth_authorization_codes",
        ["expires_at"],
    )
    create_index_idempotent(
        "ix_oauth_codes_user_account",
        "oauth_authorization_codes",
        ["user_id", "account_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if has_table("oauth_authorization_codes"):
        op.drop_index(
            "ix_oauth_codes_user_account",
            table_name="oauth_authorization_codes",
            if_exists=True,
        )
        op.drop_index(
            "ix_oauth_codes_expires_at",
            table_name="oauth_authorization_codes",
            if_exists=True,
        )
        op.drop_index(
            "ix_oauth_codes_code_hash",
            table_name="oauth_authorization_codes",
            if_exists=True,
        )
        op.drop_table("oauth_authorization_codes")

    if has_table("oauth_clients"):
        op.drop_table("oauth_clients")

    if has_column("accounts", "is_studio_user"):
        if is_sqlite:
            with op.batch_alter_table("accounts") as bop:
                bop.drop_column("is_studio_user")
        else:
            op.drop_column("accounts", "is_studio_user")
