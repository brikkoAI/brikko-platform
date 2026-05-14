"""Sprint 12 — POST /v1/public/playground tests.

Sandbox is anonymous, rate-limited per-IP, budget-capped, and bypasses
the platform's billing/PII pipeline. The fixtures here pin
``SANDBOX_API_KEY`` (otherwise endpoint short-circuits to 503 by design)
and reuse the global ``StubProvider`` from conftest as the OpenAI
adapter — sandbox routes through the same registry as /v1/chat/completions.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from voltari_gateway.auth.middleware import set_redis
from voltari_gateway.config import get_settings

# Configure SANDBOX_* env BEFORE creating the app so pydantic-settings sees them.
# Using module-scope assignments because get_settings() is lru_cache'd in process.
os.environ["SANDBOX_API_KEY"] = "sk-vlt-sandbox-fixture-AAAAAAAAAAAAAAAAAAAAAAAAAA"
os.environ["SANDBOX_RATE_LIMIT_HOUR"] = "5"
os.environ["SANDBOX_RATE_LIMIT_DAY"] = "15"
os.environ["SANDBOX_BUDGET_DAY_KOP"] = "30000"
os.environ["SANDBOX_MAX_PROMPT_LENGTH"] = "500"
os.environ["SANDBOX_MAX_TOKENS"] = "150"


@pytest_asyncio.fixture(scope="function")
async def sandbox_settings_reset():
    """Force ``get_settings`` to re-read env after our overrides."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture(scope="function")
async def sandbox_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    """Dedicated fakeredis injected via auth.middleware.set_redis.

    Sandbox reads Redis through the same accessor as the chat path, so
    the existing ``redis_client`` fixture from conftest works — but
    spinning up our own makes it explicit which keys this test owns.
    """
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis(client)
    yield client
    set_redis(None)
    await client.aclose()


@pytest_asyncio.fixture(scope="function")
async def sandbox_app(engine, session_factory, sandbox_redis, sandbox_settings_reset):
    """App built AFTER env overrides are in place (matters for SANDBOX_API_KEY)."""
    from tests.conftest import StubProvider
    from voltari_gateway.main import create_app
    from voltari_gateway.providers.registry import ProviderRegistry
    from voltari_gateway.router.catalog import Provider as ProviderEnum
    from voltari_gateway.router.router import Router as RouterEngine

    application = create_app()
    stub = StubProvider()
    application.state.openai_provider = stub
    registry = ProviderRegistry()
    registry.register(ProviderEnum.OPENAI, stub)
    application.state.provider_registry = registry
    application.state.router_engine = RouterEngine()
    application.state.failover_backoffs_ms = (1, 1, 1)
    application.state.failover_timeout_s = 5.0
    application.state.telegram_bot = None
    yield application, stub


@pytest_asyncio.fixture(scope="function")
async def sandbox_client(sandbox_app) -> AsyncIterator[tuple[AsyncClient, object]]:
    application, stub = sandbox_app
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, stub


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path(sandbox_client):
    client, stub = sandbox_client
    body = {"model": "gpt-5.4-mini", "prompt": "лимерик про PM"}
    r = await client.post(
        "/v1/public/playground",
        json=body,
        headers={"CF-Connecting-IP": "203.0.113.10"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # OpenAI-compatible chat.completion shape.
    assert data["object"] == "chat.completion"
    assert data["model"] == "gpt-5.4-mini"
    assert data["choices"][0]["message"]["role"] == "assistant"
    # Sandbox-specific headers.
    assert r.headers["X-Sandbox-Cache"] == "miss"
    assert int(r.headers["X-Sandbox-Remaining-Hour"]) == 4
    assert int(r.headers["X-Sandbox-Remaining-Day"]) == 14


@pytest.mark.asyncio
async def test_max_tokens_forced_to_150(sandbox_client):
    """Even though sandbox body has no max_tokens field, the upstream
    provider call must be made with max_tokens=150 (per PRD §3.3)."""
    client, stub = sandbox_client
    r = await client.post(
        "/v1/public/playground",
        json={"model": "gpt-5.4-mini", "prompt": "ping"},
        headers={"CF-Connecting-IP": "203.0.113.11"},
    )
    assert r.status_code == 200
    assert stub.last_request is not None
    # Sandbox owns max_tokens — clients have no way to override.
    assert stub.last_request.max_tokens == 150


# ---------------------------------------------------------------------------
# Rate-limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hourly_rate_limit(sandbox_client):
    client, _stub = sandbox_client
    headers = {"CF-Connecting-IP": "203.0.113.20"}
    # 5 successful requests with DIFFERENT prompts (avoid cache).
    for i in range(5):
        body = {"model": "gpt-5.4-mini", "prompt": f"prompt-{i}"}
        r = await client.post("/v1/public/playground", json=body, headers=headers)
        assert r.status_code == 200, f"call #{i}: {r.text}"

    # 6th call from the same IP must be 429.
    r = await client.post(
        "/v1/public/playground",
        json={"model": "gpt-5.4-mini", "prompt": "prompt-extra"},
        headers=headers,
    )
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    err = r.json()["error"]
    assert err["code"] == "sandbox_rate_limit"


@pytest.mark.asyncio
async def test_rate_limit_isolated_per_ip(sandbox_client):
    """Different IPs have independent counters."""
    client, _stub = sandbox_client
    for i in range(5):
        r = await client.post(
            "/v1/public/playground",
            json={"model": "gpt-5.4-mini", "prompt": f"a-{i}"},
            headers={"CF-Connecting-IP": "203.0.113.30"},
        )
        assert r.status_code == 200
    # Different IP → fresh quota.
    r = await client.post(
        "/v1/public/playground",
        json={"model": "gpt-5.4-mini", "prompt": "b-0"},
        headers={"CF-Connecting-IP": "203.0.113.31"},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_skips_provider(sandbox_client):
    """Identical (model, prompt) pair returns cached body without hitting
    the upstream the second time. Verified via stub call_count surrogate."""
    client, stub = sandbox_client

    # Counter trick: wrap chat_completion so we can count invocations
    # without changing the StubProvider class.
    call_count = {"n": 0}
    original = stub.chat_completion

    async def _counting_call(req):
        call_count["n"] += 1
        return await original(req)

    stub.chat_completion = _counting_call  # type: ignore[method-assign]

    body = {"model": "gpt-5.4-mini", "prompt": "hello cache"}

    # Use different IPs so rate-limit on the same IP doesn't kick in
    # before we reach the cache-hit assertion.
    r1 = await client.post(
        "/v1/public/playground",
        json=body,
        headers={"CF-Connecting-IP": "203.0.113.40"},
    )
    assert r1.status_code == 200
    assert r1.headers["X-Sandbox-Cache"] == "miss"

    r2 = await client.post(
        "/v1/public/playground",
        json=body,
        headers={"CF-Connecting-IP": "203.0.113.41"},
    )
    assert r2.status_code == 200
    assert r2.headers["X-Sandbox-Cache"] == "hit"
    # Body is byte-identical
    assert r2.json() == r1.json()
    # Only ONE provider call total.
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_too_long_returns_422(sandbox_client):
    """Sandbox prompt cap (500 by default) is enforced at the API edge."""
    client, _stub = sandbox_client
    long_prompt = "a" * 501
    r = await client.post(
        "/v1/public/playground",
        json={"model": "gpt-5.4-mini", "prompt": long_prompt},
        headers={"CF-Connecting-IP": "203.0.113.50"},
    )
    # 400 from our error-envelope normaliser (matches /v1/chat/completions
    # which also re-shapes pydantic 422 to 400 invalid_request_error).
    assert r.status_code in (400, 422)
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_unknown_model_returns_422(sandbox_client):
    """Sandbox is whitelist-only; unknown model id rejected at pydantic stage."""
    client, _stub = sandbox_client
    r = await client.post(
        "/v1/public/playground",
        json={"model": "claude-opus-4.7", "prompt": "x"},
        headers={"CF-Connecting-IP": "203.0.113.51"},
    )
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_empty_prompt_rejected(sandbox_client):
    client, _stub = sandbox_client
    r = await client.post(
        "/v1/public/playground",
        json={"model": "gpt-5.4-mini", "prompt": ""},
        headers={"CF-Connecting-IP": "203.0.113.52"},
    )
    assert r.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Sandbox not configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sandbox_not_configured_returns_503(engine, session_factory, sandbox_redis):
    """Empty SANDBOX_API_KEY → endpoint returns 503 (graceful, not 500)."""
    saved = os.environ.get("SANDBOX_API_KEY")
    os.environ["SANDBOX_API_KEY"] = ""
    try:
        get_settings.cache_clear()

        from tests.conftest import StubProvider
        from voltari_gateway.main import create_app
        from voltari_gateway.providers.registry import ProviderRegistry
        from voltari_gateway.router.catalog import Provider as ProviderEnum
        from voltari_gateway.router.router import Router as RouterEngine

        application = create_app()
        stub = StubProvider()
        registry = ProviderRegistry()
        registry.register(ProviderEnum.OPENAI, stub)
        application.state.provider_registry = registry
        application.state.router_engine = RouterEngine()
        application.state.telegram_bot = None

        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/v1/public/playground",
                json={"model": "gpt-5.4-mini", "prompt": "x"},
                headers={"CF-Connecting-IP": "203.0.113.60"},
            )
        assert r.status_code == 503
        err = r.json()["error"]
        assert err["code"] == "sandbox_unavailable"
    finally:
        if saved is None:
            os.environ.pop("SANDBOX_API_KEY", None)
        else:
            os.environ["SANDBOX_API_KEY"] = saved
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Budget cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_exhausted_returns_503(sandbox_client, sandbox_redis):
    """Manually pre-fill the budget counter to simulate a busy day."""
    client, _stub = sandbox_client
    settings = get_settings()
    from datetime import UTC, datetime

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    await sandbox_redis.set(f"sandbox:budget:day:{today}", str(settings.sandbox_budget_day_kop))

    r = await client.post(
        "/v1/public/playground",
        json={"model": "gpt-5.4-mini", "prompt": "any prompt"},
        headers={"CF-Connecting-IP": "203.0.113.70"},
    )
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "sandbox_budget_exhausted"
