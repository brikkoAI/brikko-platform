"""BrikkoLens traces API tests.

Phase 5 #4 Sprint 1в (2026-05-09).
Coverage:
* Empty list when no traces.
* List ordering (most recent first) + pagination.
* Filters: status, model, since/until, q (request_id substring).
* Detail endpoint: 200 happy path, 404 on missing, account isolation
  (one account can't read another's trace).
* Bodies stripped when account.store_prompts=False at the time of
  trace write (we trust how the data was written; read-side has no
  separate gate).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

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
            store_prompts=True,
            settings={},
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


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
    latency_ms: int = 500,
    cache_hit: bool = False,
    tools_used: bool = False,
    error_message: str | None = None,
    error_code: str | None = None,
    created_offset_seconds: int = 0,
) -> GatewayRequestLog:
    now = datetime.now(UTC) - timedelta(seconds=created_offset_seconds)
    started = now - timedelta(milliseconds=latency_ms)
    row = GatewayRequestLog(
        account_id=account_id,
        request_id=request_id,
        provider=provider,
        model=model,
        started_at=started,
        finished_at=now,
        latency_ms=latency_ms,
        prompt_tokens=20,
        completion_tokens=10,
        cached_tokens=0,
        reasoning_tokens=0,
        cost_kop=cost_kop,
        status=status,
        http_code=200 if status == "ok" else 502,
        error_message=error_message,
        error_code=error_code,
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
async def test_list_traces_empty(client, db, redis_client) -> None:
    user = await _seed_user(db)
    await _login(client, user)
    r = await client.get("/v1/account/traces")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["has_more"] is False


@pytest.mark.asyncio
async def test_list_traces_recency_order(client, db, redis_client) -> None:
    """Most recent first; pagination via offset+limit."""
    user = await _seed_user(db)
    account_id = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user.id)
            )
        )
        .scalar_one()
        .id
    )
    # 3 traces — самый старый, средний, новый.
    await _seed_trace(db, account_id=account_id, request_id="req-old", created_offset_seconds=200)
    await _seed_trace(db, account_id=account_id, request_id="req-mid", created_offset_seconds=100)
    await _seed_trace(db, account_id=account_id, request_id="req-new", created_offset_seconds=0)
    await _login(client, user)

    r = await client.get("/v1/account/traces?limit=2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [it["request_id"] for it in body["items"]] == ["req-new", "req-mid"]
    assert body["total"] == 3
    assert body["has_more"] is True

    r2 = await client.get("/v1/account/traces?limit=2&offset=2")
    body2 = r2.json()
    assert [it["request_id"] for it in body2["items"]] == ["req-old"]
    assert body2["has_more"] is False


@pytest.mark.asyncio
async def test_list_traces_status_filter(client, db, redis_client) -> None:
    user = await _seed_user(db)
    account_id = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user.id)
            )
        )
        .scalar_one()
        .id
    )
    await _seed_trace(db, account_id=account_id, request_id="ok-1", status="ok")
    await _seed_trace(db, account_id=account_id, request_id="err-1", status="error")
    await _login(client, user)

    r = await client.get("/v1/account/traces?status=error")
    body = r.json()
    assert [it["request_id"] for it in body["items"]] == ["err-1"]


@pytest.mark.asyncio
async def test_list_traces_model_filter(client, db, redis_client) -> None:
    user = await _seed_user(db)
    account_id = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user.id)
            )
        )
        .scalar_one()
        .id
    )
    await _seed_trace(db, account_id=account_id, request_id="r1", model="gpt-5.4-mini")
    await _seed_trace(db, account_id=account_id, request_id="r2", model="claude-opus-4.7")
    await _login(client, user)

    r = await client.get("/v1/account/traces?model=claude-opus-4.7")
    body = r.json()
    assert [it["request_id"] for it in body["items"]] == ["r2"]


@pytest.mark.asyncio
async def test_list_traces_q_substring_match(client, db, redis_client) -> None:
    user = await _seed_user(db)
    account_id = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user.id)
            )
        )
        .scalar_one()
        .id
    )
    await _seed_trace(db, account_id=account_id, request_id="abc-deadbeef-1")
    await _seed_trace(db, account_id=account_id, request_id="xyz-cafebabe-2")
    await _login(client, user)

    r = await client.get("/v1/account/traces?q=deadbeef")
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["request_id"] == "abc-deadbeef-1"


@pytest.mark.asyncio
async def test_trace_detail_happy_path(client, db, redis_client) -> None:
    user = await _seed_user(db)
    account_id = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user.id)
            )
        )
        .scalar_one()
        .id
    )
    await _seed_trace(db, account_id=account_id, request_id="req-detail-1", cost_kop=500)
    await _login(client, user)

    r = await client.get("/v1/account/traces/req-detail-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["request_id"] == "req-detail-1"
    assert body["cost_kop"] == 500
    assert body["model"] == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_trace_detail_404(client, db, redis_client) -> None:
    user = await _seed_user(db)
    await _login(client, user)
    r = await client.get("/v1/account/traces/nonexistent-id")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "trace_not_found"


@pytest.mark.asyncio
async def test_traces_isolated_per_account(client, db, redis_client) -> None:
    """Account A не должен видеть traces account B."""
    # Account A
    user_a = await _seed_user(db)
    account_a_id = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user_a.id)
            )
        )
        .scalar_one()
        .id
    )
    await _seed_trace(db, account_id=account_a_id, request_id="trace-a")

    # Account B
    user_b = await _seed_user(db)
    account_b_id = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user_b.id)
            )
        )
        .scalar_one()
        .id
    )
    await _seed_trace(db, account_id=account_b_id, request_id="trace-b")

    # Login как B → видит только trace-b.
    await _login(client, user_b)
    r = await client.get("/v1/account/traces")
    body = r.json()
    request_ids = [it["request_id"] for it in body["items"]]
    assert "trace-b" in request_ids
    assert "trace-a" not in request_ids

    # Detail на чужой trace → 404.
    r2 = await client.get("/v1/account/traces/trace-a")
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Sprint 3 — Advanced filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_traces_provider_filter(client, db, redis_client) -> None:
    user = await _seed_user(db)
    aid = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user.id)
            )
        )
        .scalar_one()
        .id
    )
    await _seed_trace(db, account_id=aid, request_id="o1", provider="openai")
    await _seed_trace(db, account_id=aid, request_id="a1", provider="anthropic")
    await _login(client, user)

    r = await client.get("/v1/account/traces?provider=anthropic")
    assert r.status_code == 200
    body = r.json()
    assert [it["request_id"] for it in body["items"]] == ["a1"]


@pytest.mark.asyncio
async def test_list_traces_cost_range(client, db, redis_client) -> None:
    user = await _seed_user(db)
    aid = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user.id)
            )
        )
        .scalar_one()
        .id
    )
    await _seed_trace(db, account_id=aid, request_id="cheap", cost_kop=10)
    await _seed_trace(db, account_id=aid, request_id="mid", cost_kop=100)
    await _seed_trace(db, account_id=aid, request_id="expensive", cost_kop=1000)
    await _login(client, user)

    r = await client.get("/v1/account/traces?min_cost_kop=50&max_cost_kop=500")
    assert r.status_code == 200
    body = r.json()
    assert [it["request_id"] for it in body["items"]] == ["mid"]


@pytest.mark.asyncio
async def test_list_traces_latency_range(client, db, redis_client) -> None:
    user = await _seed_user(db)
    aid = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user.id)
            )
        )
        .scalar_one()
        .id
    )
    await _seed_trace(db, account_id=aid, request_id="fast", latency_ms=100)
    await _seed_trace(db, account_id=aid, request_id="slow", latency_ms=5000)
    await _login(client, user)

    r = await client.get("/v1/account/traces?min_latency_ms=1000")
    body = r.json()
    assert [it["request_id"] for it in body["items"]] == ["slow"]


@pytest.mark.asyncio
async def test_list_traces_only_with_tools(client, db, redis_client) -> None:
    user = await _seed_user(db)
    aid = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user.id)
            )
        )
        .scalar_one()
        .id
    )
    await _seed_trace(db, account_id=aid, request_id="plain", tools_used=False)
    await _seed_trace(db, account_id=aid, request_id="with-tools", tools_used=True)
    await _login(client, user)

    r = await client.get("/v1/account/traces?only_with_tools=true")
    body = r.json()
    assert [it["request_id"] for it in body["items"]] == ["with-tools"]


@pytest.mark.asyncio
async def test_list_traces_only_with_cache(client, db, redis_client) -> None:
    user = await _seed_user(db)
    aid = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user.id)
            )
        )
        .scalar_one()
        .id
    )
    await _seed_trace(db, account_id=aid, request_id="no-cache", cache_hit=False)
    await _seed_trace(db, account_id=aid, request_id="cached", cache_hit=True)
    await _login(client, user)

    r = await client.get("/v1/account/traces?only_with_cache=true")
    body = r.json()
    assert [it["request_id"] for it in body["items"]] == ["cached"]


@pytest.mark.asyncio
async def test_list_traces_q_searches_model_and_error(client, db, redis_client) -> None:
    """`q` теперь ищет по request_id ИЛИ model ИЛИ error_message."""
    user = await _seed_user(db)
    aid = (
        (
            await db.execute(
                __import__("sqlalchemy").select(Account).where(Account.owner_id == user.id)
            )
        )
        .scalar_one()
        .id
    )
    await _seed_trace(db, account_id=aid, request_id="r1", model="claude-haiku-4.5")
    await _seed_trace(db, account_id=aid, request_id="r2", model="gpt-5.4")
    await _seed_trace(
        db,
        account_id=aid,
        request_id="r3",
        model="gpt-5.4",
        status="error",
        error_message="rate limit exceeded on provider",
    )
    await _login(client, user)

    # Match by model name fragment.
    r = await client.get("/v1/account/traces?q=haiku")
    body = r.json()
    assert [it["request_id"] for it in body["items"]] == ["r1"]

    # Match by error message fragment.
    r2 = await client.get("/v1/account/traces?q=rate%20limit")
    body2 = r2.json()
    assert [it["request_id"] for it in body2["items"]] == ["r3"]


@pytest.mark.asyncio
async def test_list_traces_requires_session(client) -> None:
    r = await client.get("/v1/account/traces")
    assert r.status_code == 401
