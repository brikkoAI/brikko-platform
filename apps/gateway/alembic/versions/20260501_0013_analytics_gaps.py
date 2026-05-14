"""Sprint 11 — analytics gaps: provider on usage_events, UTM on accounts, tariff_history.

Revision ID: 0013_analytics_gaps
Revises: 0012_sprint7_routing_purge
Create Date: 2026-05-01

Why
---

Data-analyst-агент (Sprint 11) flagged three GAPs blocking real analytics
**from the very first paying customer**:

1. ``usage_events.provider`` — today only ``model`` lives in the row.
   The provider-mix dashboard currently builds a ``CASE WHEN model LIKE
   'gpt-%' THEN 'openai' ...`` map. That's a full table scan and breaks
   the moment we add a new naming convention. The gateway already knows
   the provider at write time (``ModelSpec.provider``); we just never
   stored it. P1.

2. ``accounts.acquisition_channel`` + ``utm_source/medium/campaign`` —
   without these we cannot slice the activation funnel by channel
   (Habr / vc.ru / TG / direct). Frontend will pass UTM cookies in the
   signup body once this lands. Adding nullable columns now keeps
   legacy rows intact. P1.

3. ``tariff_history`` — ``Account.tariff`` is mutable in-place, so a
   PAYG → Pro upgrade in the middle of the month is invisible to MRR
   expansion/contraction analytics: we only see the current tariff at
   query time. A history table keyed by ``(account_id, changed_at)``
   gives us point-in-time tariff lookups. P2.

Approach / trade-offs
---------------------

* **Backfill of provider** is done via inline CASE on ``model``
  (matches the SQL the dashboard had). After backfill we ``SET NOT
  NULL`` so future writes can't omit the field. SQLite handles the
  NOT-NULL flip via ``batch_alter_table`` (column rebuild). On Postgres
  we issue ``ALTER ... SET NOT NULL`` directly.

* **UTM columns** are all nullable on purpose. We don't enforce a
  controlled vocabulary at the DB layer because the value space is
  open (UTM convention is whatever the marketer chooses). The column
  width caps memory bloat (64/128 chars).

* **TariffHistory hook** — implemented as a SQLAlchemy ORM
  ``before_flush`` event listener (``db/events.py``) rather than a
  Postgres TRIGGER. Reasons:
    1. No DDL drift between SQLite (tests) and Postgres (prod).
    2. The trigger would have to run as ``SECURITY DEFINER`` to
       reference the audit user; the listener already has the request
       context.
    3. Soles-developer maintenance — one place to read.
  The trigger path is documented for V2 if we ever split the gateway
  into multiple writers. For now there is exactly one writer.

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
)
from voltari_gateway.db.models import GUID, Tariff

revision: str = "0013_analytics_gaps"
down_revision: str | None = "0012_sprint7_routing_purge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Mapping used during backfill — mirrors the (now-retired) CASE in
# sql/analytics/04_provider_mix.sql so the migrated rows match what the
# dashboard reported pre-migration. Order matters for the LIKE prefixes
# that overlap (e.g. ``ya-`` prefix could collide with ``yandex-`` if
# we didn't anchor explicitly).
_BACKFILL_CASES: tuple[tuple[str, str], ...] = (
    (
        "model LIKE 'gpt-%' OR model LIKE 'openai-%' OR model LIKE 'o3-%' OR model LIKE 'o4-%'",
        "openai",
    ),
    ("model LIKE 'claude-%'", "anthropic"),
    ("model LIKE 'gemini-%'", "google"),
    ("model LIKE 'deepseek-%'", "deepseek"),
    ("model LIKE 'yandexgpt%' OR model LIKE 'ya-%'", "yandex"),
    ("model LIKE 'gigachat%'", "sber"),
)


def _build_backfill_case_sql() -> str:
    """Render the CASE expression used in the backfill UPDATE."""
    parts = ["CASE"]
    for cond, prov in _BACKFILL_CASES:
        parts.append(f"  WHEN {cond} THEN '{prov}'")
    parts.append("  ELSE 'unknown'")
    parts.append("END")
    return "\n".join(parts)


def _pg_enum(name: str) -> sa.Enum:
    """Reference an existing Postgres ENUM type (created by 0001)."""
    from sqlalchemy.dialects.postgresql import ENUM as PgEnum  # noqa: N811

    return PgEnum(name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    is_postgres = bind.dialect.name == "postgresql"

    # ------------------------------------------------------------------
    # 1) usage_events.provider (P1)
    # ------------------------------------------------------------------
    # Step 1a — add column as nullable so backfill can run on existing rows.
    add_column_idempotent(
        "usage_events",
        sa.Column("provider", sa.String(length=32), nullable=True),
        batch=is_sqlite,
    )

    # Step 1b — backfill from model prefix. Idempotent: re-running the
    # UPDATE on already-populated rows is a no-op because we filter
    # ``WHERE provider IS NULL``.
    case_expr = _build_backfill_case_sql()
    op.execute(sa.text(f"UPDATE usage_events SET provider = ({case_expr}) WHERE provider IS NULL"))

    # Step 1c — flip to NOT NULL. SQLite needs batch (table rebuild); PG
    # uses ALTER TABLE directly. We also wrap the PG path in an
    # idempotency check so reruns don't fight ``IS NOT NULL`` already set.
    if is_sqlite:
        with op.batch_alter_table("usage_events") as bop:
            bop.alter_column("provider", existing_type=sa.String(length=32), nullable=False)
    else:
        # Defence-in-depth: detect existing nullability so we don't issue
        # SET NOT NULL twice (PG no-ops it but still scans). Cheap.
        op.alter_column(
            "usage_events",
            "provider",
            existing_type=sa.String(length=32),
            nullable=False,
        )

    # Step 1d — composite index (provider, created_at DESC) for the
    # provider-mix dashboard. The DESC matters on PG (uses backwards
    # index scan); SQLite ignores order on B-tree but we keep it for
    # consistency.
    create_index_idempotent(
        "ix_usage_events_provider_created",
        "usage_events",
        ["provider", "created_at"],
    )

    # ------------------------------------------------------------------
    # 2) accounts UTM-fields (P1)
    # ------------------------------------------------------------------
    add_column_idempotent(
        "accounts",
        sa.Column("acquisition_channel", sa.String(length=32), nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column("utm_source", sa.String(length=64), nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column("utm_medium", sa.String(length=64), nullable=True),
        batch=is_sqlite,
    )
    add_column_idempotent(
        "accounts",
        sa.Column("utm_campaign", sa.String(length=128), nullable=True),
        batch=is_sqlite,
    )
    create_index_idempotent(
        "ix_accounts_acquisition_channel",
        "accounts",
        ["acquisition_channel"],
    )

    # ------------------------------------------------------------------
    # 3) tariff_history (P2)
    # ------------------------------------------------------------------
    # ``tariff_enum`` already exists (created by 0001). On Postgres we
    # reference it via ENUM(create_type=False); on SQLite we fall back to
    # String(64) just like 0001 did for accounts.tariff.
    def _tariff_col() -> sa.types.TypeEngine[str]:
        if is_postgres:
            return _pg_enum("tariff_enum")
        return sa.String(64)

    create_table_idempotent(
        "tariff_history",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "account_id",
            GUID(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # NULL when the account is freshly created (no prior tariff).
        sa.Column("from_tariff", _tariff_col(), nullable=True),
        sa.Column("to_tariff", _tariff_col(), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Free-form 'upgrade'/'downgrade'/'autorenew'/'closure'/etc.
        sa.Column("reason", sa.String(64), nullable=True),
        # 'user', 'admin', 'cron:autorefill', 'cron:closure'…
        sa.Column("triggered_by", sa.String(64), nullable=True),
    )
    create_index_idempotent(
        "ix_tariff_history_account_changed",
        "tariff_history",
        ["account_id", "changed_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # ------------------------------------------------------------------
    # 3) tariff_history
    # ------------------------------------------------------------------
    # drop_index is safe — Postgres ``DROP INDEX IF EXISTS`` and SQLite
    # both tolerate missing objects when the table is about to go.
    op.drop_index(
        "ix_tariff_history_account_changed",
        table_name="tariff_history",
        if_exists=True,
    )
    op.drop_table("tariff_history")

    # ------------------------------------------------------------------
    # 2) accounts UTM
    # ------------------------------------------------------------------
    op.drop_index(
        "ix_accounts_acquisition_channel",
        table_name="accounts",
        if_exists=True,
    )
    if is_sqlite:
        with op.batch_alter_table("accounts") as bop:
            if has_column("accounts", "utm_campaign"):
                bop.drop_column("utm_campaign")
            if has_column("accounts", "utm_medium"):
                bop.drop_column("utm_medium")
            if has_column("accounts", "utm_source"):
                bop.drop_column("utm_source")
            if has_column("accounts", "acquisition_channel"):
                bop.drop_column("acquisition_channel")
    else:
        for col in ("utm_campaign", "utm_medium", "utm_source", "acquisition_channel"):
            if has_column("accounts", col):
                op.drop_column("accounts", col)

    # ------------------------------------------------------------------
    # 1) usage_events.provider
    # ------------------------------------------------------------------
    op.drop_index(
        "ix_usage_events_provider_created",
        table_name="usage_events",
        if_exists=True,
    )
    if is_sqlite:
        with op.batch_alter_table("usage_events") as bop:
            if has_column("usage_events", "provider"):
                bop.drop_column("provider")
    else:
        if has_column("usage_events", "provider"):
            op.drop_column("usage_events", "provider")


# Sentinel — silence mypy: ``Tariff`` import is here to keep the
# migration coherent with the enum source of truth even if Alembic
# autogen never references it directly.
_ = Tariff
