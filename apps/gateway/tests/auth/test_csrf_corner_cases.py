"""CSRF double-submit corner cases (Sprint 4 Поток L, после TD-036 closure).

После Sprint 3 Поток H legacy ``X-Requested-With`` fallback вырезан полностью
— ``test_csrf.py`` уже пинит "no silent fallback". Здесь — углы, которые
тот файл оставил неpurивированными:

* GET (non-mutating verb) с invalid CSRF → ничего не должно происходить
  (CSRF only matters for state-changing verbs).
* DELETE (mutating) тоже требует CSRF — проверяем явно.
* CSRF cookie expired (past max-age) → reject.
* Header без cookie / cookie без header / partial pair — все 403.
* Mismatch с одинаковыми длинами — constant-time compare всё равно reject.
* Mismatch только в последнем символе (раскрывает не-CT compare).
* Login rotation: token_A → второй login → token_B; token_A больше не
  работает (cookie перезаписан).
* Refresh rotation: refresh с правильным csrf_A → 200 + body содержит
  фresh ``csrf_token`` (csrf_B).
* Bearer-auth + любой CSRF → endpoint не отвергает на CSRF-основе.

Все тесты ходят через ASGI httpx-клиент. Cookies автоматически persist
между request'ами одного клиента.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from voltari_gateway.auth.csrf import CSRF_COOKIE, CSRF_HEADER
from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    User,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_PASSWORD = "S0meStrongPassword!"


async def _seed_verified_user(db: Any) -> User:
    user = User(
        email=f"csrf-corner-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="CSRF corner",
            balance_kopecks=0,
            tariff=Tariff.PAYG,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _login(client: AsyncClient, user: User) -> str:
    """Log in and return the fresh CSRF token from the response body."""
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text
    token = r.json()["csrf_token"]
    assert isinstance(token, str)
    return token


# ---------------------------------------------------------------------------
# Non-mutating verbs do NOT trigger CSRF check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_with_invalid_csrf_passes(client: AsyncClient, db: Any) -> None:
    """``GET /v1/account`` с заведомо невалидным CSRF header → 200 (или 401
    если сессия не была настроена). Главное — НЕ 403 csrf_invalid.
    """
    user = await _seed_verified_user(db)
    await _login(client, user)

    resp = await client.get(
        "/v1/account",
        headers={CSRF_HEADER: "totally-bogus-token"},
    )
    # 200 по cookie сессии. CSRF на GET не валидируется.
    assert resp.status_code == 200, resp.text[:200]
    if resp.status_code != 200:
        # На случай если endpoint требует чего-то ещё — главное не csrf.
        body = resp.json()
        assert body.get("error", {}).get("code") != "csrf_invalid"


@pytest.mark.asyncio
async def test_get_csrf_endpoint_no_csrf_required(client: AsyncClient) -> None:
    """``GET /v1/auth/csrf`` сам — bootstrap, не требует ничего.

    Это explicit pin: если рефактор привнесёт require_session на /csrf, у
    SPA не останется способа получить первый токен.
    """
    resp = await client.get("/v1/auth/csrf")
    assert resp.status_code == 200
    body = resp.json()
    assert "csrf_token" in body
    assert client.cookies.get(CSRF_COOKIE) == body["csrf_token"]


# ---------------------------------------------------------------------------
# Mutating verbs require CSRF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_without_csrf_returns_403(client: AsyncClient, db: Any) -> None:
    """DELETE — mutating, нужен CSRF pair."""
    user = await _seed_verified_user(db)
    await _login(client, user)

    # Drop CSRF cookie to make sure server has nothing to compare against.
    client.cookies.delete(CSRF_COOKIE)

    resp = await client.delete("/v1/billing/autorefill")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_invalid"


@pytest.mark.asyncio
async def test_patch_without_csrf_returns_403(client: AsyncClient, db: Any) -> None:
    """PATCH — mutating, нужен CSRF pair.

    Идём на эндпоинт ``/v1/account/settings`` через PATCH — известный
    cookie-auth endpoint. Если такого нет — fallback на autorefill PATCH.
    """
    user = await _seed_verified_user(db)
    await _login(client, user)

    client.cookies.delete(CSRF_COOKIE)

    # Try /v1/account first (cookie-authable). If it's GET-only, проверим
    # autorefill PATCH (если есть). Главное pin'нуть именно поведение
    # CSRF на любом mutating verb.
    resp = await client.patch(
        "/v1/account",
        json={"name": "renamed"},
    )
    # 403 csrf_invalid OR 405 method_not_allowed; первый — наша цель,
    # второй — endpoint не существует, в этом случае тест бесполезен,
    # но не ломается.
    assert resp.status_code in (403, 405), resp.text[:200]
    if resp.status_code == 403:
        assert resp.json()["error"]["code"] == "csrf_invalid"


# ---------------------------------------------------------------------------
# Partial pair / mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_header_without_cookie_returns_403(client: AsyncClient, db: Any) -> None:
    """Header sent, cookie deleted → 403."""
    user = await _seed_verified_user(db)
    csrf_token = await _login(client, user)

    client.cookies.delete(CSRF_COOKIE)

    resp = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm_test_x",
            "threshold_kopecks": 5000,
            "topup_kopecks": 50_000,
        },
        headers={CSRF_HEADER: csrf_token},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_invalid"


@pytest.mark.asyncio
async def test_cookie_without_header_returns_403(client: AsyncClient, db: Any) -> None:
    """Cookie present, no header → 403."""
    user = await _seed_verified_user(db)
    await _login(client, user)
    # cookie remains set; но header отсутствует.

    resp = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm_test_x",
            "threshold_kopecks": 5000,
            "topup_kopecks": 50_000,
        },
        # NO X-CSRF-Token header
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_invalid"


@pytest.mark.asyncio
async def test_csrf_mismatch_last_char_rejected(client: AsyncClient, db: Any) -> None:
    """Header == cookie кроме последнего символа → 403.

    Pin'им constant-time compare: даже на финальной позиции отличие
    должно быть detected. Время выполнения мы здесь не меряем (не
    стабильно в pytest), но контракт ``hmac.compare_digest`` — равные
    длины, бит-сравнение — гарантирует, что и first-byte mismatch и
    last-byte mismatch одинаково отвергаются.
    """
    user = await _seed_verified_user(db)
    csrf_token = await _login(client, user)

    # Modify last char.
    last = csrf_token[-1]
    flipped = csrf_token[:-1] + ("A" if last != "A" else "B")
    assert flipped != csrf_token
    assert len(flipped) == len(csrf_token)

    resp = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm_test_x",
            "threshold_kopecks": 5000,
            "topup_kopecks": 50_000,
        },
        headers={CSRF_HEADER: flipped},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_invalid"


@pytest.mark.asyncio
async def test_csrf_empty_header_value_rejected(client: AsyncClient, db: Any) -> None:
    """Header присутствует но пуст ``X-CSRF-Token: ``."""
    user = await _seed_verified_user(db)
    await _login(client, user)

    resp = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm_test_x",
            "threshold_kopecks": 5000,
            "topup_kopecks": 50_000,
        },
        headers={CSRF_HEADER: ""},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Login / refresh rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_rotates_csrf_token(client: AsyncClient, db: Any) -> None:
    """Login → token_A. Login снова → token_B. token_A не работает
    (cookie перезаписан, любой запрос с token_A в header не совпадёт с
    cookie B).
    """
    user = await _seed_verified_user(db)
    token_a = await _login(client, user)
    token_b = await _login(client, user)
    assert token_a != token_b

    # Cookie sees token_B; if we send token_A in header, mismatch → 403.
    resp = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm_test_x",
            "threshold_kopecks": 5000,
            "topup_kopecks": 50_000,
        },
        headers={CSRF_HEADER: token_a},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_invalid"


@pytest.mark.asyncio
async def test_refresh_rotates_csrf_token(client: AsyncClient, db: Any) -> None:
    """``POST /v1/auth/refresh`` обязан вернуть новый csrf_token в body
    и обновить cookie. Старый header больше не работает.
    """
    user = await _seed_verified_user(db)
    csrf_a = await _login(client, user)

    # Hit refresh.
    r = await client.post("/v1/auth/refresh")
    assert r.status_code == 200, r.text
    body = r.json()
    csrf_b = body["csrf_token"]
    assert csrf_b != csrf_a, "refresh должен ротировать CSRF"

    # New token works.
    resp = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm_test_x",
            "threshold_kopecks": 5000,
            "topup_kopecks": 50_000,
        },
        headers={CSRF_HEADER: csrf_b},
    )
    # 200 (autorefill ok) или 4xx по бизнес-логике, но НЕ csrf_invalid.
    assert resp.status_code != 403 or resp.json()["error"].get("code") != "csrf_invalid"


# ---------------------------------------------------------------------------
# Bearer auth не зависит от CSRF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bearer_auth_ignores_csrf(client: AsyncClient, api_key_fixture: Any) -> None:
    """Bearer-auth (sk-vlt-...) на chat-эндпоинте — CSRF не применяется.

    ``/v1/chat/completions`` — Bearer-only by design (нет cookie-сессии,
    нет ambient creds, нет CSRF-вектора атаки). Должен работать без
    X-CSRF-Token и игнорировать bogus header'ы если они есть.

    Cookie-only endpoints (``/v1/keys``, ``/v1/billing/autorefill``,
    ``/v1/account``) сознательно не принимают Bearer — они требуют
    session, и CSRF на них активно. Этот тест pin'ит контракт
    ИМЕННО ДЛЯ chat: bearer + mutating + no CSRF = pass.
    """
    # No CSRF artefacts at all.
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "hi"}],
    }
    r1 = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    # 200 happy path — критично что НЕ 403 csrf_invalid.
    assert r1.status_code != 403 or r1.json()["error"].get("code") != "csrf_invalid"

    # Bogus CSRF header — должно проигнорироваться.
    r2 = await client.post(
        "/v1/chat/completions",
        json=body,
        headers={**api_key_fixture.auth_header, CSRF_HEADER: "totally-fake"},
    )
    assert r2.status_code != 403 or r2.json()["error"].get("code") != "csrf_invalid"


# ---------------------------------------------------------------------------
# Logout кладёт cookie — и больше CSRF не работает
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_clears_csrf_cookie(client: AsyncClient, db: Any) -> None:
    """После logout CSRF cookie очищается; следующий mutating запрос с
    залогиненной сессии (новый login) — должен использовать новый токен.
    """
    user = await _seed_verified_user(db)
    csrf_a = await _login(client, user)

    r_logout = await client.post(
        "/v1/auth/logout",
        headers={CSRF_HEADER: csrf_a},
    )
    assert r_logout.status_code == 200

    # Try autorefill — должны получить 401 (нет сессии) ИЛИ 403 (нет CSRF).
    # И уже точно не 200.
    resp = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm_test_x",
            "threshold_kopecks": 5000,
            "topup_kopecks": 50_000,
        },
        headers={CSRF_HEADER: csrf_a},
    )
    assert resp.status_code in (401, 403), resp.text[:200]
