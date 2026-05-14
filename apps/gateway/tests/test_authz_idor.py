"""IDOR (Insecure Direct Object Reference) audit tests.

QA P1-15. Закрывает класс багов, где user_B может прочитать/изменить/удалить
ресурсы user_A через подмену ID в URL/body.

Сценарий:
    user_A (owner) → account_A → key_A
    user_B (owner) → account_B → key_B

Каждый тест дёргает endpoint с key_B (или сессией user_B) и проверяет,
что user_B либо НЕ видит данные user_A, либо получает 403/404.

Принципы (важно для security):
  * Возвращать 404 (не 403) на чужие ресурсы — иначе timing/error-code leak
    позволяет enumerate'ить существующие ID (см. apps/gateway/voltari_gateway/api/keys.py:177).
  * Никогда не возвращать данные другого account'а в ответе на свой запрос
    (например, /v1/billing/balance с key_B обязан вернуть balance_B, не balance_A).
  * Любая mutation на чужой ресурс — отказ. Cookie-сессии user_B не должны
    "подменить" account_id через URL-параметр.

ВАЖНО: если в процессе обнаружится IDOR vulnerability, тест НЕ закрывается
workaround'ом. Он должен остаться failing, баг записывается в
``docs/tech_debt_registry.md`` как новый TD severity P0, и backend должен
прислать реальный fix.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway import config as cfg
from voltari_gateway.auth import rate_limit as rl
from voltari_gateway.auth.keys import generate_api_key
from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    Tariff,
    Transaction,
    TransactionKind,
    UsageEvent,
    User,
)

# ---------------------------------------------------------------------------
# Test-env fixtures: cookies, settings cache, rate limits
# ---------------------------------------------------------------------------
# Mirrors tests/auth/conftest.py — needed because /v1/auth/login sets
# cookies and unless cookie_domain is empty the httpx test client (running
# on http://test) drops them.


@pytest.fixture(autouse=True)
def _idor_test_env(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("COOKIE_DOMAIN", "")
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    monkeypatch.setenv("BASE_URL_FRONTEND", "http://test")
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _idor_reset_rate_limits():
    rl.reload_from_settings()
    yield
    rl.reload_from_settings()


# ---------------------------------------------------------------------------
# Fixtures: two complete users / accounts / keys
# ---------------------------------------------------------------------------

_PASSWORD_A = "user-a-password-very-strong-1234567890"
_PASSWORD_B = "user-b-password-very-strong-1234567890"


def _csrf_headers_for(client) -> dict[str, str]:
    """Build CSRF double-submit headers from the current client cookie jar.

    Post-TD-036 the backend rejects requests that don't carry the
    ``X-CSRF-Token`` header matching the ``vlt_csrf`` cookie. After login
    the cookie is set automatically by httpx, so we read it back and echo
    it as the header.
    """
    csrf = client.cookies.get("vlt_csrf")
    if not csrf:
        return {}
    return {"X-CSRF-Token": csrf}


class _UserSetup:
    """Bundled user/account/api-key triple for IDOR tests."""

    __slots__ = ("account", "api_key", "plaintext", "user")

    def __init__(
        self,
        user: User,
        account: Account,
        api_key: ApiKey,
        plaintext: str,
    ) -> None:
        self.user = user
        self.account = account
        self.api_key = api_key
        self.plaintext = plaintext

    @property
    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.plaintext}"}


async def _seed_user_with_key(
    db: AsyncSession,
    *,
    email_prefix: str,
    password: str,
    balance_kop: int = 100_000,
    tariff: Tariff = Tariff.PRO,
) -> _UserSetup:
    user = User(
        # Note: cannot use *.test TLD — pydantic EmailStr blocks reserved TLDs
        # (RFC 6761). example.com is OK and routes nowhere.
        email=f"{email_prefix}-{uuid.uuid4().hex[:8]}@idor.example.com",
        password_hash=hash_password(password),
        email_verified=True,
    )
    db.add(user)
    await db.flush()

    account = Account(
        owner_id=user.id,
        name=f"{email_prefix} account",
        balance_kopecks=balance_kop,
        tariff=tariff,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
        settings={},
    )
    db.add(account)
    await db.flush()

    generated = generate_api_key()
    api_key = ApiKey(
        account_id=account.id,
        name=f"{email_prefix}-key",
        key_hash=generated.key_hash,
        key_prefix=generated.prefix,
        scope=ApiKeyScope.WRITE,
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(user)
    await db.refresh(account)
    await db.refresh(api_key)

    return _UserSetup(user=user, account=account, api_key=api_key, plaintext=generated.plaintext)


# Late import: we need ApiKeyScope but want clean header in module level.
from voltari_gateway.db.models import ApiKeyScope  # noqa: E402


@pytest.fixture
async def user_a(db: AsyncSession) -> _UserSetup:
    return await _seed_user_with_key(db, email_prefix="alice", password=_PASSWORD_A)


@pytest.fixture
async def user_b(db: AsyncSession) -> _UserSetup:
    return await _seed_user_with_key(db, email_prefix="bob", password=_PASSWORD_B)


async def _login_as(client, email: str, password: str) -> None:
    """Login a session for the given user — cookies are stored on `client`."""
    r = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login failed: {r.text}"
    # httpx.AsyncClient persists cookies via its `cookies` jar by default,
    # but when the test fixture uses ASGITransport with base_url="http://test",
    # cookies need explicit propagation. Verify they arrived.
    assert r.cookies.get("vlt_access"), f"login did not set vlt_access: {dict(r.cookies)}"


# ---------------------------------------------------------------------------
# IDOR: API keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_b_cannot_revoke_user_a_key(client, redis_client, user_a, user_b):
    """user_B логинится через cookie и пытается DELETE /v1/keys/{key_A_id} →
    404 (не 403, чтобы не leak'ать существование).
    """
    await _login_as(client, user_b.user.email, _PASSWORD_B)

    r = await client.delete(
        f"/v1/keys/{user_a.api_key.id}",
        headers=_csrf_headers_for(client),
    )

    # 404 is the secure response — leaks no information about whether the
    # key exists. Reference: apps/gateway/voltari_gateway/api/keys.py:182
    assert r.status_code == 404, f"expected 404 (anti-enumeration), got {r.status_code}: {r.text}"

    # Sanity: key_A на самом деле всё ещё ACTIVE в БД.
    from voltari_gateway.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as s:
        ak = await s.get(ApiKey, user_a.api_key.id)
        assert ak is not None
        assert ak.status == ApiKeyStatus.ACTIVE, "key_A should still be active"


@pytest.mark.asyncio
async def test_user_b_cannot_view_user_a_keys(client, redis_client, user_a, user_b):
    """GET /v1/keys у user_B возвращает только key_B, не key_A."""
    await _login_as(client, user_b.user.email, _PASSWORD_B)

    r = await client.get("/v1/keys")
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list)

    ids = {item["id"] for item in data}
    assert str(user_b.api_key.id) in ids, "user_B должен видеть свой ключ"
    assert str(user_a.api_key.id) not in ids, "IDOR: user_B видит key_A в листинге!"


@pytest.mark.asyncio
async def test_user_b_cannot_rename_user_a_key(client, redis_client, user_a, user_b):
    """PATCH /v1/keys/{key_A_id} с сессией user_B → 404."""
    await _login_as(client, user_b.user.email, _PASSWORD_B)

    r = await client.patch(
        f"/v1/keys/{user_a.api_key.id}",
        json={"name": "hacked"},
        headers=_csrf_headers_for(client),
    )
    assert r.status_code == 404, f"expected 404, got {r.status_code}"

    # Sanity: имя не поменялось.
    from voltari_gateway.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as s:
        ak = await s.get(ApiKey, user_a.api_key.id)
        assert ak is not None
        assert ak.name != "hacked"


# ---------------------------------------------------------------------------
# IDOR: Billing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_b_bearer_balance_returns_only_b_balance(client, redis_client, user_a, user_b):
    """GET /v1/billing/balance с key_B возвращает balance_B, не balance_A.

    Мы предварительно ставим разные balance'ы на A/B чтобы наблюдать
    leak, если бы он был.
    """
    # account_A has 100_000 kop (default), account_B has 100_000 kop too.
    # Сделаем разные значения, чтобы любой случайный leak дал отличаемый
    # number в response.
    from voltari_gateway.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as s:
        acc_a = await s.get(Account, user_a.account.id)
        acc_b = await s.get(Account, user_b.account.id)
        assert acc_a is not None and acc_b is not None
        acc_a.balance_kopecks = 999_777
        acc_b.balance_kopecks = 100_500
        await s.commit()

    r = await client.get("/v1/billing/balance", headers=user_b.auth_header)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["account_id"] == str(user_b.account.id), (
        f"IDOR: balance.account_id != user_B's account_id! got {data['account_id']}"
    )
    assert data["balance_kopecks"] == 100_500, (
        f"IDOR: получен balance_A через ключ user_B! got {data['balance_kopecks']}"
    )


@pytest.mark.asyncio
async def test_user_b_cannot_see_user_a_transactions(client, redis_client, user_a, user_b):
    """GET /v1/billing/transactions с key_B → видит только B's tx."""
    from voltari_gateway.db.session import get_session_factory

    factory = get_session_factory()
    # Сидим транзакцию для каждого account.
    async with factory() as s:
        s.add(
            Transaction(
                account_id=user_a.account.id,
                type=TransactionKind.TOPUP,
                amount_kopecks=50_000,
                ref_id="tx-A-secret",
                meta={"holder": "alice"},
            )
        )
        s.add(
            Transaction(
                account_id=user_b.account.id,
                type=TransactionKind.TOPUP,
                amount_kopecks=10_000,
                ref_id="tx-B-public",
                meta={"holder": "bob"},
            )
        )
        await s.commit()

    r = await client.get("/v1/billing/transactions", headers=user_b.auth_header)
    assert r.status_code == 200, r.text
    items = r.json().get("items", [])
    ref_ids = {item["ref_id"] for item in items}
    assert "tx-B-public" in ref_ids
    assert "tx-A-secret" not in ref_ids, "IDOR: транзакция user_A видна через ключ user_B!"


@pytest.mark.asyncio
async def test_user_b_cannot_view_user_a_usage(client, redis_client, user_a, user_b):
    """GET /v1/usage с key_B → видит только B's usage_events."""
    from voltari_gateway.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as s:
        # User A — 1000 input tokens (секрет, не должны видеть в B).
        s.add(
            UsageEvent(
                account_id=user_a.account.id,
                api_key_id=user_a.api_key.id,
                model="gpt-5.4-mini",
                provider="openai",
                input_tokens=1000,
                output_tokens=500,
                cached_tokens=0,
                cost_kopecks=42,
                request_id="req-alice-secret",
            )
        )
        # User B — 200 input tokens.
        s.add(
            UsageEvent(
                account_id=user_b.account.id,
                api_key_id=user_b.api_key.id,
                model="gpt-5.4-mini",
                provider="openai",
                input_tokens=200,
                output_tokens=100,
                cached_tokens=0,
                cost_kopecks=7,
                request_id="req-bob-public",
            )
        )
        await s.commit()

    r = await client.get("/v1/usage", headers=user_b.auth_header)
    assert r.status_code == 200, r.text
    data = r.json()
    totals = data["totals"]
    # B has 200 input + 100 output = 7 kop. A has 42 kop. If we see >7 — leak.
    assert totals["request_count"] == 1, (
        f"IDOR: user_B видит {totals['request_count']} requests, ожидаем 1"
    )
    assert totals["tokens_in"] == 200, (
        f"IDOR: tokens_in={totals['tokens_in']}, ожидаем 200 (только B's)"
    )
    assert totals["cost_kop"] == 7, f"IDOR: cost_kop={totals['cost_kop']}, ожидаем 7 (только B's)"


@pytest.mark.asyncio
async def test_user_b_cannot_topup_account_a_via_metadata(
    client, app, redis_client, user_a, user_b
):
    """POST /v1/billing/topup даже если payload пытается обратиться к account_A
    через user_B's bearer key — backend ОБЯЗАН использовать principal.account_id
    (account_B), не payload.metadata.account_id.

    Контракт `apps/gateway/voltari_gateway/api/billing.py:260` — backend
    хардкодит ``metadata={"account_id": str(principal.account_id)}``, что
    закрывает атаку. Тест проверяет инвариант: при ЛЮБОМ payload'е, который
    можно отправить, backend не должен touch чужой account.
    """
    from voltari_gateway.billing.yookassa import PaymentURL

    captured: dict[str, str] = {}

    class _StubYooKassa:
        # Backend читает client.config.webhook_secret в webhook handler
        # (ему здесь не нужен — мы только /topup). Минимальный shim.
        class _Cfg:
            webhook_secret = "stub"

        config = _Cfg()

        async def create_payment(
            self,
            *,
            account_id: uuid.UUID,
            amount_kopecks: int,
            description: str,
            return_url: str | None = None,
            save_payment_method: bool = False,
            receipt_email: str | None = None,
            receipt_phone: str | None = None,
            metadata: dict[str, Any] | None = None,
            # Added 2026-05-08 alongside SBP/T-Pay support. Stub doesn't
            # care about the value — IDOR test only checks account_id is
            # principal's, not victim's.
            payment_method: str | None = None,
        ) -> PaymentURL:
            captured["account_id_arg"] = str(account_id)
            captured["meta_account_id"] = (metadata or {}).get("account_id", "<missing>")
            return PaymentURL(
                payment_id="stub-payment-123",
                confirmation_url="https://yookassa.example/stub",
                amount_kopecks=amount_kopecks,
                status="pending",
            )

    # Подменяем yookassa client на app.state — get_yookassa() читает оттуда.
    app.state.yookassa = _StubYooKassa()

    # User_B (PRO tariff) делает /topup. Минимальный topup для PRO = 2000₽.
    r = await client.post(
        "/v1/billing/topup",
        json={"amount_rub": 2000},
        headers=user_b.auth_header,
    )
    assert r.status_code == 200, r.text

    # Backend должен был использовать account_B, не account_A.
    assert captured.get("account_id_arg") == str(user_b.account.id), (
        f"IDOR: topup идёт на чужой account! "
        f"got {captured.get('account_id_arg')}, expected {user_b.account.id}"
    )
    assert captured.get("meta_account_id") == str(user_b.account.id), (
        "IDOR: metadata.account_id не привязан к principal'у"
    )


# ---------------------------------------------------------------------------
# IDOR: Receipts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_b_cannot_view_user_a_receipt(client, redis_client, user_a, user_b):
    """GET /v1/billing/receipts/{tx_A_id} с key_B → 404.

    Receipt относится к транзакции, которая принадлежит account_A. user_B
    не должен видеть его даже зная UUID.
    """
    from voltari_gateway.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as s:
        tx = Transaction(
            account_id=user_a.account.id,
            type=TransactionKind.TOPUP,
            amount_kopecks=50_000,
            ref_id="topup-alice-secret",
            meta={
                "receipt": {
                    "id": "rcpt-A-secret",
                    "url": "https://yookassa.example/receipts/A",
                    "issuer": "yookassa",
                }
            },
        )
        s.add(tx)
        await s.commit()
        await s.refresh(tx)
        tx_id = tx.id

    r = await client.get(f"/v1/billing/receipts/{tx_id}", headers=user_b.auth_header)
    assert r.status_code == 404, (
        f"IDOR: receipt user_A виден через ключ user_B! status={r.status_code}, body={r.text}"
    )


# ---------------------------------------------------------------------------
# IDOR: cookie session с подменой URL parametra
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_id_in_cookie_vs_url_param_no_substitution(
    client, redis_client, user_a, user_b
):
    """user_B логинится cookie-сессией; пытается достать /v1/account
    (no URL param) — получает свои данные, не A's.

    /v1/account endpoint берёт account_id из session.account_id, а не
    из URL/body — это защита от IDOR. Тест фиксирует контракт.
    """
    await _login_as(client, user_b.user.email, _PASSWORD_B)
    r = await client.get("/v1/account")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("account_id") == str(user_b.account.id), (
        f"session возвращает чужой account! got {data.get('account_id')}"
    )


# ---------------------------------------------------------------------------
# IDOR: revoked key всё ещё ловится 401 (cache cleared)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoked_key_b_cannot_access_b_endpoints(client, redis_client, user_b):
    """Если key_B revoked, его носитель не может пройти через bearer-flow.

    Не строго IDOR, но смежная инвариант — "revoked = no access".
    """
    from voltari_gateway.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as s:
        ak = await s.get(ApiKey, user_b.api_key.id)
        assert ak is not None
        ak.status = ApiKeyStatus.REVOKED
        await s.commit()

    r = await client.get("/v1/billing/balance", headers=user_b.auth_header)
    assert r.status_code == 401, f"revoked key должен быть 401, got {r.status_code}"
