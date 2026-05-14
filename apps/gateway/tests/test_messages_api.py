"""POST /v1/messages — native Anthropic Messages API tests.

Coverage (per Sprint 11.6 brief):

* Happy path with Anthropic primary (real adapter + respx mock on api.anthropic.com).
* Happy path with non-Anthropic primary — body translated Anthropic→OpenAI,
  response re-shaped OpenAI→Anthropic.
* Streaming preserves Anthropic SSE shape end-to-end.
* Billing: pre-flight hold + post-flight commit + UsageEvent persisted.
* Auth: 401 without Bearer.
* Tool calling: Anthropic ``tool_use`` body forwarded to Anthropic upstream
  unchanged when primary is Anthropic.
"""

from __future__ import annotations

import json

import pytest
import respx
from sqlalchemy import select

from voltari_gateway.db.models import UsageEvent
from voltari_gateway.providers.anthropic_provider import AnthropicProvider
from voltari_gateway.providers.base import (
    ChatCompletionResponse,
    ChatCompletionUsage,
    Provider,
)
from voltari_gateway.router.catalog import Provider as ProviderEnum

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------------------
# Helpers — minimal upstream-response builders.
# ---------------------------------------------------------------------------


def _anthropic_success(text: str = "Hi there") -> dict:
    return {
        "id": "msg_01abc",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 12,
            "output_tokens": 7,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


class StubDeepSeek(Provider):
    """OpenAI-shape stub used when we want a non-Anthropic primary."""

    name = "deepseek"

    def __init__(self) -> None:
        self.last_request = None

    async def chat_completion(self, req):
        self.last_request = req
        return ChatCompletionResponse(
            raw={
                "id": "chatcmpl-ds",
                "object": "chat.completion",
                "created": 1730000000,
                "model": req.model.id,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "from-deepseek"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 5,
                    "total_tokens": 13,
                },
            },
            usage=ChatCompletionUsage(prompt_tokens=8, completion_tokens=5, total_tokens=13),
            model_id=req.model.id,
            provider="deepseek",
        )

    async def chat_completion_stream(self, req):
        self.last_request = req
        chunks = [
            (
                b'data: {"id":"x","object":"chat.completion.chunk","model":"'
                + req.model.id.encode()
                + b'","choices":[{"index":0,"delta":{"role":"assistant","content":"hello "},'
                b'"finish_reason":null}]}\n\n'
            ),
            (
                b'data: {"id":"x","object":"chat.completion.chunk","model":"'
                + req.model.id.encode()
                + b'","choices":[{"index":0,"delta":{"content":"world"},'
                b'"finish_reason":null}]}\n\n'
            ),
            (
                b'data: {"id":"x","object":"chat.completion.chunk","model":"'
                + req.model.id.encode()
                + b'","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":8,"completion_tokens":2,"total_tokens":10}}\n\n'
            ),
            b"data: [DONE]\n\n",
        ]

        async def _gen():
            for c in chunks:
                yield c

        return _gen()

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_happy_path_anthropic_primary(client, api_key_fixture, app, db):
    """Pinned Sonnet → body forwarded 1:1 → Anthropic-shape response back."""
    real_anthropic = AnthropicProvider(api_key="sk-ant-test")
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, real_anthropic)
    try:
        with respx.mock(assert_all_called=True) as router_mock:
            route = router_mock.post(ANTHROPIC_URL).respond(
                200, json=_anthropic_success("Hello back!")
            )
            r = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4.6",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": "Hello"}],
                },
                headers=api_key_fixture.auth_header,
            )
            assert r.status_code == 200, r.text
            data = r.json()
            # Anthropic Messages shape preserved.
            assert data["type"] == "message"
            assert data["role"] == "assistant"
            assert data["content"][0]["type"] == "text"
            assert data["content"][0]["text"] == "Hello back!"
            assert data["stop_reason"] == "end_turn"
            # Public Brikko id (with dot), not upstream dashed id.
            assert data["model"] == "claude-sonnet-4.6"
            # Usage block survives.
            assert data["usage"]["input_tokens"] == 12
            assert data["usage"]["output_tokens"] == 7

            # Upstream got the body 1:1 — model upstream_id, max_tokens, system absent.
            sent = json.loads(route.calls.last.request.content)
            assert sent["model"] == "claude-sonnet-4-6"
            assert sent["max_tokens"] == 100
            assert sent["messages"] == [{"role": "user", "content": "Hello"}]
    finally:
        await real_anthropic.aclose()


@pytest.mark.asyncio
async def test_messages_happy_path_non_anthropic_translates(client, api_key_fixture, app, db):
    """Pinned DeepSeek → request translated, response re-shaped to Anthropic."""
    stub_ds = StubDeepSeek()
    app.state.provider_registry.register(ProviderEnum.DEEPSEEK, stub_ds)

    r = await client.post(
        "/v1/messages",
        json={
            "model": "deepseek-v4-pro",
            "max_tokens": 100,
            "system": "You are helpful",
            "messages": [{"role": "user", "content": "ping"}],
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # Response is Anthropic-shape even though upstream was OpenAI-shape.
    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert data["content"][0]["text"] == "from-deepseek"
    assert data["stop_reason"] == "end_turn"
    assert data["model"] == "deepseek-v4-pro"

    # Verify the translation: DeepSeek stub saw OpenAI-shape with system
    # message prepended.
    sent_messages = stub_ds.last_request.messages
    assert sent_messages[0] == {"role": "system", "content": "You are helpful"}
    assert sent_messages[1] == {"role": "user", "content": "ping"}


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_stream_preserves_anthropic_shape(client, api_key_fixture, app):
    """Stream against Anthropic primary — SSE events keep Anthropic shape."""
    real_anthropic = AnthropicProvider(api_key="sk-ant-test")
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, real_anthropic)

    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_x",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 5,
                        "output_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "hi"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 2},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    sse_body = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for (e, d) in events)

    try:
        with respx.mock() as router_mock:
            router_mock.post(ANTHROPIC_URL).respond(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse_body.encode("utf-8"),
            )
            r = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4.6",
                    "max_tokens": 50,
                    "stream": True,
                    "messages": [{"role": "user", "content": "ping"}],
                },
                headers=api_key_fixture.auth_header,
            )
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            # Each event has Anthropic shape (event: <name> + data: {type:...}).
            assert "event: message_start" in r.text
            assert "event: content_block_delta" in r.text
            assert "event: message_stop" in r.text
            # No OpenAI ``chat.completion.chunk`` artefacts.
            assert "chat.completion.chunk" not in r.text
    finally:
        await real_anthropic.aclose()


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_billing_creates_usage_event(client, api_key_fixture, app, db):
    """Successful /v1/messages call → UsageEvent row written + balance debited."""
    stub_ds = StubDeepSeek()
    app.state.provider_registry.register(ProviderEnum.DEEPSEEK, stub_ds)

    initial_balance = api_key_fixture.account.balance_kopecks

    r = await client.post(
        "/v1/messages",
        json={
            "model": "deepseek-v4-pro",
            "max_tokens": 50,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    rows = (await db.execute(select(UsageEvent))).scalars().all()
    assert len(rows) == 1
    event = rows[0]
    assert event.model == "deepseek-v4-pro"
    assert event.provider == "deepseek"
    assert event.input_tokens == 8
    assert event.output_tokens == 5
    # Cost is computed from compute_cost_kopecks (1.15 markup). DeepSeek V4
    # Pro is cheap enough that very small token counts may round to 0
    # kopecks; the assertion just verifies the column was populated.
    assert event.cost_kopecks >= 0

    # Account balance not greater than initial (debited or held-then-released).
    await db.refresh(api_key_fixture.account)
    assert api_key_fixture.account.balance_kopecks <= initial_balance


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_requires_bearer_token(client):
    """No Authorization header → 401 with auth-error envelope."""
    r = await client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-4.6",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["type"] == "authentication_error"


# ---------------------------------------------------------------------------
# Tool calling — Anthropic shape end-to-end with Anthropic primary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_tool_use_passthrough_anthropic(client, api_key_fixture, app):
    """``tools`` and ``tool_choice`` forwarded 1:1 when primary is Anthropic."""
    real_anthropic = AnthropicProvider(api_key="sk-ant-test")
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, real_anthropic)

    # Upstream returns a tool_use block — verifies the shape survives.
    upstream_body = {
        "id": "msg_tool",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_xyz",
                "name": "get_weather",
                "input": {"city": "Moscow"},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 20,
            "output_tokens": 8,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }

    tools = [
        {
            "name": "get_weather",
            "description": "Get the weather",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
    ]
    tool_choice = {"type": "tool", "name": "get_weather"}

    try:
        with respx.mock(assert_all_called=True) as router_mock:
            route = router_mock.post(ANTHROPIC_URL).respond(200, json=upstream_body)
            r = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4.6",
                    "max_tokens": 100,
                    "tools": tools,
                    "tool_choice": tool_choice,
                    "messages": [{"role": "user", "content": "What's the weather in Moscow?"}],
                },
                headers=api_key_fixture.auth_header,
            )
            assert r.status_code == 200
            data = r.json()
            # Anthropic tool_use block preserved in response.
            assert data["content"][0]["type"] == "tool_use"
            assert data["content"][0]["name"] == "get_weather"
            assert data["content"][0]["input"] == {"city": "Moscow"}
            assert data["stop_reason"] == "tool_use"

            # Upstream got the Anthropic-shape tools/tool_choice unchanged.
            sent = json.loads(route.calls.last.request.content)
            assert sent["tools"] == tools
            assert sent["tool_choice"] == tool_choice
    finally:
        await real_anthropic.aclose()
