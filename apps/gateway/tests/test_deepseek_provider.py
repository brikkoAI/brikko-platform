"""DeepSeekProvider tests via respx.

DeepSeek is an OpenAI-compat upstream so the surface is small. We focus
on:
* upstream_id mapping (``deepseek-v3.2-chat`` → ``deepseek-chat``).
* prompt cache extraction (``prompt_cache_hit_tokens`` →
  ``prompt_tokens_details.cached_tokens``).
* streaming pass-through with cache mapping.
* status-code → Provider*Error mapping reuses OpenAI mapping.
"""

from __future__ import annotations

import json

import pytest
import respx

from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ProviderAuthError,
    ProviderClientError,
    ProviderRateLimitError,
)
from voltari_gateway.providers.deepseek_provider import DeepSeekProvider
from voltari_gateway.router.catalog import get_model

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"


def _model():
    m = get_model("deepseek-v3.2-chat")
    assert m is not None
    return m


def _make_req(messages, **kw) -> ChatCompletionRequest:
    return ChatCompletionRequest(model=_model(), messages=messages, **kw)


def _success_body() -> dict:
    return {
        "id": "chatcmpl-ds-1",
        "object": "chat.completion",
        "created": 1730000000,
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "пр"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "total_tokens": 105,
            "prompt_cache_hit_tokens": 60,
            "prompt_cache_miss_tokens": 40,
        },
    }


@pytest.mark.asyncio
async def test_non_stream_uses_upstream_id() -> None:
    provider = DeepSeekProvider(api_key="sk-ds")
    try:
        with respx.mock(assert_all_called=True) as router:
            route = router.post(DEEPSEEK_URL).respond(200, json=_success_body())
            resp = await provider.chat_completion(_make_req([{"role": "user", "content": "hi"}]))
            sent = json.loads(route.calls.last.request.content)
            assert sent["model"] == "deepseek-chat"  # upstream alias
            assert resp.model_id == "deepseek-v3.2-chat"  # public id
            assert resp.raw["model"] == "deepseek-v3.2-chat"
            assert resp.usage.cached_tokens == 60
            assert resp.usage.prompt_tokens == 100
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_streaming_remaps_cache_field() -> None:
    provider = DeepSeekProvider(api_key="sk-ds")
    sse = (
        "data: "
        + json.dumps(
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "model": "deepseek-chat",
                "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
            }
        )
        + "\n\n"
        + "data: "
        + json.dumps(
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "model": "deepseek-chat",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 1,
                    "total_tokens": 11,
                    "prompt_cache_hit_tokens": 8,
                },
            }
        )
        + "\n\n"
        + "data: [DONE]\n\n"
    )
    try:
        with respx.mock() as router:
            router.post(DEEPSEEK_URL).respond(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse.encode("utf-8"),
            )
            chunks_iter = await provider.chat_completion_stream(
                _make_req([{"role": "user", "content": "hi"}])
            )
            collected = []
            async for c in chunks_iter:
                collected.append(c)
            joined = b"".join(collected).decode("utf-8")
            # Cache hit must surface as cached_tokens; model must be mapped back.
            assert '"cached_tokens": 8' in joined
            assert '"deepseek-v3.2-chat"' in joined
            assert "data: [DONE]\n\n" in joined
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_429_rate_limit_propagates() -> None:
    provider = DeepSeekProvider(api_key="sk-ds")
    try:
        with respx.mock() as router:
            router.post(DEEPSEEK_URL).respond(
                429,
                json={"error": {"message": "rate", "type": "rate_limit"}},
                headers={"retry-after": "1"},
            )
            with pytest.raises(ProviderRateLimitError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_401_auth_error() -> None:
    provider = DeepSeekProvider(api_key="sk-ds")
    try:
        with respx.mock() as router:
            router.post(DEEPSEEK_URL).respond(
                401, json={"error": {"message": "bad key", "type": "auth_error"}}
            )
            with pytest.raises(ProviderAuthError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_400_client_error() -> None:
    provider = DeepSeekProvider(api_key="sk-ds")
    try:
        with respx.mock() as router:
            router.post(DEEPSEEK_URL).respond(
                400, json={"error": {"message": "bad request", "type": "invalid"}}
            )
            with pytest.raises(ProviderClientError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_extra_fields_forwarded() -> None:
    provider = DeepSeekProvider(api_key="sk-ds")
    try:
        with respx.mock() as router:
            route = router.post(DEEPSEEK_URL).respond(200, json=_success_body())
            req = _make_req(
                [{"role": "user", "content": "x"}],
                extra={"response_format": {"type": "json_object"}},
            )
            await provider.chat_completion(req)
            sent = json.loads(route.calls.last.request.content)
            assert sent["response_format"] == {"type": "json_object"}
    finally:
        await provider.aclose()
