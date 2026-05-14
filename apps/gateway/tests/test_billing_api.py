"""HTTP-level tests for ``/v1/billing/*`` endpoints.

Covers scenarios 17 (webhook idempotency), 18 (delayed webhook), 19 (refund),
20 (welcome dedupe — partial), 23 (zero balance hard-stop), 24 (seats —
out-of-scope here, see admin tests), 25-29 (welcome / WELCOME30 — partial,
the welcome creation logic lives in signup, not billing), 30 (PAYG receipt —
mocked happy path), 35 (min topup validation).

Also covers the dual-auth flow added 29.04.2026 (frontend hotfix): every
client-facing /v1/billing/* endpoint now accepts either a Bearer token
(M2M/SDK) or a cookie session (browser dashboard).

We use ``respx`` to intercept the outbound httpx calls to ЮKassa so no real
network is touched. The webhook signature is computed locally for the test.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
import respx
from sqlalchemy import select

from voltari_gateway.auth.password import hash_password
from voltari_gateway.billing.yookassa import (
    YooKassaClient,
    YooKassaConfig,
    verify_webhook_signature,
)
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    Transaction,
    TransactionKind,
    User,
)

# ---------- helpers ------------------------------------------------------------


def _yookassa_config() -> YooKassaConfig:
    return YooKassaConfig(
        shop_id="shop-test",
        secret_key="secret-test",
        webhook_secret="hook-secret",
        return_url_template="https://test/return?account={account_id}",
        base_url="https://test-yookassa/v3",
    )


def _hmac_header(raw: bytes, secret: str = "hook-secret") -> str:
    digest = hmac.new(secret.encode(), raw, hashlib.sha1).hexdigest()
    return f"sha1={digest}"


@pytest.fixture
async def app_with_yookassa(app):
    """Attach a real YooKassaClient (with respx-mocked transport) to the app."""
    cfg = _yookassa_config()
    app.state.yookassa = YooKassaClient(cfg)
    yield app
    await app.state.yookassa.aclose()


# ---------- /v1/billing/balance -----------------------------------------------


@pytest.mark.asyncio
async def test_balance_endpoint(client, api_key_fixture):
    r = await client.get("/v1/billing/balance", headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["balance_kopecks"] == api_key_fixture.account.balance_kopecks
    assert body["tariff"] == api_key_fixture.account.tariff.value


# ---------- /v1/billing/topup --------------------------------------------------


@pytest.mark.asyncio
async def test_topup_creates_payment_and_returns_url(app_with_yookassa, client, api_key_fixture):
    """Happy path — POST /topup proxies to ЮKassa and returns confirmation_url."""
    yk_payload = {
        "id": "pay_abc123",
        "status": "pending",
        "amount": {"value": "2000.00", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "confirmation_url": "https://yk/confirm?id=pay_abc123",
        },
    }
    with respx.mock(base_url="https://test-yookassa/v3") as mock:
        mock.post("/payments").respond(200, json=yk_payload)
        r = await client.post(
            "/v1/billing/topup",
            json={"amount_rub": 2000},
            headers=api_key_fixture.auth_header,
        )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["payment_id"] == "pay_abc123"
    assert data["confirmation_url"].startswith("https://yk/confirm")
    assert data["amount_kopecks"] == 2000 * 100


@pytest.mark.asyncio
async def test_topup_rejects_below_minimum(app_with_yookassa, client, api_key_fixture):
    """Sc. 35: Pro min topup = 2000 ₽; 1500 ₽ rejected before hitting ЮKassa."""
    r = await client.post(
        "/v1/billing/topup",
        json={"amount_rub": 1500},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "below_min_topup"


@pytest.mark.asyncio
@pytest.mark.parametrize("payment_method", ["bank_card", "sbp", "tinkoff_bank", "sberbank"])
async def test_topup_passes_payment_method_to_yookassa(
    app_with_yookassa, client, api_key_fixture, payment_method
):
    """When the caller pre-selects a method, we pass it to ЮKassa so the
    user lands directly on that flow (SBP QR / T-Pay deeplink / card form)
    instead of ЮKassa's own picker."""
    yk_payload = {
        "id": "pay_method_test",
        "status": "pending",
        "amount": {"value": "2000.00", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "confirmation_url": "https://yk/confirm?id=pay_method_test",
        },
    }
    with respx.mock(base_url="https://test-yookassa/v3") as mock:
        route = mock.post("/payments").respond(200, json=yk_payload)
        r = await client.post(
            "/v1/billing/topup",
            json={"amount_rub": 2000, "payment_method": payment_method},
            headers=api_key_fixture.auth_header,
        )
    assert r.status_code == 200, r.text
    # Verify we sent payment_method_data to ЮKassa with the chosen type
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["payment_method_data"]["type"] == payment_method


@pytest.mark.asyncio
async def test_topup_rejects_unknown_payment_method(app_with_yookassa, client, api_key_fixture):
    """Unknown method names — pydantic enum rejection at request boundary,
    no ЮKassa call. Brikko's exception handler maps 422 → 400."""
    r = await client.post(
        "/v1/billing/topup",
        json={"amount_rub": 2000, "payment_method": "bitcoin"},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_topup_default_no_payment_method_lets_yookassa_pick(
    app_with_yookassa, client, api_key_fixture
):
    """Backwards compat — omit payment_method entirely, ЮKassa shows its
    own picker. The body we send must NOT contain payment_method_data."""
    yk_payload = {
        "id": "pay_default",
        "status": "pending",
        "amount": {"value": "2000.00", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "confirmation_url": "https://yk/x",
        },
    }
    with respx.mock(base_url="https://test-yookassa/v3") as mock:
        route = mock.post("/payments").respond(200, json=yk_payload)
        r = await client.post(
            "/v1/billing/topup",
            json={"amount_rub": 2000},
            headers=api_key_fixture.auth_header,
        )
    assert r.status_code == 200
    sent_body = json.loads(route.calls[0].request.content)
    assert "payment_method_data" not in sent_body


@pytest.mark.asyncio
async def test_topup_payg_requires_receipt_contact(app_with_yookassa, client, api_key_fixture, db):
    """PAYG must supply email/phone so самозанятый-чек can be issued."""
    api_key_fixture.account.tariff = Tariff.PAYG
    db.add(api_key_fixture.account)
    await db.commit()

    r = await client.post(
        "/v1/billing/topup",
        json={"amount_rub": 500},
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "receipt_contact_required"


# ---------- /v1/billing/yookassa/webhook --------------------------------------


def _make_webhook_body(account_id: uuid.UUID, payment_id: str, amount_rub: float = 2000.0):
    body = {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
            "metadata": {"account_id": str(account_id)},
            "payment_method": {"id": "pmid-1", "type": "bank_card", "saved": True},
        },
    }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


@pytest.mark.asyncio
async def test_webhook_credits_balance_once(app_with_yookassa, client, api_key_fixture, db):
    """Sc. 17: webhook arrives twice → balance increases once."""
    raw = _make_webhook_body(api_key_fixture.account.id, "pay_xyz")
    headers = {"Content-Type": "application/json", "Content-HMAC": _hmac_header(raw)}

    start_balance = api_key_fixture.account.balance_kopecks

    r1 = await client.post("/v1/billing/yookassa/webhook", content=raw, headers=headers)
    r2 = await client.post("/v1/billing/yookassa/webhook", content=raw, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200

    await db.refresh(api_key_fixture.account)
    assert api_key_fixture.account.balance_kopecks == start_balance + 200_000  # +2000 ₽

    rows = (await db.execute(select(Transaction))).scalars().all()
    topups = [t for t in rows if t.type == TransactionKind.TOPUP]
    assert len(topups) == 1


@pytest.mark.asyncio
async def test_webhook_bad_signature_rejected(app_with_yookassa, client, api_key_fixture):
    raw = _make_webhook_body(api_key_fixture.account.id, "pay_evil")
    bad_headers = {
        "Content-Type": "application/json",
        "Content-HMAC": "sha1=deadbeef",
    }
    r = await client.post("/v1/billing/yookassa/webhook", content=raw, headers=bad_headers)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_webhook_refund_event_decrements_balance(
    app_with_yookassa, client, api_key_fixture, db
):
    """Sc. 19: refund webhook reduces balance (allowed to overdraft within floor)."""
    api_key_fixture.account.balance_kopecks = 1_000  # 10 ₽
    db.add(api_key_fixture.account)
    await db.commit()

    raw_body = json.dumps(
        {
            "type": "notification",
            "event": "refund.succeeded",
            "object": {
                "id": "ref_001",
                "status": "succeeded",
                "amount": {"value": "5.00", "currency": "RUB"},
                "metadata": {"account_id": str(api_key_fixture.account.id)},
            },
        },
        separators=(",", ":"),
    ).encode()
    headers = {"Content-Type": "application/json", "Content-HMAC": _hmac_header(raw_body)}

    r = await client.post("/v1/billing/yookassa/webhook", content=raw_body, headers=headers)
    assert r.status_code == 200
    await db.refresh(api_key_fixture.account)
    assert api_key_fixture.account.balance_kopecks == 500  # 10 ₽ - 5 ₽


# ---------- autorefill enable/disable -----------------------------------------


@pytest.mark.asyncio
async def test_autorefill_lifecycle(app_with_yookassa, client, api_key_fixture, db):
    r = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pmid-saved-1",
            "threshold_kopecks": 100_00,
            "topup_kopecks": 500_00,
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200, r.text
    await db.refresh(api_key_fixture.account)
    assert api_key_fixture.account.autorefill_enabled is True
    assert api_key_fixture.account.autorefill_pm_id == "pmid-saved-1"

    r = await client.delete("/v1/billing/autorefill", headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    await db.refresh(api_key_fixture.account)
    assert api_key_fixture.account.autorefill_enabled is False
    assert api_key_fixture.account.autorefill_pm_id is None


@pytest.mark.asyncio
async def test_autorefill_threshold_must_be_below_topup(app_with_yookassa, client, api_key_fixture):
    r = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pmid-saved-1",  # passes pydantic min_length=8
            "threshold_kopecks": 1_000_00,  # threshold > topup → custom 400
            "topup_kopecks": 500_00,
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "autorefill_threshold_invalid"


# ---------- /v1/billing/transactions ------------------------------------------


@pytest.mark.asyncio
async def test_transactions_history(client, api_key_fixture, db):
    db.add_all(
        [
            Transaction(
                account_id=api_key_fixture.account.id,
                type=TransactionKind.TOPUP,
                amount_kopecks=200_000,
                ref_id="seed-topup",
            ),
            Transaction(
                account_id=api_key_fixture.account.id,
                type=TransactionKind.CHARGE,
                amount_kopecks=-3_500,
                ref_id="seed-charge",
            ),
        ]
    )
    await db.commit()

    r = await client.get("/v1/billing/transactions", headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert {i["ref_id"] for i in items} == {"seed-topup", "seed-charge"}


# ---------- /v1/billing/receipts/{id} -----------------------------------------


@pytest.mark.asyncio
async def test_receipt_returns_metadata_when_present(client, api_key_fixture, db):
    tx = Transaction(
        account_id=api_key_fixture.account.id,
        type=TransactionKind.TOPUP,
        amount_kopecks=50_000,
        ref_id="pay_with_receipt",
        meta={"receipt": {"id": "rc_1", "url": "https://yk/r/1", "issuer": "yookassa"}},
    )
    db.add(tx)
    await db.commit()

    r = await client.get(f"/v1/billing/receipts/{tx.id}", headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["receipt_id"] == "rc_1"
    assert body["issuer"] == "yookassa"


@pytest.mark.asyncio
async def test_receipt_missing_returns_404(client, api_key_fixture, db):
    tx = Transaction(
        account_id=api_key_fixture.account.id,
        type=TransactionKind.TOPUP,
        amount_kopecks=50_000,
        ref_id="pay_no_receipt",
    )
    db.add(tx)
    await db.commit()
    r = await client.get(f"/v1/billing/receipts/{tx.id}", headers=api_key_fixture.auth_header)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "receipt_pending"


# ---------- HMAC unit -----------------------------------------------------------


def test_verify_webhook_signature_sha1():
    body = b'{"event":"x"}'
    secret = "k1"
    sig = "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
    assert verify_webhook_signature(body, sig, secret) is True
    assert verify_webhook_signature(body, sig, "wrong") is False
    assert verify_webhook_signature(body, "sha1=00", secret) is False
    assert verify_webhook_signature(body, None, secret) is False


# =============================================================================
# Dual auth (Bearer OR cookie session) — frontend dashboard hotfix 29.04.2026.
#
# The /v1/billing/* endpoints must work for two callers:
#   1) M2M / SDK   — Authorization: Bearer sk-vlt-...
#   2) Browser SPA — Cookie: vlt_access=...; X-CSRF-Token: <vlt_csrf cookie>
#
# CSRF (double-submit token) is enforced ONLY for the cookie path on
# mutating verbs. Bearer flow is by definition not CSRF-able.
#
# TD-036 (Sprint 3 Поток H) — legacy ``X-Requested-With`` fallback removed.
# =============================================================================


_DASHBOARD_PASSWORD = "correct horse battery staple"


async def _csrf(client) -> dict[str, str]:
    """Mint a CSRF token for the cookie-auth path. Stores cookie on client."""
    r = await client.get("/v1/auth/csrf")
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


@pytest.fixture(autouse=False)
def _dashboard_env(monkeypatch):
    """Make cookie auth work over httpx ASGITransport (http://test).

    Without this the cookies set by /login carry Domain=.brikko.ru and
    Secure=true, so the test client never echoes them back. Mirrors the
    autouse fixture in tests/auth/conftest.py — we don't import that
    conftest because it also wires rate-limiter resets we don't need here.
    """
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("COOKIE_DOMAIN", "")
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    monkeypatch.setenv("BASE_URL_FRONTEND", "http://test")
    from voltari_gateway import config as cfg

    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


async def _dashboard_seed(db, *, balance_kop: int, tariff: Tariff = Tariff.PRO) -> User:
    """Create a User+Account that can /login. Returns the User."""
    user = User(
        email=f"dash-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_DASHBOARD_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="Dash Acct",
            balance_kopecks=balance_kop,
            tariff=tariff,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
            settings={},
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _dashboard_login(client, user: User) -> None:
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _DASHBOARD_PASSWORD},
    )
    assert r.status_code == 200, r.text


# ---- 1. balance via Bearer (regression — existing flow still green) ----------


@pytest.mark.asyncio
async def test_balance_via_bearer_token(client, api_key_fixture):
    """Pre-existing M2M path: Bearer header still resolves the principal."""
    r = await client.get(
        "/v1/billing/balance",
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    assert r.json()["balance_kopecks"] == api_key_fixture.account.balance_kopecks


# ---- 2. balance via cookie session (the actual hotfix) -----------------------


@pytest.mark.asyncio
async def test_balance_via_cookie_session(_dashboard_env, client, db, redis_client):
    """Frontend dashboard scenario: SPA hits /v1/billing/balance with cookies.

    Before the dual-auth change this returned 401 because the endpoint
    only accepted Bearer. Now it must succeed and reflect the same balance.
    """
    user = await _dashboard_seed(db, balance_kop=42_000)
    await _dashboard_login(client, user)

    r = await client.get("/v1/billing/balance")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["balance_kopecks"] == 42_000
    assert body["balance_rub"] == 420.0


# ---- 3. transactions via cookie session --------------------------------------


@pytest.mark.asyncio
async def test_transactions_via_cookie_session(_dashboard_env, client, db, redis_client):
    """GET /v1/billing/transactions also works through cookie auth."""
    user = await _dashboard_seed(db, balance_kop=10_000)
    await _dashboard_login(client, user)

    # Seed a transaction on the user's account.
    account = (await db.execute(select(Account).where(Account.owner_id == user.id))).scalar_one()
    db.add(
        Transaction(
            account_id=account.id,
            type=TransactionKind.TOPUP,
            amount_kopecks=10_000,
            ref_id="cookie-seeded-topup",
        )
    )
    await db.commit()

    r = await client.get("/v1/billing/transactions")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["ref_id"] == "cookie-seeded-topup"


# ---- 4. POST /topup via session WITH CSRF — passes ---------------------------


@pytest.mark.asyncio
async def test_topup_via_session_with_csrf_passes(
    _dashboard_env, app_with_yookassa, client, db, redis_client
):
    """Cookie + correct X-Requested-With → 200 (proxies through ЮKassa stub)."""
    user = await _dashboard_seed(db, balance_kop=0, tariff=Tariff.PRO)
    await _dashboard_login(client, user)

    yk_payload = {
        "id": "pay_session_ok",
        "status": "pending",
        "amount": {"value": "2000.00", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "confirmation_url": "https://yk/confirm?id=pay_session_ok",
        },
    }
    with respx.mock(base_url="https://test-yookassa/v3") as mock:
        mock.post("/payments").respond(200, json=yk_payload)
        r = await client.post(
            "/v1/billing/topup",
            json={"amount_rub": 2000},
            headers=await _csrf(client),
        )
    assert r.status_code == 200, r.text
    assert r.json()["payment_id"] == "pay_session_ok"


# ---- 5. POST /topup via session WITHOUT CSRF — 403 ---------------------------


@pytest.mark.asyncio
async def test_topup_via_session_without_csrf_returns_403(
    _dashboard_env, app_with_yookassa, client, db, redis_client
):
    """A logged-in browser missing X-Requested-With must be rejected as CSRF.

    This is the whole reason we accept the header at all — without it a
    third-party site could trigger top-ups via a stale session cookie.
    """
    user = await _dashboard_seed(db, balance_kop=0, tariff=Tariff.PRO)
    await _dashboard_login(client, user)

    r = await client.post(
        "/v1/billing/topup",
        json={"amount_rub": 2000},
        # NO X-Requested-With header — must 403.
    )
    assert r.status_code == 403, r.text
    # Sprint 2 (BE Поток D): code unified to ``csrf_invalid`` alongside
    # the new double-submit pattern. Both legacy-only and new-only code
    # paths emit the same error code now.
    assert r.json()["error"]["code"] == "csrf_invalid"


# ---- 6. POST /topup via Bearer — no CSRF header required ---------------------


@pytest.mark.asyncio
async def test_topup_via_bearer_no_csrf_required(app_with_yookassa, client, api_key_fixture):
    """Bearer flow must NOT require X-Requested-With.

    Bearer tokens aren't CSRF-able (no ambient credentials, third-party sites
    can't read sk-vlt-...). Forcing CSRF on M2M traffic would break SDKs.
    """
    yk_payload = {
        "id": "pay_bearer_no_csrf",
        "status": "pending",
        "amount": {"value": "2000.00", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "confirmation_url": "https://yk/confirm?id=pay_bearer_no_csrf",
        },
    }
    with respx.mock(base_url="https://test-yookassa/v3") as mock:
        mock.post("/payments").respond(200, json=yk_payload)
        r = await client.post(
            "/v1/billing/topup",
            json={"amount_rub": 2000},
            headers=api_key_fixture.auth_header,  # NO X-Requested-With
        )
    assert r.status_code == 200, r.text


# ---- 7. no auth at all → 401 -------------------------------------------------


@pytest.mark.asyncio
async def test_balance_no_auth_returns_401(client):
    """No Bearer, no cookie → 401 with auth-error envelope."""
    r = await client.get("/v1/billing/balance")
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"


# ---- 8. autorefill DELETE via cookie session ---------------------------------


@pytest.mark.asyncio
async def test_autorefill_delete_via_session_with_csrf(
    _dashboard_env, app_with_yookassa, client, db, redis_client
):
    """DELETE /v1/billing/autorefill via cookie + CSRF → 200."""
    user = await _dashboard_seed(db, balance_kop=10_000)
    await _dashboard_login(client, user)

    r = await client.delete("/v1/billing/autorefill", headers=await _csrf(client))
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
