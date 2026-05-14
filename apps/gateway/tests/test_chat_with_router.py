"""Integration tests for /v1/chat/completions with the router engaged.

These exercise the router → registry → failover → billing pipeline using
the existing ``StubProvider`` (registered as the OpenAI adapter) plus
additional stubs that we plug into the registry under non-OpenAI keys.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select

from voltari_gateway.db.models import UsageEvent
from voltari_gateway.providers.base import (
    ChatCompletionResponse,
    ChatCompletionUsage,
    Provider,
    ProviderServerError,
)
from voltari_gateway.router.catalog import Provider as ProviderEnum

# -- helpers ------------------------------------------------------------------


class FailingProvider(Provider):
    """Provider that always raises a given error — used to exercise failover."""

    def __init__(self, name: str, exc_factory) -> None:
        self.name = name
        self._exc_factory = exc_factory
        self.calls = 0

    async def chat_completion(self, req):
        self.calls += 1
        raise self._exc_factory()

    async def chat_completion_stream(self, req):
        self.calls += 1
        raise self._exc_factory()

    async def aclose(self) -> None:
        return None


class CountingStub(Provider):
    """Mirror of conftest StubProvider but with a recorder."""

    def __init__(self, name: str = "openai") -> None:
        self.name = name
        self.calls: list[str] = []  # model.id list

    async def chat_completion(self, req):
        self.calls.append(req.model.id)
        return ChatCompletionResponse(
            raw={
                "id": "chatcmpl-cs",
                "object": "chat.completion",
                "created": 1,
                "model": req.model.id,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
            usage=ChatCompletionUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
            model_id=req.model.id,
            provider=self.name,
        )

    async def chat_completion_stream(self, req):
        self.calls.append(req.model.id)
        chunks = [
            (
                f'data: {{"id":"x","object":"chat.completion.chunk","model":"{req.model.id}",'
                f'"choices":[{{"index":0,"delta":{{"content":"hi"}},"finish_reason":null}}]}}'
            ).encode()
            + b"\n\n",
            (
                f'data: {{"id":"x","object":"chat.completion.chunk","model":"{req.model.id}",'
                f'"choices":[{{"index":0,"delta":{{}},"finish_reason":"stop"}}],'
                f'"usage":{{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}}}'
            ).encode()
            + b"\n\n",
            b"data: [DONE]\n\n",
        ]

        async def _gen() -> AsyncIterator[bytes]:
            for c in chunks:
                yield c

        return _gen()

    async def aclose(self) -> None:
        return None


# -- tests --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_model_returns_router_decision_header(client, api_key_fixture, app) -> None:
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "ping"}],
    }
    r = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    assert "X-Router-Decision" in r.headers
    decision = r.headers["X-Router-Decision"]
    assert decision.startswith("gpt-5.4-mini;")
    assert "reason=pinned:gpt-5.4-mini" in decision
    assert "failover_used=false" in decision
    body_resp = r.json()
    assert body_resp["x_gateway"]["router"]["failover_used"] is False
    assert body_resp["x_gateway"]["router"]["events"][0]["succeeded"] is True


@pytest.mark.asyncio
async def test_auto_cheap_picks_cheapest_configured(client, api_key_fixture, app) -> None:
    """auto:cheap with only OpenAI configured → picks cheapest OpenAI model.

    Updated 2026-05-09 (Sprint M2): catalog now contains gpt-4o-mini at
    $0.15 in / $0.60 out, which is cheaper on the 70/30 blended price than
    gpt-5.4-mini ($0.75 / $4.50). The router correctly picks it.
    """
    body = {
        "model": "auto:cheap",
        "messages": [{"role": "user", "content": "ping"}],
    }
    r = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    decision = r.headers["X-Router-Decision"]
    assert decision.startswith("gpt-4o-mini;")


@pytest.mark.asyncio
async def test_failover_walks_chain_when_primary_fails(client, api_key_fixture, app, db) -> None:
    """Primary OpenAI fails → router walks chain; we install a working Anthropic.

    The router's pinned-mode fallback chain is built across providers.
    For ``gpt-5.4-mini`` (MID tier) the chain prefers other-provider
    MID-tier models — Anthropic Sonnet is one of them.
    """
    counting_anthropic = CountingStub(name="anthropic")
    failing_openai = FailingProvider("openai", lambda: ProviderServerError("boom", status_code=503))
    # Replace existing OpenAI in registry; add Anthropic.
    app.state.provider_registry._providers[ProviderEnum.OPENAI] = failing_openai
    app.state.openai_provider = failing_openai
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, counting_anthropic)

    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "ping"}],
    }
    r = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    decision = r.headers["X-Router-Decision"]
    # Failover chose a non-OpenAI model from the chain.
    assert "failover_used=true" in decision
    body_resp = r.json()
    assert body_resp["x_gateway"]["router"]["failover_used"] is True
    # The failing primary made it into events.
    events = body_resp["x_gateway"]["router"]["events"]
    assert any(e["model_id"] == "gpt-5.4-mini" and not e["succeeded"] for e in events)
    # Anthropic stub got at least one call.
    assert len(counting_anthropic.calls) >= 1


@pytest.mark.asyncio
async def test_failover_disabled_propagates_first_error(client, api_key_fixture, app) -> None:
    """`failover: false` opts out — first model error becomes 502."""
    failing = FailingProvider("openai", lambda: ProviderServerError("boom", status_code=503))
    app.state.provider_registry._providers[ProviderEnum.OPENAI] = failing
    app.state.openai_provider = failing

    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "ping"}],
        "failover": False,
    }
    r = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_usage_event_records_chosen_model_and_cost(client, api_key_fixture, app, db) -> None:
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "ping"}],
    }
    r = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].model == "gpt-5.4-mini"
    assert rows[0].cost_kopecks > 0


@pytest.mark.asyncio
async def test_unknown_model_returns_404(client, api_key_fixture, app) -> None:
    body = {
        "model": "totally-fake-model-2099",
        "messages": [{"role": "user", "content": "ping"}],
    }
    r = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "model_not_found"


# ---------------------------------------------------------------------------
# auto:code + Anthropic cache_control wiring
# ---------------------------------------------------------------------------
#
# Smoke-tests the full path: client requests ``model: "auto:code"`` →
# router picks claude-sonnet-4.6 → AnthropicProvider attaches cache_control
# to a large system prompt. Uses respx so we can inspect the actual
# upstream JSON body without a real API key.


@pytest.mark.asyncio
async def test_auto_code_anthropic_primary_sends_cache_control(
    client, api_key_fixture, app
) -> None:
    """``auto:code`` → Anthropic primary → adapter sets ``cache_control``.

    Verifies the contract Cursor / Claude Code clients depend on: a large
    system prompt is sent with ``cache_control: ephemeral`` so subsequent
    coding-agent turns hit the prompt cache (10× cheaper input).
    """
    import json

    import respx

    from voltari_gateway.providers.anthropic_provider import (
        CACHE_THRESHOLD_CHARS,
        AnthropicProvider,
    )
    from voltari_gateway.router.catalog import Provider as ProviderEnum

    real_anthropic = AnthropicProvider(api_key="sk-ant-test")
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, real_anthropic)

    big_system = "You are an expert TypeScript engineer. " * 200  # >>4096 chars
    assert len(big_system) > CACHE_THRESHOLD_CHARS

    upstream_body = {
        "id": "msg_xyz",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": "ack"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 50,
            "output_tokens": 10,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }

    try:
        with respx.mock(assert_all_called=True) as router_mock:
            route = router_mock.post("https://api.anthropic.com/v1/messages").respond(
                200, json=upstream_body
            )
            r = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto:code",
                    "messages": [
                        {"role": "system", "content": big_system},
                        {"role": "user", "content": "refactor this fn"},
                    ],
                },
                headers=api_key_fixture.auth_header,
            )
            assert r.status_code == 200, r.text
            # Router resolved auto:code → Sonnet 4.6.
            assert r.headers["X-Router-Decision"].startswith("claude-sonnet-4.6;")
            # Inspect the actual upstream payload — large system prompt
            # must carry ``cache_control`` ephemeral on the last block.
            sent = json.loads(route.calls.last.request.content)
            assert isinstance(sent["system"], list)
            assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    finally:
        await real_anthropic.aclose()
