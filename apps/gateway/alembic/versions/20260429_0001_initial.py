"""initial schema — users, accounts, seats, api_keys, transactions, usage_events, request_payloads

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-29

Idempotency
-----------

Both ENUM creation and ``CREATE TABLE`` go through helpers in
``alembic/helpers.py`` which check the live schema before issuing DDL,
so a crash mid-upgrade leaves a re-runnable state. See the helper
module's docstring for the trade-offs.

Enum values are sourced from the SQLAlchemy enum classes in
``voltari_gateway.db.models`` so the schema and the ORM cannot drift
silently — adding a new ``Tariff`` value automatically appears in the
migration without a second copy to keep in sync. The pre-29.04 code
duplicated the values inline, which is exactly the schema-drift trap
called out in the audit (BE P0-11).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    create_enum_idempotent,
    create_index_idempotent,
    create_table_idempotent,
)
from voltari_gateway.db.models import (
    GUID,
    AccountStatus,
    ApiKeyScope,
    ApiKeyStatus,
    SeatRole,
    Tariff,
    TransactionKind,
)

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Single source of truth: enum-classes from models.py. Adding a new value
# there propagates to the migration automatically — no chance of the
# hardcoded list drifting from the ORM.
_ENUMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tariff_enum", tuple(m.value for m in Tariff)),
    ("account_status_enum", tuple(m.value for m in AccountStatus)),
    ("seat_role_enum", tuple(m.value for m in SeatRole)),
    ("api_key_scope_enum", tuple(m.value for m in ApiKeyScope)),
    ("api_key_status_enum", tuple(m.value for m in ApiKeyStatus)),
    ("transaction_kind_enum", tuple(m.value for m in TransactionKind)),
)


def _pg_enum(name: str) -> sa.Enum:
    """Reference an existing ENUM type (we create it ourselves above)."""
    from sqlalchemy.dialects.postgresql import (
        ENUM as PgEnum,  # noqa: N811 — `ENUM` это alias под `PgEnum` чтобы не конфликтовать с `sa.Enum`.
    )

    # ``create_type=False`` — op.create_table() must not try to recreate
    # the type. asyncpg + prepared statements don't play well with
    # ``checkfirst=True``, hence the explicit DO-block in helpers.
    return PgEnum(name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    for enum_name, values in _ENUMS:
        create_enum_idempotent(enum_name, values)

    # On Postgres we reference the enum types created above; on SQLite the
    # SAEnum used by the ORM falls back to a CHECK constraint on a TEXT
    # column, so we just declare String.
    def enum_col(name: str) -> sa.types.TypeEngine[str]:
        if is_postgres:
            return _pg_enum(name)
        return sa.String(64)

    create_table_idempotent(
        "users",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("email_verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    create_index_idempotent("ix_users_email", "users", ["email"], unique=True)

    create_table_idempotent(
        "accounts",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("owner_id", GUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("balance_kopecks", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("tariff", enum_col("tariff_enum"), nullable=False, server_default="payg"),
        sa.Column(
            "status",
            enum_col("account_status_enum"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("store_prompts", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("balance_kopecks >= -1000", name="balance_check"),
    )
    create_index_idempotent("ix_accounts_owner_id", "accounts", ["owner_id"])

    create_table_idempotent(
        "seats",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "account_id",
            GUID(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            GUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", enum_col("seat_role_enum"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "user_id", name="uq_seats_account_user"),
    )
    create_index_idempotent("ix_seats_account_id", "seats", ["account_id"])
    create_index_idempotent("ix_seats_user_id", "seats", ["user_id"])

    create_table_idempotent(
        "api_keys",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "account_id",
            GUID(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("key_prefix", sa.String(32), nullable=False),
        sa.Column(
            "scope",
            enum_col("api_key_scope_enum"),
            nullable=False,
            server_default="write",
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            enum_col("api_key_status_enum"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_idempotent("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])
    create_index_idempotent("ix_api_keys_account_id", "api_keys", ["account_id"])
    create_index_idempotent("ix_api_keys_status", "api_keys", ["status"])

    create_table_idempotent(
        "transactions",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "account_id",
            GUID(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", enum_col("transaction_kind_enum"), nullable=False),
        sa.Column("amount_kopecks", sa.BigInteger, nullable=False),
        sa.Column("ref_id", sa.String(255), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_idempotent("ix_transactions_account_id", "transactions", ["account_id"])
    create_index_idempotent(
        "ix_transactions_account_created",
        "transactions",
        ["account_id", "created_at"],
    )

    create_table_idempotent(
        "usage_events",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "account_id",
            GUID(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "api_key_id",
            GUID(),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_kopecks", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_idempotent("ix_usage_events_request_id", "usage_events", ["request_id"])
    create_index_idempotent("ix_usage_events_account_id", "usage_events", ["account_id"])
    create_index_idempotent("ix_usage_events_api_key_id", "usage_events", ["api_key_id"])
    create_index_idempotent(
        "ix_usage_events_account_created",
        "usage_events",
        ["account_id", "created_at"],
    )

    create_table_idempotent(
        "request_payloads",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "usage_event_id",
            GUID(),
            sa.ForeignKey("usage_events.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("encrypted_prompt", sa.LargeBinary, nullable=False),
        sa.Column("encrypted_response", sa.LargeBinary, nullable=False),
        sa.Column("encryption_key_id", sa.String(64), nullable=False),
        sa.Column("opt_out", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_idempotent(
        "ix_request_payloads_retention", "request_payloads", ["retention_until"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table("request_payloads")
    op.drop_table("usage_events")
    op.drop_table("transactions")
    op.drop_table("api_keys")
    op.drop_table("seats")
    op.drop_table("accounts")
    op.drop_table("users")

    if bind.dialect.name == "postgresql":
        # Drop ENUM types in reverse creation order. ``checkfirst=True`` so
        # a partially-applied downgrade can still complete.
        for enum_name, _values in reversed(_ENUMS):
            op.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))
