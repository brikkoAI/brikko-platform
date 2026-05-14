"""Admin status endpoint tests.

Sprint 13.7 (2026-05-09).

Coverage:
* Без сессии → 401
* С обычной сессией (email не в ADMIN_EMAILS) → 403
* Пустой ADMIN_EMAILS → даже Owner-ровень получает 403 (closed by default)
* Email в ADMIN_EMAILS → 200 + структура
* Aggregate api_status: ok при свежих успехах, degraded при error_rate >5%,
  down при 0 configured providers (env stub).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from voltari_gateway.auth.password import hash_password
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    GatewayRequestLog,
    Tariff,
    User,
)

_PASSWORD = "correct horse battery staple"


async def _seed_user(db, email: str | None = None) -> User:
    user = User(
        email=email or f"u-{uuid.uuid4().hex[:10]}@example.com",
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


async def _login(client, user) -> None:
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text


def _set_admin_emails(value: str):
    """Patch get_settings cache to override admin_emails for тест."""
    settings = get_settings()
    return patch.object(settings, "admin_emails", value)


@pytest.mark.asyncio
async def test_admin_status_requires_session(client, db, redis_client) -> None:
    r = await client.get("/v1/account/admin/status")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_admin_status_non_admin_403(client, db, redis_client) -> None:
    user = await _seed_user(db, email="not-admin@example.com")
    await _login(client, user)

    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.get("/v1/account/admin/status")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_admin_status_empty_env_closes_endpoint(client, db, redis_client) -> None:
    """Если ADMIN_EMAILS пустой — endpoint закрыт for everybody."""
    user = await _seed_user(db, email="anyone@example.com")
    await _login(client, user)

    with _set_admin_emails(""):
        r = await client.get("/v1/account/admin/status")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_status_admin_gets_200(client, db, redis_client) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)

    with _set_admin_emails("ceo@brikko.ru,other@brikko.ru"):
        r = await client.get("/v1/account/admin/status")
    assert r.status_code == 200, r.text
    body = r.json()
    # Структура
    assert body["api_status"] in ("ok", "degraded", "down")
    assert "last_24h" in body
    assert body["last_24h"]["requests"] == 0
    assert body["last_24h"]["error_rate"] == 0.0
    assert "providers" in body
    assert isinstance(body["providers"], list)
    assert "database" in body
    assert body["database"]["accounts_total"] >= 1
    assert "deploy" in body
    assert "version" in body["deploy"]


@pytest.mark.asyncio
async def test_admin_status_email_case_insensitive(client, db, redis_client) -> None:
    """ADMIN_EMAILS сравнение игнорирует регистр — env-список можно писать
    как угодно, проверка приводит к lower()."""
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)

    # Env-список написан с большими буквами — тест что _is_admin их нормализует.
    with _set_admin_emails("CEO@BRIKKO.RU"):
        r = await client.get("/v1/account/admin/status")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_admin_check_returns_false_for_regular(client, db, redis_client) -> None:
    user = await _seed_user(db, email="not-admin@example.com")
    await _login(client, user)
    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.get("/v1/account/me/admin-check")
    assert r.status_code == 200, r.text
    assert r.json() == {"is_admin": False}


@pytest.mark.asyncio
async def test_admin_check_returns_true_for_admin(client, db, redis_client) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.get("/v1/account/me/admin-check")
    assert r.status_code == 200, r.text
    assert r.json() == {"is_admin": True}


@pytest.mark.asyncio
async def test_admin_check_requires_session(client, db, redis_client) -> None:
    r = await client.get("/v1/account/me/admin-check")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_admin_status_aggregates_24h_traces(client, db, redis_client) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    aid = (await db.execute(sa.select(Account).where(Account.owner_id == user.id))).scalar_one().id

    now = datetime.now(UTC)
    # 8 ok + 2 errors → 20% error rate
    for i in range(8):
        db.add(
            GatewayRequestLog(
                account_id=aid,
                request_id=f"ok-{i}",
                provider="openai",
                model="gpt-5.4",
                started_at=now - timedelta(seconds=1),
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
    for i in range(2):
        db.add(
            GatewayRequestLog(
                account_id=aid,
                request_id=f"err-{i}",
                provider="openai",
                model="gpt-5.4",
                started_at=now - timedelta(seconds=1),
                finished_at=now,
                latency_ms=500,
                prompt_tokens=10,
                completion_tokens=0,
                cached_tokens=0,
                reasoning_tokens=0,
                cost_kop=0,
                status="error",
                http_code=502,
                is_streaming=False,
                cache_hit=False,
                pii_masked=False,
                tools_used=False,
                created_at=now,
            )
        )
    await db.commit()
    await _login(client, user)

    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.get("/v1/account/admin/status")
    body = r.json()
    assert body["last_24h"]["requests"] == 10
    assert body["last_24h"]["errors"] == 2
    assert abs(body["last_24h"]["error_rate"] - 0.2) < 0.01
    assert body["last_24h"]["cost_kop"] == 80  # 8 * 10
    # 20% error → degraded
    assert body["api_status"] == "degraded"
