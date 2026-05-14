"""AnthropicProvider tests via respx.

We mock the Anthropic API at the HTTP layer (the ``anthropic`` SDK is
itself a thin shell over httpx) so we exercise the full request-build /
response-translate path without a real API key.

Coverage:
* non-stream happy path → OpenAI-shape envelope.
* prompt caching: large system prompt produces ``cache_control``.
* tool-use response → ``tool_calls`` in OpenAI shape.
* streaming → OpenAI ``chat.completion.chunk`` SSE.
* 429 → ``ProviderRateLimitError``.
* 401 → ``ProviderAuthError``.
* 503 → ``ProviderServerError``.
"""

from __future__ import annotations

import json

import pytest
import respx

from voltari_gateway.providers.anthropic_provider import (
    CACHE_THRESHOLD_CHARS,
    AnthropicProvider,
    _split_system,
)
from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderServerError,
)
from voltari_gateway.router.catalog import get_model

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def _model():
    m = get_model("claude-sonnet-4.6")
    assert m is not None
    return m


def _make_req(messages, **kw) -> ChatCompletionRequest:
    return ChatCompletionRequest(model=_model(), messages=messages, **kw)


def _success_body(text: str = "Hello!", *, with_tool: bool = False) -> dict:
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if with_tool:
        content.append(
            {
                "type": "tool_use",
                "id": "toolu_abc",
                "name": "get_weather",
                "input": {"city": "Moscow"},
            }
        )
    return {
        "id": "msg_01abc",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4.6",
        "content": content,
        "stop_reason": "tool_use" if with_tool else "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 12,
            "output_tokens": 7,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


@pytest.mark.asyncio
async def test_non_stream_happy_path() -> None:
    provider = AnthropicProvider(api_key="sk-ant-test")
    try:
        with respx.mock(assert_all_called=True) as router:
            route = router.post(ANTHROPIC_URL).respond(200, json=_success_body("Hi there"))
            req = _make_req([{"role": "user", "content": "ping"}])
            resp = await provider.chat_completion(req)
            assert resp.provider == "anthropic"
            assert resp.model_id == "claude-sonnet-4.6"
            assert resp.usage.prompt_tokens == 12
            assert resp.usage.completion_tokens == 7
            assert resp.raw["choices"][0]["message"]["content"] == "Hi there"
            assert resp.raw["choices"][0]["finish_reason"] == "stop"
            assert resp.raw["object"] == "chat.completion"
            # Sent body — verify upstream id used (dashed per Anthropic API).
            sent = json.loads(route.calls.last.request.content)
            assert sent["model"] == "claude-sonnet-4-6"
            assert sent["max_tokens"] == 4096  # default
            assert sent["messages"] == [{"role": "user", "content": "ping"}]
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_large_system_prompt_gets_cache_control() -> None:
    provider = AnthropicProvider(api_key="sk-ant-test")
    big_system = "x" * (CACHE_THRESHOLD_CHARS + 100)
    try:
        with respx.mock() as router:
            router.post(ANTHROPIC_URL).respond(200, json=_success_body("ack"))
            req = _make_req(
                [
                    {"role": "system", "content": big_system},
                    {"role": "user", "content": "hi"},
                ]
            )
            await provider.chat_completion(req)
            sent = json.loads(router.calls.last.request.content)
            assert isinstance(sent["system"], list)
            assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
            assert sent["system"][0]["text"] == big_system
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_short_system_prompt_stays_plain_string() -> None:
    provider = AnthropicProvider(api_key="sk-ant-test")
    try:
        with respx.mock() as router:
            router.post(ANTHROPIC_URL).respond(200, json=_success_body("ack"))
            req = _make_req(
                [
                    {"role": "system", "content": "Be concise."},
                    {"role": "user", "content": "hi"},
                ]
            )
            await provider.chat_completion(req)
            sent = json.loads(router.calls.last.request.content)
            assert sent["system"] == "Be concise."
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_tool_use_translates_to_tool_calls() -> None:
    provider = AnthropicProvider(api_key="sk-ant-test")
    try:
        with respx.mock() as router:
            router.post(ANTHROPIC_URL).respond(200, json=_success_body("", with_tool=True))
            req = _make_req([{"role": "user", "content": "weather?"}])
            resp = await provider.chat_completion(req)
            msg = resp.raw["choices"][0]["message"]
            assert msg["content"] is None
            assert msg["tool_calls"][0]["function"]["name"] == "get_weather"
            assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"city": "Moscow"}
            assert resp.raw["choices"][0]["finish_reason"] == "tool_calls"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_streaming_translates_to_openai_chunks() -> None:
    """Stream translation: anthropic event types → openai chat.completion.chunk."""
    provider = AnthropicProvider(api_key="sk-ant-test")
    # Build a synthetic Anthropic SSE stream.
    events = [
        {
            "event": "message_start",
            "data": {
                "type": "message_start",
                "message": {
                    "id": "msg_x",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-sonnet-4.6",
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
        },
        {
            "event": "content_block_start",
            "data": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        },
        {
            "event": "content_block_delta",
            "data": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello"},
            },
        },
        {
            "event": "content_block_delta",
            "data": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": " world"},
            },
        },
        {
            "event": "content_block_stop",
            "data": {"type": "content_block_stop", "index": 0},
        },
        {
            "event": "message_delta",
            "data": {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 4},
            },
        },
        {"event": "message_stop", "data": {"type": "message_stop"}},
    ]
    sse_body = "".join(f"event: {e['event']}\ndata: {json.dumps(e['data'])}\n\n" for e in events)

    try:
        with respx.mock() as router:
            router.post(ANTHROPIC_URL).respond(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse_body.encode("utf-8"),
            )
            req = _make_req([{"role": "user", "content": "hi"}])
            chunks_iter = await provider.chat_completion_stream(req)
            collected: list[bytes] = []
            async for c in chunks_iter:
                collected.append(c)
            text = b"".join(collected).decode("utf-8")
            assert "data: [DONE]\n\n" in text
            # First chunk should carry the assistant role.
            chunk_lines = [
                line
                for line in text.split("\n")
                if line.startswith("data: ") and not line.endswith("[DONE]")
            ]
            first = json.loads(chunk_lines[0][len("data: ") :])
            assert first["choices"][0]["delta"].get("role") == "assistant"
            # Some chunk must carry "Hello".
            assert any(
                json.loads(line[len("data: ") :])["choices"][0]["delta"].get("content") == "Hello"
                for line in chunk_lines
                if "Hello" in line
            )
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_429_maps_to_rate_limit_error() -> None:
    provider = AnthropicProvider(api_key="sk-ant-test")
    try:
        with respx.mock() as router:
            router.post(ANTHROPIC_URL).respond(
                429,
                headers={"retry-after": "3"},
                json={"error": {"type": "rate_limit_error", "message": "slow down"}},
            )
            with pytest.raises(ProviderRateLimitError) as exc_info:
                await provider.chat_completion(_make_req([{"role": "user", "content": "hi"}]))
            assert exc_info.value.status_code == 429
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_401_maps_to_auth_error() -> None:
    provider = AnthropicProvider(api_key="sk-ant-test")
    try:
        with respx.mock() as router:
            router.post(ANTHROPIC_URL).respond(
                401,
                json={"error": {"type": "authentication_error", "message": "bad key"}},
            )
            with pytest.raises(ProviderAuthError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "hi"}]))
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_503_maps_to_server_error() -> None:
    provider = AnthropicProvider(api_key="sk-ant-test")
    try:
        with respx.mock() as router:
            router.post(ANTHROPIC_URL).respond(
                503,
                json={"error": {"type": "overloaded_error", "message": "too busy"}},
            )
            with pytest.raises(ProviderServerError) as exc_info:
                await provider.chat_completion(_make_req([{"role": "user", "content": "hi"}]))
            assert exc_info.value.status_code == 503
    finally:
        await provider.aclose()


# ---------------------------------------------------------------------------
# Regression — 2026-05-01 fix (commit 0b2da28). Anthropic's Messages API
# rejects dotted model ids (``claude-sonnet-4.6`` → 404 not_found_error). Our
# catalog exposes the dotted id to clients (matches our pricing docs and
# OpenAI-style naming) but the upstream call MUST use dashes. If anyone
# "simplifies" the catalog by dropping ``upstream_id`` overrides, this fires.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("public_id", "expected_upstream"),
    [
        ("claude-sonnet-4.6", "claude-sonnet-4-6"),
        ("claude-haiku-4.5", "claude-haiku-4-5-20251001"),
        ("claude-opus-4.7", "claude-opus-4-7"),
    ],
)
@pytest.mark.asyncio
async def test_anthropic_upstream_id_uses_dashes_not_dots(
    public_id: str, expected_upstream: str
) -> None:
    spec = get_model(public_id)
    assert spec is not None, f"missing catalog entry: {public_id}"
    # Catalog-level invariant — translation present, dashed, no dots.
    assert spec.upstream_id == expected_upstream
    assert "." not in spec.upstream_id
    assert spec.upstream_id != spec.id

    # End-to-end — adapter actually sends the dashed id on the wire.
    provider = AnthropicProvider(api_key="sk-ant-test")
    body = _success_body("ok")
    body["model"] = expected_upstream
    try:
        with respx.mock(assert_all_called=True) as router:
            route = router.post(ANTHROPIC_URL).respond(200, json=body)
            req = ChatCompletionRequest(model=spec, messages=[{"role": "user", "content": "ping"}])
            await provider.chat_completion(req)
            sent = json.loads(route.calls.last.request.content)
            assert sent["model"] == expected_upstream
            assert "." not in sent["model"]
    finally:
        await provider.aclose()


def test_split_system_combines_multiple_systems() -> None:
    sys, rest = _split_system(
        [
            {"role": "system", "content": "a"},
            {"role": "system", "content": "b"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert sys == "a\n\nb"
    assert rest == [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# Sprint 9 Task 4 — usage.cache_read_input_tokens / cache_creation_input_tokens
# get surfaced on ChatCompletionUsage.cached_tokens. Already implemented; this
# test pins it against accidental drift.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_cached_tokens_surface_on_usage() -> None:
    provider = AnthropicProvider(api_key="sk-ant-test")
    body = _success_body("Cached reply")
    body["usage"] = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 800,  # 800 tokens served from cache
    }
    try:
        with respx.mock() as router:
            router.post(ANTHROPIC_URL).respond(200, json=body)
            resp = await provider.chat_completion(_make_req([{"role": "user", "content": "hi"}]))
            assert resp.usage.cached_tokens == 800
            # prompt_tokens stays the non-cached count + cache_creation.
            assert resp.usage.prompt_tokens == 100
            # OpenAI-shape mirror also carries it for clients reading raw.
            assert resp.raw["usage"]["prompt_tokens_details"]["cached_tokens"] == 800
    finally:
        await provider.aclose()
