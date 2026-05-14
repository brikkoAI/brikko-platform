"""Sprint 11 — Alembic 0013 round-trip + functional tests.

Verifies the analytics-gap migration (``20260501_0013_analytics_gaps``)
against a real Postgres because three of its operations only behave
correctly on the real DB:

  * ``ALTER COLUMN ... SET NOT NULL`` after a backfill UPDATE.
  * ``CREATE INDEX (provider, created_at DESC)`` against a real B-tree.
  * ``LATERAL`` lookups in the MRR-breakdown SQL (Part Б of
    ``03_mrr_breakdown.sql``) — Postgres-only syntax we rely on.

Strategy mirrors ``test_migrations_roundtrip.py``:

  * SKIP_POSTGRES_TESTS=1            → skipped (CI without Docker).
  * Docker daemon unreachable        → skipped at module import.
  * Otherwise testcontainers brings up postgres:16-alpine for the module.

The test does:

  1. ``upgrade head`` from a clean schema, asserts the new objects
     (column ``usage_events.provider``, four UTM columns on
     ``accounts``, ``tariff_history`` table + index) are present.
  2. Inserts a usage event and an account/tariff_history pair to
     verify writes work end-to-end (incl. the SQLAlchemy event
     listener that appends history rows).
  3. ``downgrade -1`` (back to 0012) and asserts the new objects are
     gone — catches missing DROP statements.
  4. ``upgrade head`` again and re-asserts presence — catches
     non-idempotent up paths.

Runtime: ~12s on a warm Docker daemon.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.config import Config as AlembicConfig
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

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
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(GATEWAY_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", async_url)
    os.environ["DATABASE_URL"] = async_url
    return cfg


@pytest.fixture(scope="module")
def postgres_container() -> Iterator[str]:
    try:
        container = PostgresContainer(
            "postgres:16-alpine",
            username="voltari",
            password="voltari",
            dbname="voltari_schema_v2_test",
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
    engine = create_async_engine(async_url, future=True, poolclass=sa.pool.NullPool)
    async with engine.begin() as conn:
        await conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        await conn.execute(sa.text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest_asyncio.fixture
async def clean_pg(postgres_container: str) -> AsyncIterator[str]:
    await _wipe_schema(postgres_container)
    yield postgres_container


async def _inspect_columns(async_url: str, table: str) -> set[str]:
    engine = create_async_engine(async_url, future=True, poolclass=sa.pool.NullPool)
    try:
        async with engine.connect() as conn:

            def _do(sync_conn: sa.engine.Connection) -> set[str]:
                insp = sa.inspect(sync_conn)
                if not insp.has_table(table):
                    return set()
                return {c["name"] for c in insp.get_columns(table)}

            return await conn.run_sync(_do)
    finally:
        await engine.dispose()


async def _inspect_indexes(async_url: str, table: str) -> set[str]:
    engine = create_async_engine(async_url, future=True, poolclass=sa.pool.NullPool)
    try:
        async with engine.connect() as conn:

            def _do(sync_conn: sa.engine.Connection) -> set[str]:
                insp = sa.inspect(sync_conn)
                if not insp.has_table(table):
                    return set()
                return {ix["name"] for ix in insp.get_indexes(table)}

            return await conn.run_sync(_do)
    finally:
        await engine.dispose()


async def _has_table(async_url: str, table: str) -> bool:
    engine = create_async_engine(async_url, future=True, poolclass=sa.pool.NullPool)
    try:
        async with engine.connect() as conn:

            def _do(sync_conn: sa.engine.Connection) -> bool:
                return sa.inspect(sync_conn).has_table(table)

            return await conn.run_sync(_do)
    finally:
        await engine.dispose()


# --- Tests -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_0013_upgrade_creates_all_objects(clean_pg: str) -> None:
    """``upgrade head`` lands the column, the UTM block, and tariff_history."""
    cfg = _make_alembic_config(clean_pg)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    usage_cols = await _inspect_columns(clean_pg, "usage_events")
    assert "provider" in usage_cols, "usage_events.provider not created by 0013"

    usage_idx = await _inspect_indexes(clean_pg, "usage_events")
    assert "ix_usage_events_provider_created" in usage_idx, (
        "0013 forgot to create ix_usage_events_provider_created"
    )

    accounts_cols = await _inspect_columns(clean_pg, "accounts")
    for col in (
        "acquisition_channel",
        "utm_source",
        "utm_medium",
        "utm_campaign",
    ):
        assert col in accounts_cols, f"accounts.{col} not created by 0013"

    accounts_idx = await _inspect_indexes(clean_pg, "accounts")
    assert "ix_accounts_acquisition_channel" in accounts_idx, (
        "0013 forgot to create ix_accounts_acquisition_channel"
    )

    assert await _has_table(clean_pg, "tariff_history"), "tariff_history not created"
    th_cols = await _inspect_columns(clean_pg, "tariff_history")
    assert {
        "id",
        "account_id",
        "from_tariff",
        "to_tariff",
        "changed_at",
        "reason",
        "triggered_by",
    }.issubset(th_cols), f"tariff_history schema missing columns: have {th_cols}"
    th_idx = await _inspect_indexes(clean_pg, "tariff_history")
    assert "ix_tariff_history_account_changed" in th_idx


@pytest.mark.asyncio
async def test_0013_downgrade_drops_everything(clean_pg: str) -> None:
    """``downgrade -1`` removes the new column, UTM, table, and indexes."""
    cfg = _make_alembic_config(clean_pg)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    await asyncio.to_thread(command.downgrade, cfg, "0012_sprint7_routing_purge")

    usage_cols = await _inspect_columns(clean_pg, "usage_events")
    assert "provider" not in usage_cols, "downgrade left usage_events.provider behind"

    usage_idx = await _inspect_indexes(clean_pg, "usage_events")
    assert "ix_usage_events_provider_created" not in usage_idx, (
        "downgrade left provider index behind"
    )

    accounts_cols = await _inspect_columns(clean_pg, "accounts")
    for col in (
        "acquisition_channel",
        "utm_source",
        "utm_medium",
        "utm_campaign",
    ):
        assert col not in accounts_cols, f"downgrade left accounts.{col} behind"

    assert not await _has_table(clean_pg, "tariff_history"), "downgrade left tariff_history behind"


@pytest.mark.asyncio
async def test_0013_upgrade_downgrade_upgrade_roundtrip(clean_pg: str) -> None:
    """head → -1 → head should be idempotent and end with the same shape."""
    cfg = _make_alembic_config(clean_pg)

    await asyncio.to_thread(command.upgrade, cfg, "head")
    cols_first = await _inspect_columns(clean_pg, "usage_events")

    await asyncio.to_thread(command.downgrade, cfg, "0012_sprint7_routing_purge")
    await asyncio.to_thread(command.upgrade, cfg, "head")

    cols_second = await _inspect_columns(clean_pg, "usage_events")
    assert cols_first == cols_second, "schema drift after round-trip"
    assert await _has_table(clean_pg, "tariff_history")


@pytest.mark.asyncio
async def test_0013_provider_backfill_for_existing_rows(clean_pg: str) -> None:
    """An existing usage_events row created BEFORE 0013 must get a provider tag.

    This simulates the production migration path: rows already exist, the
    column is added nullable, backfilled, and then flipped to NOT NULL.
    The exact provider value is asserted against the gateway's known
    naming convention.
    """
    cfg = _make_alembic_config(clean_pg)
    await asyncio.to_thread(command.upgrade, cfg, "0012_sprint7_routing_purge")

    # Insert a user/account/api_key/usage_event by hand using raw SQL —
    # avoids touching ORM models which already declare ``provider``.
    user_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    api_key_id = str(uuid.uuid4())
    engine = create_async_engine(clean_pg, future=True, poolclass=sa.pool.NullPool)
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, email_verified, "
                "created_at, updated_at) VALUES (:id, :email, 'x', true, NOW(), NOW())"
            ),
            {"id": user_id, "email": f"backfill-{user_id[:8]}@example.com"},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO accounts (id, owner_id, name, balance_kopecks, tariff, "
                "status, store_prompts, settings, autorefill_enabled, pii_masking_enabled, "
                "routing_mode, routing_strategy, created_at, updated_at) "
                "VALUES (:id, :owner, 'a', 0, 'payg', 'active', true, '{}'::jsonb, "
                "false, false, 'smart', 'cheap', NOW(), NOW())"
            ),
            {"id": account_id, "owner": user_id},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO api_keys (id, account_id, name, key_hash, key_prefix, "
                "scope, status, created_at, updated_at) "
                "VALUES (:id, :acc, 'k', 'h', 'sk-vlt-test1', 'write', 'active', NOW(), NOW())"
            ),
            {"id": api_key_id, "acc": account_id},
        )
        # Two events with provider-distinct prefixes so the CASE backfill
        # has to do real work.
        for model in ("gpt-5.4-mini", "claude-sonnet-4-6", "deepseek-v3.2-chat"):
            await conn.execute(
                sa.text(
                    "INSERT INTO usage_events (id, account_id, api_key_id, model, "
                    "input_tokens, output_tokens, cached_tokens, cost_kopecks, "
                    "request_id, created_at, updated_at) VALUES (:id, :acc, :key, "
                    ":model, 10, 20, 0, 100, :req, NOW(), NOW())"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "acc": account_id,
                    "key": api_key_id,
                    "model": model,
                    "req": f"req-{model}",
                },
            )
    await engine.dispose()

    # Apply 0013 — backfill must populate ``provider`` for the rows above.
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(clean_pg, future=True, poolclass=sa.pool.NullPool)
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT model, provider FROM usage_events ORDER BY model")
        )
        rows = result.all()
    await engine.dispose()

    by_model = {r[0]: r[1] for r in rows}
    assert by_model["gpt-5.4-mini"] == "openai"
    assert by_model["claude-sonnet-4-6"] == "anthropic"
    assert by_model["deepseek-v3.2-chat"] == "deepseek"


@pytest.mark.asyncio
async def test_0013_tariff_history_listener_writes_on_upgrade(clean_pg: str) -> None:
    """Updating ``Account.tariff`` through the ORM emits a TariffHistory row.

    Verifies the SQLAlchemy ``before_flush`` listener installed in
    ``voltari_gateway.db.events``: signup-time tariff is NOT logged
    (FK to brand-new account would fail at flush time, см.
    инцидент 2026-05-01). Только UPDATE существующего Account.tariff
    создаёт строку с ``from_tariff = old``, ``to_tariff = new``,
    ``reason``/``triggered_by`` из ``account._tariff_change_meta``.
    """
    cfg = _make_alembic_config(clean_pg)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    from voltari_gateway.db.models import (
        Account,
        AccountStatus,
        Tariff,
        TariffHistory,
        User,
    )

    engine = create_async_engine(clean_pg, future=True, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with factory() as s:
            user = User(
                email=f"hist-{uuid.uuid4().hex[:8]}@test.local",
                password_hash="x",
                email_verified=True,
            )
            s.add(user)
            await s.flush()

            account = Account(
                owner_id=user.id,
                name="hist",
                balance_kopecks=0,
                tariff=Tariff.PAYG,
                status=AccountStatus.ACTIVE,
                store_prompts=True,
            )
            s.add(account)
            await s.commit()
            account_id = account.id

        # Signup НЕ пишет строку — listener пропускает INSERT Account
        # из-за FK гонки в before_flush (см. db/events.py docstring).
        async with factory() as s:
            rows = (
                (
                    await s.execute(
                        sa.select(TariffHistory).where(TariffHistory.account_id == account_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 0, f"signup must NOT write history row, got {len(rows)}"

        # UPDATE Account.tariff — listener должен записать строку.
        async with factory() as s:
            account = (
                await s.execute(sa.select(Account).where(Account.id == account_id))
            ).scalar_one()
            account._tariff_change_meta = {  # type: ignore[attr-defined]
                "reason": "user_upgrade",
                "triggered_by": "user",
            }
            account.tariff = Tariff.PRO
            await s.commit()

        async with factory() as s:
            rows = (
                (
                    await s.execute(
                        sa.select(TariffHistory)
                        .where(TariffHistory.account_id == account_id)
                        .order_by(TariffHistory.changed_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1, f"expected 1 history row after upgrade, got {len(rows)}"
            r = rows[0]
            assert r.from_tariff == Tariff.PAYG
            assert r.to_tariff == Tariff.PRO
            assert r.reason == "user_upgrade"
            assert r.triggered_by == "user"
    finally:
        await engine.dispose()


# Suppress unused-import warning — Account type used only in the listener
# test above; keeping the explicit re-export keeps mypy happy.
_ = Any
