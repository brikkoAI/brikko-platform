"""Round-trip + drift tests for Alembic migrations against real Postgres.

The unit test suite uses ``Base.metadata.create_all`` against in-memory
SQLite — fast, but it never exercises the migration files at all. The
29.04 self-launch hit a partial-rerun bug (DuplicateTable on retry)
that pure ORM-based tests never could have caught, so we run the real
migrations against a real Postgres and verify three properties:

1. **Round-trip**: ``upgrade head`` → ``downgrade base`` → ``upgrade head``
   succeeds. Catches non-idempotent CREATE TYPE / CREATE TABLE patterns
   and missing DROPs in downgrade().

2. **Crash-mid-revision recovery**: simulate a crash where alembic_version
   was never bumped but a table from the next revision already exists.
   The next ``upgrade head`` must succeed because the helpers are
   idempotent.

3. **Schema drift**: after ``upgrade head`` the live DB schema matches
   ``Base.metadata`` (columns the ORM expects exist in DB and vice versa).
   This catches the BE P0-22 class of bug where a model gains a column
   but the migration was never written.

Skip strategy mirrors ``test_welcome_credit_postgres.py``:
* ``SKIP_POSTGRES_TESTS=1`` → skipped (CI without Docker, dev workstation).
* Docker daemon unreachable → skipped via ``pytest.skip`` at module import.

Driver note: the project pins ``asyncpg`` only — no ``psycopg2`` is
installed. We drive Alembic over the asyncpg URL because env.py already
goes through ``async_engine_from_config``. Verification queries
(inspecting tables/columns) also use ``create_async_engine`` and
``run_sync(inspect)`` so we never need the sync driver.

Runtime: ~15s on a warm Docker daemon (cold pull adds ~30s once).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.config import Config as AlembicConfig
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

# --- Skip-marker plumbing ----------------------------------------------------

pytestmark = pytest.mark.integration

if os.environ.get("SKIP_POSTGRES_TESTS", "").lower() in ("1", "true", "yes"):
    pytest.skip(
        "SKIP_POSTGRES_TESTS=1 — postgres integration tests disabled.",
        allow_module_level=True,
    )

testcontainers = pytest.importorskip("testcontainers.postgres")
PostgresContainer = testcontainers.PostgresContainer  # type: ignore[attr-defined]


# --- Helpers -----------------------------------------------------------------


GATEWAY_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = GATEWAY_ROOT / "alembic.ini"


def _make_alembic_config(async_url: str) -> AlembicConfig:
    """Build an Alembic config that points at the given DB.

    env.py uses ``async_engine_from_config`` and reads from
    ``sqlalchemy.url``, so passing the ``postgresql+asyncpg://`` URL
    directly is fine — the migration runs in its own asyncio loop.

    We also set ``DATABASE_URL`` because env.py preferentially reads
    that env var (production behaviour) before falling back to the
    ini-file URL.
    """
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(GATEWAY_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", async_url)
    os.environ["DATABASE_URL"] = async_url
    return cfg


@pytest.fixture(scope="module")
def postgres_container() -> Iterator[str]:
    """Spin up postgres:16 and yield the asyncpg URL.

    All test interactions go through async engines because the project
    only installs the asyncpg driver. testcontainers gives us a
    psycopg2-style URL by default; we rewrite to asyncpg for both
    Alembic and verification queries.
    """
    try:
        container = PostgresContainer(
            "postgres:16-alpine",
            username="voltari",
            password="voltari",
            dbname="voltari_migrations_test",
        )
        container.start()
    except Exception as exc:  # docker daemon down, image pull failed, etc.
        pytest.skip(f"Docker/Postgres unavailable: {exc}")

    try:
        sync_url = container.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        if "+asyncpg" not in async_url:
            async_url = async_url.replace("postgresql://", "postgresql+asyncpg://")
        yield async_url
    finally:
        container.stop()


async def _wipe_schema(async_url: str) -> None:
    """Drop+recreate the public schema so each test starts blank."""
    engine = create_async_engine(async_url, future=True, poolclass=sa.pool.NullPool)
    async with engine.begin() as conn:
        await conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        await conn.execute(sa.text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest_asyncio.fixture
async def clean_pg(postgres_container: str) -> AsyncIterator[str]:
    await _wipe_schema(postgres_container)
    yield postgres_container


async def _inspect(async_url: str) -> dict[str, Any]:
    """Return ``{tables: {name: {column_names}}}`` from the live DB."""
    engine = create_async_engine(async_url, future=True, poolclass=sa.pool.NullPool)
    try:
        async with engine.connect() as conn:

            def _do(sync_conn: sa.engine.Connection) -> dict[str, Any]:
                insp = sa.inspect(sync_conn)
                return {
                    "tables": {
                        t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()
                    },
                }

            return await conn.run_sync(_do)
    finally:
        await engine.dispose()


# --- Tests -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alembic_upgrade_downgrade_upgrade(clean_pg: str) -> None:
    """The canonical round-trip: head → base → head.

    If any migration leaks state across downgrade (e.g. forgets to drop
    an ENUM type, leaves an index behind), the second ``upgrade head``
    fails on duplicate-object errors that the pre-29.04 code couldn't
    survive.
    """
    cfg = _make_alembic_config(clean_pg)

    # Each Alembic command starts its own asyncio loop via env.py's
    # ``asyncio.run(...)``. Running them inline from this async test is
    # only safe if we offload to a thread, otherwise we hit
    # "asyncio.run() cannot be called from a running loop".
    await asyncio.to_thread(command.upgrade, cfg, "head")
    state_after_first_upgrade = await _inspect(clean_pg)
    _assert_core_tables(state_after_first_upgrade, present=True)

    await asyncio.to_thread(command.downgrade, cfg, "base")
    state_after_downgrade = await _inspect(clean_pg)
    _assert_core_tables(state_after_downgrade, present=False)

    await asyncio.to_thread(command.upgrade, cfg, "head")
    state_after_second_upgrade = await _inspect(clean_pg)
    _assert_core_tables(state_after_second_upgrade, present=True)

    # Tables and columns should match between the two head states.
    assert state_after_first_upgrade["tables"] == state_after_second_upgrade["tables"], (
        "Schema drift detected after downgrade+upgrade cycle"
    )


@pytest.mark.asyncio
async def test_alembic_partial_crash_recovery(clean_pg: str) -> None:
    """Simulate a crash mid-migration and verify upgrade still completes.

    Concrete scenario: someone applied 0001 cleanly, then container was
    killed half-way through 0002 — the ``processed_webhooks`` table is
    already there but ``alembic_version`` still says 0001_initial.
    The next ``upgrade head`` MUST succeed because our helpers
    short-circuit on existing objects.

    Pre-29.04 (op.create_table without IF-NOT-EXISTS) this would crash
    forever and the only recovery was to manually edit alembic_version
    or wipe the schema.
    """
    cfg = _make_alembic_config(clean_pg)
    await asyncio.to_thread(command.upgrade, cfg, "0001_initial")

    # Manually create the table that 0002 will try to create — crash sim.
    engine = create_async_engine(clean_pg, future=True, poolclass=sa.pool.NullPool)
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "CREATE TABLE processed_webhooks ("
                "  payment_id varchar(255) PRIMARY KEY,"
                "  event varchar(64) NOT NULL,"
                "  first_seen_at timestamptz NOT NULL,"
                "  processed_at timestamptz NOT NULL,"
                "  source varchar(32) NOT NULL DEFAULT 'yookassa'"
                ")"
            )
        )
    await engine.dispose()

    # The next upgrade head must NOT die on DuplicateTable.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    state = await _inspect(clean_pg)
    _assert_core_tables(state, present=True)


@pytest.mark.asyncio
async def test_alembic_partial_enum_recovery(clean_pg: str) -> None:
    """Same as crash recovery but for the ENUM-type creation in 0001.

    Pre-fix the f-string ``CREATE TYPE`` crashed on rerun if the type
    already existed. The DO-block + ``IF NOT EXISTS`` check fixes it,
    and we verify by pre-creating one of the enum types and running
    upgrade fresh.
    """
    engine = create_async_engine(clean_pg, future=True, poolclass=sa.pool.NullPool)
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "CREATE TYPE tariff_enum AS ENUM ("
                "  'payg', 'pro', 'team', 'business', 'business_plus'"
                ")"
            )
        )
    await engine.dispose()

    cfg = _make_alembic_config(clean_pg)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    state = await _inspect(clean_pg)
    _assert_core_tables(state, present=True)


@pytest.mark.asyncio
async def test_schema_alignment_with_orm_metadata(clean_pg: str) -> None:
    """After ``upgrade head`` the DB schema matches ``Base.metadata``.

    Specifically:
    * Every ORM table exists in the DB.
    * Every ORM column exists in the DB.

    This catches drift like a model gaining ``settings JSONB`` without a
    matching ``add_column`` migration (BE P0-11). We do NOT compare
    column types — exact type equivalence is too brittle across SA
    versions and dialect quirks (e.g. JSON vs JSONB). Names alone catch
    the dominant class of drift in this codebase.
    """
    from voltari_gateway.db.models import Base

    cfg = _make_alembic_config(clean_pg)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    state = await _inspect(clean_pg)
    db_tables = set(state["tables"].keys())
    # Drop alembic's bookkeeping table — it's not in Base.metadata.
    db_tables.discard("alembic_version")

    orm_tables = set(Base.metadata.tables.keys())

    missing_in_db = orm_tables - db_tables
    extra_in_db = db_tables - orm_tables
    assert not missing_in_db, (
        f"ORM declares tables that the migrations do not create: {sorted(missing_in_db)}"
    )
    assert not extra_in_db, f"DB has tables that the ORM does not know about: {sorted(extra_in_db)}"

    for table_name in sorted(orm_tables):
        db_cols = state["tables"][table_name]
        orm_cols = {c.name for c in Base.metadata.tables[table_name].columns}

        missing_cols = orm_cols - db_cols
        extra_cols = db_cols - orm_cols

        assert not missing_cols, (
            f"Table {table_name!r}: ORM declares columns the migration does "
            f"not create: {sorted(missing_cols)}"
        )
        assert not extra_cols, (
            f"Table {table_name!r}: DB has columns the ORM does not declare: {sorted(extra_cols)}"
        )


# --- Internal helpers --------------------------------------------------------


_CORE_TABLES = {
    "users",
    "accounts",
    "seats",
    "api_keys",
    "transactions",
    "usage_events",
    "request_payloads",
    "processed_webhooks",
    "welcome_credits_log",
    "email_invites",
}


def _assert_core_tables(state: dict[str, Any], *, present: bool) -> None:
    """Assert every table in ``_CORE_TABLES`` is (or isn't) in ``state``."""
    found = set(state["tables"].keys())
    if present:
        missing = _CORE_TABLES - found
        assert not missing, f"missing tables after upgrade: {sorted(missing)}"
    else:
        present_unexpectedly = _CORE_TABLES & found
        assert not present_unexpectedly, (
            f"tables still present after downgrade base: {sorted(present_unexpectedly)}"
        )
