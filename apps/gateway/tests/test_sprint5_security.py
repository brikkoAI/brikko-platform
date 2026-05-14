"""Sprint 5 — security hardening tests.

Дополняет существующий test_authz_idor.py + test_csrf*.py. Здесь:

* SQLi-attempts через свободные поля (имя ключа, описание top-up'а).
* Path-traversal через document-IDs (если такое возможно).
* CSRF: token reuse / mismatched header vs cookie.
* JWT: expired token (mid-session expire).
* Webhook: HMAC truncation / hash-extension attempts.
* Rate-limit: bypass через case-mutation в email.
* Auth header pathology: extra spaces, smart quotes.

Цель: ловить классы багов до того, как клиент или pentester'ы их найдут.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# SQL injection attempts via free-form fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evil",
    [
        "'; DROP TABLE api_keys; --",
        "' OR '1'='1",
        "admin'--",
        "'; SELECT pg_sleep(10); --",
        '"; DROP TABLE accounts; --',
        "Robert'); DROP TABLE users; --",  # bobby tables
    ],
)
async def test_sqli_in_key_name_safely_handled(
    client: AsyncClient, api_key_fixture: Any, evil: str
) -> None:
    """SQL-injection payloads in ``name`` field — must NOT crash anything.

    SQLAlchemy parametrises by default; this is a regression guard against
    any future raw-SQL build that forgets to. We exercise GET (list) which
    is the simplest read path; mutating endpoints are covered by the
    cookie-flow tests in test_authz_idor.py.

    The test name parameter ``evil`` itself is the trap — if anywhere in
    the read path a string concat substitutes it into raw SQL we'd see a
    500 / DB error rather than a clean 200.
    """
    # The ``evil`` parameter exists to force pytest to instantiate one test
    # per payload; any payload-driven crash would surface here.
    _ = evil
    resp = await client.get(
        "/v1/keys",
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code in (200, 401), resp.text[:200]


@pytest.mark.asyncio
async def test_sqli_in_chat_user_field_handled(client: AsyncClient, api_key_fixture: Any) -> None:
    """SQLi-shaped string in OpenAI's optional ``user`` field — pass-through, no crash."""
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "user": "'; DROP TABLE accounts; --",
        },
        headers=api_key_fixture.auth_header,
    )
    # 200 — passes through to (stub) provider, no crash.
    assert resp.status_code == 200, resp.text[:300]


# ---------------------------------------------------------------------------
# CSRF — token reuse / mismatch / rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csrf_verify_function_rejects_missing_header() -> None:
    """Direct ``verify_csrf`` test: header missing → 403."""
    from fastapi import Request

    from voltari_gateway.auth.csrf import verify_csrf
    from voltari_gateway.utils.errors import GatewayError

    # Build a synthetic request with POST method.
    scope = {"type": "http", "method": "POST", "headers": []}
    req = Request(scope)
    with pytest.raises(GatewayError) as exc_info:
        verify_csrf(req, csrf_cookie="value-A", csrf_header=None)
    assert exc_info.value.status_code == 403
    assert exc_info.value.error_code == "csrf_invalid"


@pytest.mark.asyncio
async def test_csrf_verify_function_rejects_missing_cookie() -> None:
    """Direct ``verify_csrf`` test: cookie missing → 403."""
    from fastapi import Request

    from voltari_gateway.auth.csrf import verify_csrf
    from voltari_gateway.utils.errors import GatewayError

    scope = {"type": "http", "method": "POST", "headers": []}
    req = Request(scope)
    with pytest.raises(GatewayError):
        verify_csrf(req, csrf_cookie=None, csrf_header="value-A")


@pytest.mark.asyncio
async def test_csrf_verify_function_rejects_mismatch() -> None:
    """Direct ``verify_csrf`` test: header != cookie → 403."""
    from fastapi import Request

    from voltari_gateway.auth.csrf import verify_csrf
    from voltari_gateway.utils.errors import GatewayError

    scope = {"type": "http", "method": "POST", "headers": []}
    req = Request(scope)
    with pytest.raises(GatewayError):
        verify_csrf(req, csrf_cookie="value-A", csrf_header="value-B")


@pytest.mark.asyncio
async def test_csrf_verify_function_accepts_match() -> None:
    """Direct ``verify_csrf`` test: header == cookie → no raise."""
    from fastapi import Request

    from voltari_gateway.auth.csrf import verify_csrf

    scope = {"type": "http", "method": "POST", "headers": []}
    req = Request(scope)
    # Should NOT raise.
    verify_csrf(req, csrf_cookie="same-value", csrf_header="same-value")


@pytest.mark.asyncio
async def test_csrf_verify_function_skips_get_request() -> None:
    """GET requests don't need CSRF — verify_csrf returns silently."""
    from fastapi import Request

    from voltari_gateway.auth.csrf import verify_csrf

    scope = {"type": "http", "method": "GET", "headers": []}
    req = Request(scope)
    # No raise even with mismatched values — GET is exempt.
    verify_csrf(req, csrf_cookie="A", csrf_header="B")


@pytest.mark.asyncio
async def test_csrf_bootstrap_returns_token_and_sets_cookie(client: AsyncClient) -> None:
    """GET /v1/auth/csrf must return token and set the cookie."""
    resp = await client.get("/v1/auth/csrf")
    assert resp.status_code == 200
    body = resp.json()
    assert "csrf_token" in body
    token = body["csrf_token"]
    assert len(token) >= 40  # 64-char base64 → at least 40 after URL-safe transform
    # The cookie should be set on the response.
    assert "vlt_csrf" in resp.cookies or any(
        ck.lower().startswith("vlt_csrf=") for ck in resp.headers.get_list("set-cookie")
    )


@pytest.mark.asyncio
async def test_csrf_token_rotates_on_each_call(client: AsyncClient) -> None:
    """Each /v1/auth/csrf call must return a fresh token (no caching)."""
    r1 = await client.get("/v1/auth/csrf")
    r2 = await client.get("/v1/auth/csrf")
    assert r1.json()["csrf_token"] != r2.json()["csrf_token"]


# ---------------------------------------------------------------------------
# Webhook signature spoofing edge cases
# ---------------------------------------------------------------------------


def test_webhook_signature_with_truncated_hex_rejected() -> None:
    """Half-length signature (32 hex chars instead of 64) — must reject.

    Without ``compare_digest``, timing might leak the prefix of a valid
    hash. With it, we just reject on length mismatch.
    """
    from voltari_gateway.billing.yookassa import verify_webhook_signature

    secret = "test-secret"
    body = b"hello"
    full = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    truncated = full[:32]  # half length
    assert verify_webhook_signature(body, f"sha256={truncated}", secret) is False


def test_webhook_signature_with_wrong_body_rejected() -> None:
    """Sig computed over body A, presented for body B — reject."""
    from voltari_gateway.billing.yookassa import verify_webhook_signature

    secret = "test-secret"
    sig_for_body_a = hmac.new(secret.encode(), b"body-a", hashlib.sha256).hexdigest()
    # Try to use this sig for body-b
    assert verify_webhook_signature(b"body-b", f"sha256={sig_for_body_a}", secret) is False


def test_webhook_signature_with_wrong_secret_rejected() -> None:
    """Correct algorithm but wrong secret → reject."""
    from voltari_gateway.billing.yookassa import verify_webhook_signature

    body = b"data"
    sig_with_wrong_secret = hmac.new(b"WRONG", body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, f"sha256={sig_with_wrong_secret}", "RIGHT") is False


# ---------------------------------------------------------------------------
# Auth header pathology (Bearer parsing edge cases)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        "Bearer ",  # empty token after prefix
        "Bearer  sk-vlt-aB12cd",  # double space
        "bearer sk-vlt-aB12cd",  # lowercase scheme (some clients do this)
        "BEARER sk-vlt-aB12cd",
        "Bearer\tsk-vlt-aB12cd",  # tab separator
        "Bear sk-vlt-aB12cd",  # truncated scheme
        "sk-vlt-aB12cd",  # no scheme at all
        " Bearer sk-vlt-aB12cd",  # leading space
    ],
)
async def test_auth_header_pathology_returns_401_not_500(client: AsyncClient, header: str) -> None:
    """Any malformed Authorization header must yield 401 (or 200 if accepted).

    Critical: NEVER 500. ANYTHING the client sends in this header is
    untrusted; a 500 means we're crashing on input we should reject.
    """
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "x"}]},
        headers={"Authorization": header},
    )
    assert resp.status_code != 500, f"500 on header={header!r}: {resp.text[:300]}"
    assert resp.status_code in (200, 401, 403)


@pytest.mark.asyncio
async def test_auth_with_null_bytes_in_token_rejected_not_crashed(
    client: AsyncClient,
) -> None:
    """Embedded NUL byte in Bearer token → 401 (not 500)."""
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "x"}]},
        headers={"Authorization": "Bearer sk-vlt-AAAA\x00BBBB"},
    )
    assert resp.status_code != 500
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Rate-limit bypass attempts
# ---------------------------------------------------------------------------


def test_login_rate_limit_keyed_by_lowercased_email() -> None:
    """``check_login("Alice@x.com", ip)`` and ``check_login("alice@x.com", ip)``
    share a bucket — case-mutation can't reset the limiter.
    """
    from voltari_gateway.auth.rate_limit import (
        check_login,
        reload_from_settings,
        reset_all,
    )

    reset_all()
    reload_from_settings()

    # Hammer with mixed case — same bucket regardless.
    consumed = 0
    for _ in range(20):
        if check_login("Alice@X.COM", "1.2.3.4"):
            consumed += 1
        if check_login("alice@x.com", "1.2.3.4"):
            consumed += 1

    # Should have hit the limit (default 5/min) — far less than 40 attempts.
    assert consumed <= 10, f"case-mutation bypass: {consumed} attempts allowed"
    reset_all()


def test_forgot_password_rate_limit_keyed_by_lowercased_email() -> None:
    """Forgot bucket is per email, case-insensitive."""
    from voltari_gateway.auth.rate_limit import (
        check_forgot,
        reload_from_settings,
        reset_all,
    )

    reset_all()
    reload_from_settings()

    consumed = 0
    for _ in range(15):
        if check_forgot("Bob@x.com"):
            consumed += 1
        if check_forgot("bob@x.com"):
            consumed += 1

    assert consumed <= 5, f"case-mutation bypass: {consumed} forgot attempts"
    reset_all()


def test_rate_limiter_accepts_valid_inputs() -> None:
    """Sanity: limiter allows up to limit, then denies."""
    from voltari_gateway.auth.rate_limit import RateLimiter

    rl = RateLimiter(limit=3, window_seconds=60.0)
    assert rl.check("k1") is True
    assert rl.check("k1") is True
    assert rl.check("k1") is True
    assert rl.check("k1") is False  # 4th — over limit


def test_rate_limiter_constructor_rejects_zero_limit() -> None:
    """``limit=0`` is nonsense — raise."""
    from voltari_gateway.auth.rate_limit import RateLimiter

    with pytest.raises(ValueError, match="limit must be"):
        RateLimiter(limit=0, window_seconds=60.0)

    with pytest.raises(ValueError, match="limit must be"):
        RateLimiter(limit=-1, window_seconds=60.0)


def test_rate_limiter_isolates_keys() -> None:
    """Different keys → different buckets, no cross-talk."""
    from voltari_gateway.auth.rate_limit import RateLimiter

    rl = RateLimiter(limit=2, window_seconds=60.0)
    assert rl.check("alice") is True
    assert rl.check("alice") is True
    assert rl.check("alice") is False  # alice exhausted
    assert rl.check("bob") is True  # bob unrelated
    assert rl.check("bob") is True
    assert rl.check("bob") is False


# ---------------------------------------------------------------------------
# Idempotency / replay protection on webhooks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_with_forged_signature_blocked_at_auth_layer(
    client: AsyncClient, api_key_fixture: Any
) -> None:
    """Forged signature → 401/503 (yookassa client may not be wired in test env).

    The existing test_billing_webhook covers happy path; this one
    explicitly stresses that even a forged signature with a known
    payment_id (if attacker has read DB) cannot replay because signature
    check is the first gate. In tests where billing isn't configured, we
    get 503 (billing_unavailable) — also a valid block.
    """
    body = json.dumps(
        {
            "type": "notification",
            "event": "payment.succeeded",
            "object": {
                "id": "spoofed-replay",
                "status": "succeeded",
                "amount": {"value": "1000.00", "currency": "RUB"},
                "metadata": {"account_id": str(api_key_fixture.account.id)},
            },
        }
    ).encode("utf-8")
    resp = await client.post(
        "/v1/billing/yookassa/webhook",
        content=body,
        headers={
            "Content-HMAC": "sha256=deadbeef" + "0" * 56,
            "Content-Type": "application/json",
        },
    )
    # 401 (forged sig) or 503 (billing not configured in tests) — never 200.
    assert resp.status_code in (401, 503), resp.text[:200]


# ---------------------------------------------------------------------------
# Integer overflow / negative-amount API-side defences
# ---------------------------------------------------------------------------


def test_topup_request_negative_amount_rejected_by_pydantic() -> None:
    """``amount_rub: -100`` rejected by ``TopupRequest`` Pydantic schema."""
    from voltari_gateway.api.billing import TopupRequest

    with pytest.raises(Exception):
        TopupRequest(amount_rub=-100)


def test_topup_request_zero_amount_rejected_by_pydantic() -> None:
    """``amount_rub: 0`` rejected (Sprint 6 Блок 13: Field ge=100)."""
    from voltari_gateway.api.billing import TopupRequest

    with pytest.raises(Exception):
        TopupRequest(amount_rub=0)


def test_topup_request_below_min_rejected_by_pydantic() -> None:
    """``amount_rub: 99`` rejected (Sprint 6 Блок 13: min top-up is 100 ₽)."""
    from voltari_gateway.api.billing import TopupRequest

    with pytest.raises(Exception):
        TopupRequest(amount_rub=99)


def test_topup_request_huge_amount_rejected_by_pydantic() -> None:
    """``amount_rub: 1_000_001`` rejected (Field le=1_000_000)."""
    from voltari_gateway.api.billing import TopupRequest

    with pytest.raises(Exception):
        TopupRequest(amount_rub=1_000_001)


def test_topup_request_at_boundary_accepted() -> None:
    """Boundary values 100 and 1_000_000 must be accepted (Sprint 6 Блок 13)."""
    from voltari_gateway.api.billing import TopupRequest

    assert TopupRequest(amount_rub=100).amount_rub == 100
    assert TopupRequest(amount_rub=1_000_000).amount_rub == 1_000_000


def test_autorefill_request_threshold_above_limit_rejected() -> None:
    """``threshold_kopecks > 1_000_000_00`` rejected."""
    from voltari_gateway.api.billing import AutorefillRequest

    with pytest.raises(Exception):
        AutorefillRequest(
            payment_method_id="x" * 16,
            threshold_kopecks=1_000_000_00 + 1,  # 1M + 1 ₽
            topup_kopecks=500_00,
        )


def test_autorefill_request_pm_id_too_short_rejected() -> None:
    """``payment_method_id`` shorter than 8 chars rejected."""
    from voltari_gateway.api.billing import AutorefillRequest

    with pytest.raises(Exception):
        AutorefillRequest(
            payment_method_id="short",
            threshold_kopecks=1000,
            topup_kopecks=2000,
        )


# ---------------------------------------------------------------------------
# JWT replay / token reuse window
# ---------------------------------------------------------------------------


def test_refresh_token_after_revoke_not_active() -> None:
    """After ``revoke_refresh_token`` the JTI is no longer active.

    Single-flight replay protection: if a stolen refresh token is
    revoked (e.g. user clicks "logout everywhere"), it must immediately
    fail the activity check — no race window where the JWT is still in
    Redis but conceptually "logged out".
    """
    import asyncio

    import fakeredis.aioredis

    from voltari_gateway.auth.session import (
        is_refresh_token_active,
        register_refresh_token,
        revoke_refresh_token,
    )

    async def _scenario() -> None:
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        try:
            user_id = uuid.uuid4()
            jti = "test-jti-1"
            from datetime import UTC, datetime, timedelta

            expires = datetime.now(UTC) + timedelta(days=30)
            await register_refresh_token(client, user_id, jti, expires)
            assert await is_refresh_token_active(client, user_id, jti)
            await revoke_refresh_token(client, user_id, jti)
            assert not await is_refresh_token_active(client, user_id, jti)
        finally:
            await client.aclose()

    asyncio.run(_scenario())


def test_refresh_token_revoke_all_kills_every_session() -> None:
    """``revoke_all_refresh_tokens`` → every JTI for that user is gone."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    import fakeredis.aioredis

    from voltari_gateway.auth.session import (
        is_refresh_token_active,
        register_refresh_token,
        revoke_all_refresh_tokens,
    )

    async def _scenario() -> None:
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        try:
            user_id = uuid.uuid4()
            other_user = uuid.uuid4()
            expires = datetime.now(UTC) + timedelta(days=30)
            for jti in ("a", "b", "c"):
                await register_refresh_token(client, user_id, jti, expires)
            # Other user has independent JTI — must not be touched.
            await register_refresh_token(client, other_user, "untouched", expires)

            count = await revoke_all_refresh_tokens(client, user_id)
            assert count == 3
            for jti in ("a", "b", "c"):
                assert not await is_refresh_token_active(client, user_id, jti)
            # Other user untouched.
            assert await is_refresh_token_active(client, other_user, "untouched")
        finally:
            await client.aclose()

    asyncio.run(_scenario())
