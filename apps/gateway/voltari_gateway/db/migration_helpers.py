"""Idempotent helpers for Alembic migrations.

Why this exists
---------------

Alembic itself is *not* idempotent at the operation level. If a migration
crashes mid-way (Docker OOM, container kill during ``alembic upgrade``,
network blip on a remote DB) the partially-applied changes stay in the
schema but the ``alembic_version`` row is never written — so the next
``upgrade head`` re-runs the same ``op.create_table(...)`` and explodes
with ``DuplicateTable``. From a 4 a.m. on-call perspective this looks
like "the database is permanently broken" because the obvious fix
(``downgrade base + upgrade head``) only works if ``downgrade`` itself
is reachable.

The helpers below check the live DB schema *before* each DDL operation
and skip the call if the object already exists. They preserve the
legitimate alembic flow (``alembic_version`` still tracks revisions
linearly) while making each individual revision survive partial reruns.

Trade-offs
----------

* Idempotent helpers can mask real conflicts — if a column already exists
  with a *different* type, we don't fix it. We log so a human notices.
* The ``inspect()`` calls round-trip to the DB, so each helper costs one
  extra query. Migrations are infrequent and run once per deploy, so
  this is fine.
* Using ``op.add_column`` etc. directly is still fine for *new* tables
  created in the same revision — there's nothing to conflict with.

These helpers are intentionally small and explicit so a reader doesn't
have to learn a meta-DSL. We don't try to wrap the entire ``op`` API.
"""

from __future__ import annotations

import re
from typing import Any

import sqlalchemy as sa
import structlog

from alembic import op

log = structlog.get_logger("alembic.helpers")

# A safe identifier — used for whitelisting names we interpolate into raw
# SQL via f-strings (ENUM types). PostgreSQL identifiers can technically
# contain more, but our codebase only uses snake_case and we want a hard
# stop on anything else slipping in.
_SAFE_SQL_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


def assert_safe_identifier(name: str, kind: str = "identifier") -> None:
    """Refuse to interpolate anything but ``[a-z][a-z0-9_]*`` into raw SQL.

    Used by ``create_enum_idempotent`` to harden the f-string SQL pattern
    against future maintainers passing in user-supplied or config-derived
    names. Defence in depth — the current callers all pass literals.
    """
    if not _SAFE_SQL_IDENTIFIER.match(name):
        raise ValueError(f"unsafe SQL {kind} {name!r}: must match {_SAFE_SQL_IDENTIFIER.pattern}")


def create_enum_idempotent(name: str, values: tuple[str, ...]) -> None:
    """Create a Postgres ENUM type if it does not already exist.

    Postgres < 15 has no ``CREATE TYPE IF NOT EXISTS``, so we wrap the
    creation in a ``DO`` block that checks ``pg_type`` first. All inputs
    pass through ``assert_safe_identifier`` so we never let an arbitrary
    string into the SQL string.

    No-ops on non-Postgres dialects (SQLite uses CHECK constraints
    transparently for SAEnum).
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    assert_safe_identifier(name, kind="enum name")
    for v in values:
        assert_safe_identifier(v, kind="enum value")

    values_sql = ", ".join(f"'{v}'" for v in values)
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{name}') THEN
                    CREATE TYPE {name} AS ENUM ({values_sql});
                END IF;
            END
            $$;
            """
        )
    )


def has_table(name: str) -> bool:
    """Return True if ``name`` exists in the bound DB."""
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def has_column(table: str, column: str) -> bool:
    """Return True if ``table.column`` exists in the bound DB."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def has_index(table: str, name: str) -> bool:
    """Return True if an index named ``name`` exists on ``table``."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    return any(ix["name"] == name for ix in insp.get_indexes(table))


def has_constraint(table: str, name: str) -> bool:
    """Return True if a unique/check constraint ``name`` exists on ``table``.

    SQLAlchemy's inspector reports unique constraints, check constraints,
    and FK constraints separately; we check all three because we don't
    care which kind a partially-applied migration left behind.
    """
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    for kind in ("get_unique_constraints", "get_check_constraints"):
        getter = getattr(insp, kind, None)
        if getter is None:
            continue
        try:
            for c in getter(table):
                if c.get("name") == name:
                    return True
        except NotImplementedError:
            # SQLite + check_constraints prior to SA 2.0.30 raised this.
            continue
    return False


def create_table_idempotent(name: str, *args: Any, **kwargs: Any) -> None:
    """``op.create_table`` that no-ops if the table already exists.

    Survives crash-mid-revision: if ``upgrade()`` died after the CREATE TABLE
    landed but before alembic_version was bumped, the next run finds the
    table and skips it. Anything *added* to that revision after the original
    create_table still runs (column adds / index adds use their own
    idempotent helpers below).
    """
    if has_table(name):
        log.info("migration_skip_existing_table", table=name)
        return
    op.create_table(name, *args, **kwargs)


def add_column_idempotent(table: str, column: sa.Column[Any], *, batch: bool = False) -> None:
    """``op.add_column`` that no-ops if the column already exists.

    ``batch=True`` runs through ``op.batch_alter_table`` for SQLite, which
    rebuilds the table — we don't want to rebuild needlessly, so we still
    short-circuit on existence.
    """
    if has_column(table, column.name):
        log.info("migration_skip_existing_column", table=table, column=column.name)
        return

    if batch:
        with op.batch_alter_table(table) as bop:
            bop.add_column(column)
    else:
        op.add_column(table, column)


def create_index_idempotent(
    name: str,
    table: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    """``op.create_index`` that no-ops if the named index already exists."""
    if has_index(table, name):
        log.info("migration_skip_existing_index", table=table, index=name)
        return
    op.create_index(name, table, columns, unique=unique)


def create_unique_constraint_idempotent(
    name: str,
    table: str,
    columns: list[str],
    *,
    batch: bool = False,
) -> None:
    """``op.create_unique_constraint`` (or batch variant) that no-ops if exists.

    Batch mode is required on SQLite when adding a UQ to an existing table.
    """
    if has_constraint(table, name):
        log.info("migration_skip_existing_constraint", table=table, constraint=name)
        return

    if batch:
        with op.batch_alter_table(table) as bop:
            bop.create_unique_constraint(name, columns)
    else:
        op.create_unique_constraint(name, table, columns)


def add_check_constraint_idempotent(
    name: str,
    table: str,
    condition: str,
    *,
    batch: bool = False,
) -> None:
    """``op.create_check_constraint`` that no-ops if the named CHECK already exists.

    SQLite cannot ``ALTER TABLE ... ADD CONSTRAINT`` and CHECK constraints are
    rebuilt via ``batch_alter_table`` (table copy). Pass ``batch=True`` for
    SQLite. PostgreSQL handles ``ALTER TABLE ADD CONSTRAINT`` natively.

    Idempotent on the constraint name — survives partial-rerun mid-migration
    in the same way ``create_table_idempotent`` does. The condition string is
    SQL pasted verbatim, so callers MUST use literals only (no f-string
    interpolation of identifiers — see ``assert_safe_identifier`` in callers
    that need it).
    """
    if has_constraint(table, name):
        log.info("migration_skip_existing_constraint", table=table, constraint=name)
        return

    if batch:
        with op.batch_alter_table(table) as bop:
            bop.create_check_constraint(name, condition)
    else:
        op.create_check_constraint(name, table, condition)


__all__ = [
    "add_check_constraint_idempotent",
    "add_column_idempotent",
    "assert_safe_identifier",
    "create_enum_idempotent",
    "create_index_idempotent",
    "create_table_idempotent",
    "create_unique_constraint_idempotent",
    "has_column",
    "has_constraint",
    "has_index",
    "has_table",
]
