"""End-to-end tests for /v1/account/admin/provider_balances*.

Coverage:
* GET — без сессии 401, не-admin 403, admin 200 с полным списком (10 строк).
* POST /refresh — без сессии 401, не-admin 403, admin 200 (с пустыми
  адаптерами — все ManualAdapter — refreshed=0).
* POST /{provider}/manual — без сессии 401, не-admin 403, admin 200,
  unknown provider → 400, negative balance → 422 (pydantic).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest

from tests.auth.conftest import csrf_headers as _csrf_headers
from voltari_gateway.auth.password import hash_password
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
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
    settings = get_settings()
    return patch.object(settings, "admin_emails", value)


# ---------------------------------------------------------------------------
# GET /provider_balances
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requires_session(client) -> None:
    r = await client.get("/v1/account/admin/provider_balances")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_non_admin_403(client, db, redis_client) -> None:
    user = await _seed_user(db, email="not-admin@example.com")
    await _login(client, user)
    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.get("/v1/account/admin/provider_balances")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_list_admin_returns_full_provider_list(client, db, redis_client) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.get("/v1/account/admin/provider_balances")
    assert r.status_code == 200, r.text
    body = r.json()
    items = body["items"]
    # 10 known providers
    assert len(items) == 10
    names = {row["provider"] for row in items}
    assert names == {
        "openai",
        "anthropic",
        "google",
        "deepseek",
        "moonshot",
        "minimax",
        "zhipu",
        "together",
        "yandex",
        "sber",
    }
    # Никаких записей в БД ещё не было — все pending
    for row in items:
        assert row["fetch_status"] == "pending"
        assert row["balance_native"] is None


@pytest.mark.asyncio
async def test_list_empty_admin_emails_closes_endpoint(client, db, redis_client) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    with _set_admin_emails(""):
        r = await client.get("/v1/account/admin/provider_balances")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /provider_balances/refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_requires_session(client) -> None:
    r = await client.post("/v1/account/admin/provider_balances/refresh")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_refresh_non_admin_403(client, db, redis_client) -> None:
    user = await _seed_user(db, email="not-admin@example.com")
    await _login(client, user)
    headers = await _csrf_headers(client)
    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.post("/v1/account/admin/provider_balances/refresh", headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_refresh_admin_200_no_credentials(client, db, redis_client) -> None:
    """Без provider keys в env — все адаптеры ManualAdapter, refresh = no-op.

    Test fixture conftest.py не задаёт ключи DEEPSEEK/SBER/MOONSHOT, так
    что refresh_all() пробежит 0 API-adapter'ов и вернёт пустой результат.
    """
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.post("/v1/account/admin/provider_balances/refresh", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["refreshed_count"] == 0
    assert body["error_count"] == 0
    # Items всё равно показывают полный список
    assert len(body["items"]) == 10


# ---------------------------------------------------------------------------
# POST /provider_balances/{provider}/manual
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_requires_session(client) -> None:
    r = await client.post(
        "/v1/account/admin/provider_balances/openai/manual",
        json={"balance_native": "50", "currency": "USD"},
    )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_manual_non_admin_403(client, db, redis_client) -> None:
    user = await _seed_user(db, email="not-admin@example.com")
    await _login(client, user)
    headers = await _csrf_headers(client)
    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.post(
            "/v1/account/admin/provider_balances/openai/manual",
            json={"balance_native": "50", "currency": "USD"},
            headers=headers,
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_manual_admin_records_balance(client, db, redis_client) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.post(
            "/v1/account/admin/provider_balances/openai/manual",
            json={
                "balance_native": "50.25",
                "currency": "USD",
                "notes": "топап 10.05",
            },
            headers=headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "openai"
    assert body["fetch_method"] == "manual"
    assert body["fetch_status"] == "manual"
    assert Decimal(body["balance_native"]) == Decimal("50.25")
    assert body["balance_currency"] == "USD"
    assert body["balance_rub_kopecks"] == 402_000  # 50.25 * 80 * 100
    assert body["notes"] == "топап 10.05"


@pytest.mark.asyncio
async def test_manual_then_list_shows_value(client, db, redis_client) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.post(
            "/v1/account/admin/provider_balances/anthropic/manual",
            json={"balance_native": "100", "currency": "USD"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        r2 = await client.get("/v1/account/admin/provider_balances")
    assert r2.status_code == 200
    by_name = {row["provider"]: row for row in r2.json()["items"]}
    anthropic = by_name["anthropic"]
    assert anthropic["fetch_status"] == "manual"
    assert anthropic["balance_rub_kopecks"] == 800_000  # 100 USD * 80₽ * 100


@pytest.mark.asyncio
async def test_manual_unknown_provider_400(client, db, redis_client) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.post(
            "/v1/account/admin/provider_balances/palm/manual",
            json={"balance_native": "1", "currency": "USD"},
            headers=headers,
        )
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "unknown_provider"


@pytest.mark.asyncio
async def test_manual_negative_balance_rejected(client, db, redis_client) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.post(
            "/v1/account/admin/provider_balances/openai/manual",
            json={"balance_native": "-10", "currency": "USD"},
            headers=headers,
        )
    # Pydantic ge=0 → 400 (gateway exception handler converts 422→400 envelope).
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_manual_invalid_currency_rejected(client, db, redis_client) -> None:
    user = await _seed_user(db, email="ceo@brikko.ru")
    await _login(client, user)
    headers = await _csrf_headers(client)
    with _set_admin_emails("ceo@brikko.ru"):
        r = await client.post(
            "/v1/account/admin/provider_balances/openai/manual",
            json={"balance_native": "10", "currency": "GBP"},
            headers=headers,
        )
    # Pydantic Literal validation → 400 (envelope handler converts 422→400).
    assert r.status_code == 400
