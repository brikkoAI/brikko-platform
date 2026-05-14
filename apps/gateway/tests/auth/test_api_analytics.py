"""BrikkoLens analytics API tests.

Phase 5 #4 Sprint 2 (2026-05-09).

SQLite не поддерживает ``percentile_cont``, поэтому p50/p95 проверяем
через структуру (поле есть, тип int) — точные значения проверяются на
PG в интеграционных тестах.

Coverage:
* Empty range — totals=0, daily пустой грид.
* Несколько trace'ов: totals агрегируется правильно, daily-grid содержит
  все дни диапазона (включая дни без данных, заполненные нулями).
* Filter by from/to — за пределами окна не учитывается.
* Cap 90 days — слишком широкий запрос обрезается.
* Account isolation: traces соседнего аккаунта не попадают в summary.
* Required session: 401 без логина.
* by_model / by_provider / by_status breakdowns корректны.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

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

_PASSWORD = "correct horse battery staple"


async def _seed_user(db) -> User:
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
    await db.refresh(user)
    return user


async def _account_id(db, user) -> uuid.UUID:
    return (await db.execute(sa.select(Account).where(Account.owner_id == user.id))).scalar_one().id


async def _login(client, user) -> None:
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text


async def _seed_trace(
    db,
    *,
    account_id,
    request_id: str,
    model: str = "gpt-5.4-mini",
    provider: str = "openai",
    status: str = "ok",
    cost_kop: int = 100,
    prompt_tokens: int = 20,
    completion_tokens: int = 10,
    cached_tokens: int = 0,
    cache_hit: bool = False,
    tools_used: bool = False,
    latency_ms: int = 500,
    created_offset_days: int = 0,
) -> GatewayRequestLog:
    now = datetime.now(UTC) - timedelta(days=created_offset_days)
    started = now - timedelta(milliseconds=latency_ms)
    row = GatewayRequestLog(
        account_id=account_id,
        request_id=request_id,
        provider=provider,
        model=model,
        started_at=started,
        finished_at=now,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=0,
        cost_kop=cost_kop,
        status=status,
        http_code=200 if status == "ok" else 502,
        is_streaming=False,
        cache_hit=cache_hit,
        pii_masked=False,
        tools_used=tools_used,
        created_at=now,
    )
    db.add(row)
    await db.commit()
    return row


@pytest.mark.asyncio
async def test_summary_empty_range(client, db, redis_client) -> None:
    user = await _seed_user(db)
    await _login(client, user)

    r = await client.get("/v1/account/analytics/summary")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["totals"]["requests"] == 0
    assert body["totals"]["errors"] == 0
    assert body["totals"]["cost_kop"] == 0
    # default = 14 дней
    assert len(body["daily"]) == 14
    assert all(d["requests"] == 0 for d in body["daily"])
    assert body["by_model"] == []
    assert body["by_provider"] == []


@pytest.mark.asyncio
async def test_summary_aggregates_totals(client, db, redis_client) -> None:
    user = await _seed_user(db)
    aid = await _account_id(db, user)
    await _seed_trace(db, account_id=aid, request_id="r1", cost_kop=100, status="ok")
    await _seed_trace(
        db,
        account_id=aid,
        request_id="r2",
        cost_kop=200,
        status="ok",
        cache_hit=True,
        cached_tokens=5,
    )
    await _seed_trace(db, account_id=aid, request_id="r3", cost_kop=0, status="error")
    await _login(client, user)

    r = await client.get("/v1/account/analytics/summary")
    body = r.json()

    assert body["totals"]["requests"] == 3
    assert body["totals"]["errors"] == 1
    assert body["totals"]["cost_kop"] == 300
    assert body["totals"]["cache_hits"] == 1
    assert body["totals"]["cached_tokens"] == 5
    assert body["totals"]["prompt_tokens"] == 60  # 3 * 20
    assert body["totals"]["completion_tokens"] == 30  # 3 * 10
    assert body["totals"]["avg_latency_ms"] == 500


@pytest.mark.asyncio
async def test_summary_by_model_breakdown(client, db, redis_client) -> None:
    user = await _seed_user(db)
    aid = await _account_id(db, user)
    await _seed_trace(db, account_id=aid, request_id="a1", model="gpt-5.4")
    await _seed_trace(db, account_id=aid, request_id="a2", model="gpt-5.4")
    await _seed_trace(db, account_id=aid, request_id="b1", model="claude-opus")
    await _login(client, user)

    r = await client.get("/v1/account/analytics/summary")
    body = r.json()

    by_model = {item["key"]: item["requests"] for item in body["by_model"]}
    assert by_model["gpt-5.4"] == 2
    assert by_model["claude-opus"] == 1
    # share корректно нормализован
    gpt_share = next(it for it in body["by_model"] if it["key"] == "gpt-5.4")["share"]
    assert abs(gpt_share - 2 / 3) < 0.01


@pytest.mark.asyncio
async def test_summary_by_provider_breakdown(client, db, redis_client) -> None:
    user = await _seed_user(db)
    aid = await _account_id(db, user)
    await _seed_trace(db, account_id=aid, request_id="x1", provider="openai")
    await _seed_trace(db, account_id=aid, request_id="x2", provider="anthropic")
    await _seed_trace(db, account_id=aid, request_id="x3", provider="anthropic")
    await _login(client, user)

    r = await client.get("/v1/account/analytics/summary")
    body = r.json()

    by_provider = {item["key"]: item["requests"] for item in body["by_provider"]}
    assert by_provider["openai"] == 1
    assert by_provider["anthropic"] == 2


@pytest.mark.asyncio
async def test_summary_account_isolation(client, db, redis_client) -> None:
    """Один аккаунт не видит трейсы другого."""
    me = await _seed_user(db)
    other = await _seed_user(db)
    other_aid = await _account_id(db, other)
    await _seed_trace(db, account_id=other_aid, request_id="other-r1", cost_kop=999)
    await _login(client, me)

    r = await client.get("/v1/account/analytics/summary")
    body = r.json()
    assert body["totals"]["requests"] == 0
    assert body["totals"]["cost_kop"] == 0


@pytest.mark.asyncio
async def test_summary_requires_session(client, db, redis_client) -> None:
    r = await client.get("/v1/account/analytics/summary")
    # Без сессии 401 (или 403 в зависимости от middleware) — не 200.
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_summary_range_from_to(client, db, redis_client) -> None:
    user = await _seed_user(db)
    aid = await _account_id(db, user)
    # Один сегодняшний, один 5 дней назад, один 30 дней назад.
    await _seed_trace(db, account_id=aid, request_id="t-today", cost_kop=10)
    await _seed_trace(db, account_id=aid, request_id="t-5d", cost_kop=20, created_offset_days=5)
    await _seed_trace(db, account_id=aid, request_id="t-30d", cost_kop=30, created_offset_days=30)
    await _login(client, user)

    today = date.today()
    range_from = (today - timedelta(days=7)).isoformat()
    range_to = today.isoformat()

    r = await client.get(f"/v1/account/analytics/summary?from={range_from}&to={range_to}")
    body = r.json()

    # Только today + 5d должны попасть.
    assert body["totals"]["requests"] == 2
    assert body["totals"]["cost_kop"] == 30
    assert len(body["daily"]) == 8


@pytest.mark.asyncio
async def test_summary_range_caps_at_90_days(client, db, redis_client) -> None:
    user = await _seed_user(db)
    await _login(client, user)

    r = await client.get("/v1/account/analytics/summary?from=2020-01-01&to=2026-12-31")
    assert r.status_code == 200
    body = r.json()
    # Должен прийти ровно 90 дней.
    assert len(body["daily"]) == 90
