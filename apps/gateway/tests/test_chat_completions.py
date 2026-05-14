"""POST /v1/chat/completions tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.conftest import parse_sse
from voltari_gateway.db.models import UsageEvent
from voltari_gateway.providers.base import (
    ProviderTimeoutError,
)


@pytest.mark.asyncio
async def test_non_stream_happy_path(client, api_key_fixture, db, app):
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "ping"}],
    }
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["id"]
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["usage"]["prompt_tokens"] == 10
    assert data["x_gateway"]["provider"] == "openai"
    assert "X-Request-Id" in r.headers

    # Usage event was recorded
    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].input_tokens == 10
    assert rows[0].output_tokens == 20
    assert rows[0].cost_kopecks > 0


@pytest.mark.asyncio
@pytest.mark.skip(
    reason=(
        "FLAKY — sse_starlette background task + aiosqlite event-loop binding "
        "issue causes test to hang indefinitely (~30% of runs). "
        "pytest-timeout + pytest-rerunfailures не помогли (thread метод не "
        "убивает asyncio task). Proper fix: переписать через explicit lifespan "
        "+ separate SSE consumer task. TODO Q1 2026 — Smart Router v2 S1 sprint."
    )
)
async def test_stream_happy_path(client, api_key_fixture, db):
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "stream me"}],
        "stream": True,
    }
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    assert events[-1] == "[DONE]"
    json_events = [e for e in events if isinstance(e, dict)]
    assert any(e.get("object") == "chat.completion.chunk" for e in json_events)
    # Usage event recorded after stream completion
    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].input_tokens == 10
    assert rows[0].output_tokens == 2


@pytest.mark.asyncio
async def test_invalid_key_returns_envelope(client):
    body = {"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "x"}]}
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers={"Authorization": "Bearer sk-vlt-bogusbogus"},
    )
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_missing_model_returns_404_envelope(client, api_key_fixture):
    body = {"model": "totally-fake", "messages": [{"role": "user", "content": "x"}]}
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "model_not_found"
    assert err["param"] == "model"


@pytest.mark.asyncio
async def test_malformed_json_body_returns_400_envelope(client, api_key_fixture):
    r = await client.post(
        "/v1/chat/completions",
        content=b'{"model": "gpt-5.4-mini", "messages": ',  # truncated
        headers={
            **api_key_fixture.auth_header,
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_empty_messages_rejected(client, api_key_fixture):
    body = {"model": "gpt-5.4-mini", "messages": []}
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_max_tokens_exceeds_tariff_cap_returns_400(client, api_key_fixture):
    """Phase 4 P0: token-cost DoS защита — max_tokens > tariff cap → 400.

    PAYG cap = 4096; ставим 100_000 — должно отлететь на pre-flight,
    провайдер не должен быть вызван. Это закрывает атаку с welcome-200₽
    балансом + max_tokens=131_072 на премиум-модели.
    """
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 100_000,
    }
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "max_tokens_tariff_cap"
    assert "exceeds your tariff cap" in err["message"]


@pytest.mark.asyncio
async def test_provider_timeout_translated_to_502(client, api_key_fixture, app):
    provider = app.state.openai_provider

    async def _boom(_req):
        raise ProviderTimeoutError("upstream slow")

    provider.chat_completion = _boom  # type: ignore[method-assign]

    body = {"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "x"}]}
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["type"] == "api_error"
    assert err["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_payg_zero_balance_blocks_call(client, db, api_key_fixture, redis_client):
    api_key_fixture.account.balance_kopecks = 0
    from voltari_gateway.db.models import Tariff

    api_key_fixture.account.tariff = Tariff.PAYG
    db.add(api_key_fixture.account)
    await db.commit()
    await redis_client.flushall()

    body = {"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "x"}]}
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 402
    err = r.json()["error"]
    assert err["type"] == "insufficient_quota"
