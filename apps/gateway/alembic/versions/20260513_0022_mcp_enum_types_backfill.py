"""mcp_enum_types_backfill — добор PG enum types которые забыли создать в 0019.

Revision ID: 0022_mcp_enum_types_backfill
Revises: 0021_mcp_scopes_v2
Create Date: 2026-05-13

PRODUCTION INCIDENT 2026-05-13: при попытке создать MCP-token через UI prod
вернул 500. Корень: миграция ``20260511_0019_mcp_tokens.py`` создала колонки
``mcp_tokens.scope`` и ``mcp_tokens.status`` как ``String`` + CHECK
constraint, а ORM (``voltari_gateway.db.models.McpToken``) использует
``Mapped[McpScope]`` через ``_enum_col(McpScope, "mcp_scope_enum")`` —
SQLAlchemy генерит ``$2::mcp_scope_enum`` cast на каждом запросе, и в PG
этого type нет → ``UndefinedObjectError``.

Hot-fix на проде 2026-05-13: ``CREATE TYPE + ALTER COLUMN ... TYPE`` вручную
через psql. Эта миграция идемпотентно делает то же самое для:
* fresh deployments (новый VPS или disaster recovery)
* integration tests (testcontainers postgres)
* SQLite — no-op (там оба варианта — varchar, ORM-cast игнорируется)

Migration безопасна:
* Если type уже существует (как на проде после hot-fix) — пропускается.
* Если column уже ``mcp_scope_enum`` — пропускается.
* На SQLite — полный no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022_mcp_enum_types_backfill"
down_revision: str | None = "0021_mcp_scopes_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SCOPE_VALUES = (
    "all",
    "read_account",
    "read_usage",
    "recommend_model",
    "list_models",
    "read_traces",
    "list_cookbook",
    "list_integrations",
)
_STATUS_VALUES = ("active", "revoked")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite hosts varchar columns; ORM-cast не применяется.
        return

    # 1) Создать enum types если их ещё нет.
    scope_values_sql = ", ".join(f"'{v}'" for v in _SCOPE_VALUES)
    status_values_sql = ", ".join(f"'{v}'" for v in _STATUS_VALUES)

    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'mcp_scope_enum') THEN
                CREATE TYPE mcp_scope_enum AS ENUM ({scope_values_sql});
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'mcp_token_status_enum') THEN
                CREATE TYPE mcp_token_status_enum AS ENUM ({status_values_sql});
            END IF;
        END$$;
    """)

    # 2) Конвертировать columns если они ещё VARCHAR.
    #    information_schema.columns.udt_name = 'mcp_scope_enum' если уже enum.
    op.execute("""
        DO $$
        DECLARE
            scope_udt text;
            status_udt text;
        BEGIN
            SELECT udt_name INTO scope_udt
              FROM information_schema.columns
              WHERE table_name = 'mcp_tokens' AND column_name = 'scope';

            IF scope_udt IS NOT NULL AND scope_udt != 'mcp_scope_enum' THEN
                ALTER TABLE mcp_tokens ALTER COLUMN scope DROP DEFAULT;
                ALTER TABLE mcp_tokens ALTER COLUMN scope
                    TYPE mcp_scope_enum USING scope::mcp_scope_enum;
            END IF;

            SELECT udt_name INTO status_udt
              FROM information_schema.columns
              WHERE table_name = 'mcp_tokens' AND column_name = 'status';

            IF status_udt IS NOT NULL AND status_udt != 'mcp_token_status_enum' THEN
                ALTER TABLE mcp_tokens ALTER COLUMN status DROP DEFAULT;
                ALTER TABLE mcp_tokens ALTER COLUMN status
                    TYPE mcp_token_status_enum USING status::mcp_token_status_enum;
                ALTER TABLE mcp_tokens ALTER COLUMN status
                    SET DEFAULT 'active'::mcp_token_status_enum;
            END IF;
        END$$;
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Convert back to varchar (with CHECK constraints, как было в 0019).
    # NB: asyncpg запрещает несколько statements в одном prepared statement,
    # поэтому каждый ALTER идёт отдельным op.execute().
    op.execute("ALTER TABLE mcp_tokens ALTER COLUMN scope DROP DEFAULT")
    op.execute("ALTER TABLE mcp_tokens ALTER COLUMN scope TYPE varchar(32) USING scope::text")
    op.execute("ALTER TABLE mcp_tokens ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE mcp_tokens ALTER COLUMN status TYPE varchar(16) USING status::text")
    op.execute("ALTER TABLE mcp_tokens ALTER COLUMN status SET DEFAULT 'active'")

    op.execute("DROP TYPE IF EXISTS mcp_scope_enum")
    op.execute("DROP TYPE IF EXISTS mcp_token_status_enum")
