"""Tests для retention_cleanup script.

Phase 5 #4 Sprint 3 (2026-05-09).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    GatewayRequestLog,
    Tariff,
    User,
)
from voltari_gateway.scripts.retention_cleanup import cleanup_old_traces

_PASSWORD = "correct horse battery staple"


async def _seed_account(db) -> uuid.UUID:
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="Acme",
            balance_kopecks=100_000,
            tariff=Tariff.PRO,
            status=AccountStatus.ACTIVE,
            store_prompts=False,
            settings={},
        )
    )
    await db.commit()
    return (await db.execute(sa.select(Account).where(Account.owner_id == user.id))).scalar_one().id


async def _seed_trace(
    db,
    *,
    account_id,
    request_id: str,
    created_offset_days: int = 0,
) -> None:
    now = datetime.now(UTC) - timedelta(days=created_offset_days)
    db.add(
        GatewayRequestLog(
            account_id=account_id,
            request_id=request_id,
            provider="openai",
            model="gpt-5.4-mini",
            started_at=now - timedelta(milliseconds=500),
            finished_at=now,
            latency_ms=500,
            prompt_tokens=10,
            completion_tokens=5,
            cached_tokens=0,
            reasoning_tokens=0,
            cost_kop=10,
            status="ok",
            http_code=200,
            is_streaming=False,
            cache_hit=False,
            pii_masked=False,
            tools_used=False,
            created_at=now,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_cleanup_removes_old_rows(db, redis_client) -> None:
    aid = await _seed_account(db)
    await _seed_trace(db, account_id=aid, request_id="recent", created_offset_days=10)
    await _seed_trace(db, account_id=aid, request_id="ancient", created_offset_days=100)

    deleted = await cleanup_old_traces(retention_days=56, chunk_size=10)
    assert deleted == 1

    rows = (await db.execute(sa.select(GatewayRequestLog))).scalars().all()
    request_ids = sorted(r.request_id for r in rows)
    assert request_ids == ["recent"]


@pytest.mark.asyncio
async def test_cleanup_dry_run_changes_nothing(db, redis_client) -> None:
    aid = await _seed_account(db)
    await _seed_trace(db, account_id=aid, request_id="ancient", created_offset_days=100)

    would_delete = await cleanup_old_traces(retention_days=56, dry_run=True)
    assert would_delete == 1

    # Запись не удалилась — её можно прочитать.
    rows = (await db.execute(sa.select(GatewayRequestLog))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_cleanup_handles_empty_table(db, redis_client) -> None:
    deleted = await cleanup_old_traces(retention_days=56)
    assert deleted == 0


@pytest.mark.asyncio
async def test_cleanup_chunked(db, redis_client) -> None:
    """Удаление работает корректно когда строк больше chunk_size."""
    aid = await _seed_account(db)
    for i in range(15):
        await _seed_trace(db, account_id=aid, request_id=f"old-{i}", created_offset_days=100)
    await _seed_trace(db, account_id=aid, request_id="fresh", created_offset_days=1)

    deleted = await cleanup_old_traces(retention_days=56, chunk_size=5)
    assert deleted == 15

    rows = (await db.execute(sa.select(GatewayRequestLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].request_id == "fresh"
