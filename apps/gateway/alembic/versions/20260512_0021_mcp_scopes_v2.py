"""mcp_scopes_v2 — extend McpScope enum for S3 read-only tools.

Revision ID: 0021_mcp_scopes_v2
Revises: 0020_provider_cookies
Create Date: 2026-05-12

Sprint MCP S3 (CEO 2026-05-12) — 4 new read-only tools + ``all`` wildcard.

The CHECK constraint on ``mcp_tokens.scope`` in 0019 enumerated three
literals: ``read_account | read_usage | recommend_model``. This sprint
adds:

* ``list_models``       — catalog of models with RUB pricing
* ``read_traces``       — last 20 traces from ``gateway_request_log``
* ``list_cookbook``     — 5 ready-made prompt recipes
* ``list_integrations`` — Cursor/Cline/Claude Code tool guides
* ``all``               — wildcard granted to default helper-skill tokens

Single-scope-per-token still holds for **restrictive** tokens (a user can
mint a token with e.g. ``read_account`` only). The ``all`` super-scope
keeps the helper-skill onboarding one click — token created via
``brikko-helper init`` carries ``all`` and works against every tool
without forcing a scope-picker.

Two-step drop+recreate of the CHECK constraint:

* Postgres: idempotent — IF EXISTS the constraint is dropped; the new
  one supersedes it. Existing rows are untouched (values stay valid).
* SQLite: ``ALTER TABLE … DROP CONSTRAINT`` is unsupported. We use the
  batch-mode wrapper which copies the table to a temp with the new
  CHECK and renames. ``recreate='always'`` because SQLite never honoured
  the original CHECK literally (it stores it but doesn't reparse on
  ALTER); forcing recreate keeps the two dialects honest.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021_mcp_scopes_v2"
down_revision: str | None = "0020_provider_cookies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_SCOPES = (
    "read_account",
    "read_usage",
    "recommend_model",
    "list_models",
    "read_traces",
    "list_cookbook",
    "list_integrations",
    "all",
)
_NEW_SCOPES_SQL = ",".join(f"'{s}'" for s in _NEW_SCOPES)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        # Drop-and-add. Old constraint name comes from 0019:
        # ``ck_mcp_tokens_scope``. We use a guarded IF EXISTS so a partial-
        # rerun (e.g. after a botched earlier upgrade) doesn't error.
        op.execute("ALTER TABLE mcp_tokens DROP CONSTRAINT IF EXISTS ck_mcp_tokens_scope")
        op.execute(
            f"ALTER TABLE mcp_tokens "
            f"ADD CONSTRAINT ck_mcp_tokens_scope "
            f"CHECK (scope IN ({_NEW_SCOPES_SQL}))"
        )
    else:
        # SQLite — batch_alter_table copies the table with the new CHECK.
        with op.batch_alter_table("mcp_tokens", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_mcp_tokens_scope", type_="check")
            batch_op.create_check_constraint(
                "ck_mcp_tokens_scope",
                f"scope IN ({_NEW_SCOPES_SQL})",
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # Reverting drops the four new scopes from the allow-list. We do NOT
    # delete existing rows that hold one of the dropped scopes — the
    # CHECK is only enforced on INSERT/UPDATE, and a downgrade in
    # production after rows exist with new scopes is operator-error
    # territory. We surface a clear error in that case rather than
    # silently corrupting auth.
    old_scopes_sql = "'read_account','read_usage','recommend_model'"

    if is_postgres:
        op.execute("ALTER TABLE mcp_tokens DROP CONSTRAINT IF EXISTS ck_mcp_tokens_scope")
        op.execute(
            f"ALTER TABLE mcp_tokens "
            f"ADD CONSTRAINT ck_mcp_tokens_scope "
            f"CHECK (scope IN ({old_scopes_sql}))"
        )
    else:
        with op.batch_alter_table("mcp_tokens", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_mcp_tokens_scope", type_="check")
            batch_op.create_check_constraint(
                "ck_mcp_tokens_scope",
                f"scope IN ({old_scopes_sql})",
            )
