"""Tests for ``voltari_gateway.billing.janitor`` (TD-028).

The janitor is a thin wrapper over a single SQL DELETE; we test the
DELETE predicate directly against the in-memory SQLite + ORM. The
loop's tick scheduling is exercised separately to keep test runtime
short.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voltari_gateway.billing.janitor import (
    hold_janitor_loop,
    sweep_expired_holds,
)
from voltari_gateway.db.models import AccountHold

# ---- helpers ----------------------------------------------------------------


async def _seed_hold(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    ref_id: str,
    expires_at: datetime,
    amount_kopecks: int = 100,
) -> AccountHold:
    hold = AccountHold(
        account_id=account_id,
        ref_id=ref_id,
        amount_kopecks=amount_kopecks,
        created_at=datetime.now(UTC),
        expires_at=expires_at,
    )
    db.add(hold)
    await db.flush()
    return hold


# ---- direct sweep tests -----------------------------------------------------


@pytest.mark.asyncio
async def test_janitor_deletes_expired_holds(db: AsyncSession, api_key_fixture: Any) -> None:
    """Hold expired beyond grace window is removed in one sweep."""
    account_id = api_key_fixture.account.id
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    await _seed_hold(db, account_id=account_id, ref_id="stale-1", expires_at=long_ago)
    await db.commit()

    deleted = await sweep_expired_holds(db, grace_seconds=300)
    await db.commit()
    assert deleted == 1

    rows = (await db.execute(select(AccountHold))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_janitor_keeps_active_holds(db: AsyncSession, api_key_fixture: Any) -> None:
    """Hold whose expires_at is in the future must NOT be touched."""
    account_id = api_key_fixture.account.id
    soon = datetime.now(UTC) + timedelta(seconds=120)
    await _seed_hold(db, account_id=account_id, ref_id="active-1", expires_at=soon)
    await db.commit()

    deleted = await sweep_expired_holds(db, grace_seconds=300)
    await db.commit()
    assert deleted == 0

    rows = (await db.execute(select(AccountHold))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_janitor_respects_grace_window(db: AsyncSession, api_key_fixture: Any) -> None:
    """A hold past expires_at but within the grace window should NOT be
    swept — so a request still in flight isn't yanked out from under us.
    """
    account_id = api_key_fixture.account.id
    # 60s past expiry, but grace is 300s → must stay.
    just_past = datetime.now(UTC) - timedelta(seconds=60)
    await _seed_hold(db, account_id=account_id, ref_id="just-past-1", expires_at=just_past)
    await db.commit()

    deleted = await sweep_expired_holds(db, grace_seconds=300)
    await db.commit()
    assert deleted == 0


@pytest.mark.asyncio
async def test_janitor_mixed_population(db: AsyncSession, api_key_fixture: Any) -> None:
    """In a mixed population only the truly stale rows are removed."""
    account_id = api_key_fixture.account.id
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    just_past = datetime.now(UTC) - timedelta(seconds=10)
    soon = datetime.now(UTC) + timedelta(minutes=2)

    await _seed_hold(db, account_id=account_id, ref_id="stale-A", expires_at=long_ago)
    await _seed_hold(db, account_id=account_id, ref_id="stale-B", expires_at=long_ago)
    await _seed_hold(db, account_id=account_id, ref_id="grace-A", expires_at=just_past)
    await _seed_hold(db, account_id=account_id, ref_id="active-A", expires_at=soon)
    await db.commit()

    deleted = await sweep_expired_holds(db, grace_seconds=300)
    await db.commit()
    assert deleted == 2

    survived = (await db.execute(select(AccountHold.ref_id))).scalars().all()
    assert sorted(survived) == ["active-A", "grace-A"]


# ---- loop tests -------------------------------------------------------------


@pytest.mark.asyncio
async def test_janitor_loop_runs_until_stop(
    session_factory: async_sessionmaker[AsyncSession],
    db: AsyncSession,
    api_key_fixture: Any,
) -> None:
    """Driving the loop with stop_event for one tick should clean stale
    holds and exit cleanly."""
    account_id = api_key_fixture.account.id
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    await _seed_hold(db, account_id=account_id, ref_id="loop-stale", expires_at=long_ago)
    await db.commit()

    stop = asyncio.Event()
    task = asyncio.create_task(
        hold_janitor_loop(
            session_factory=session_factory,
            interval_seconds=10,  # not relevant; we stop quickly
            grace_seconds=300,
            stop_event=stop,
        )
    )
    # Give the first tick time to land.
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)

    rows = (await db.execute(select(AccountHold))).scalars().all()
    assert rows == []
