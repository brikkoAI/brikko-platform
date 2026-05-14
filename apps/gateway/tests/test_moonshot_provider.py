"""MoonshotProvider tests via respx.

Moonshot exposes an OpenAI-compatible chat surface, so the test surface
mirrors test_together_provider.py — focus on the things that can break
specifically because we subclass OpenAIProvider:

* base_url is api.moonshot.ai (not api.openai.com — guards against the
  inheritance chain leaking the parent's URL).
* upstream_id mapping (public ``kimi-k2`` → upstream
  ``kimi-k2-0711-preview``).
* streaming pass-through with [DONE] terminator.
* audio_speech is explicitly disabled (Moonshot has no TTS).
"""

from __future__ import annotations

import json

import pytest
import respx

from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ProviderRateLimitError,
)
from voltari_gateway.providers.moonshot_provider import MoonshotProvider
from voltari_gateway.router.catalog import get_model

MOONSHOT_BASE = "https://api.moonshot.ai/v1"
MOONSHOT_CHAT_URL = f"{MOONSHOT_BASE}/chat/completions"


def _model(model_id: str = "kimi-k2"):
    m = get_model(model_id)
    assert m is not None, f"catalog missing {model_id}"
    return m


def _make_req(messages, *, model_id: str = "kimi-k2", **kw) -> ChatCompletionRequest:
    return ChatCompletionRequest(model=_model(model_id), messages=messages, **kw)


def _success_body(model: str = "kimi-k2-0711-preview") -> dict:
    return {
        "id": "chatcmpl-ms-1",
        "object": "chat.completion",
        "created": 1730000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "你好"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": 2,
            "total_tokens": 10,
        },
    }


@pytest.mark.asyncio
async def test_chat_completion_uses_moonshot_base_url_and_upstream_id() -> None:
    """Sanity: requests go to api.moonshot.ai, NOT api.openai.com,
    and we map public ``kimi-k2`` to upstream ``kimi-k2-0711-preview``."""
    provider = MoonshotProvider(api_key="sk-ms")
    try:
        with respx.mock(assert_all_called=False) as router:
            openai_route = router.post("https://api.openai.com/v1/chat/completions")
            ms_route = router.post(MOONSHOT_CHAT_URL).respond(200, json=_success_body())

            resp = await provider.chat_completion(_make_req([{"role": "user", "content": "hi"}]))

            assert openai_route.call_count == 0
            assert ms_route.call_count == 1
            sent = json.loads(ms_route.calls.last.request.content)
            assert sent["model"] == "kimi-k2-0711-preview"
            assert resp.model_id == "kimi-k2"
            assert resp.provider == "moonshot"
            assert resp.usage.prompt_tokens == 8
            assert resp.usage.completion_tokens == 2
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_chat_completion_stream_passes_through() -> None:
    provider = MoonshotProvider(api_key="sk-ms")
    sse = (
        "data: "
        + json.dumps(
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "model": "kimi-k2-0711-preview",
                "choices": [{"index": 0, "delta": {"content": "你"}, "finish_reason": None}],
            }
        )
        + "\n\n"
        + "data: "
        + json.dumps(
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "model": "kimi-k2-0711-preview",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
            }
        )
        + "\n\n"
        + "data: [DONE]\n\n"
    )
    try:
        with respx.mock() as router:
            router.post(MOONSHOT_CHAT_URL).respond(
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
            assert "data: [DONE]\n\n" in joined
            assert "kimi-k2-0711-preview" in joined
            assert '"prompt_tokens": 4' in joined
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_audio_speech_raises_not_implemented() -> None:
    """Moonshot has no TTS; calling audio_speech must fail fast."""
    provider = MoonshotProvider(api_key="sk-ms")
    try:
        with pytest.raises(NotImplementedError):
            await provider.audio_speech(
                model="tts-1",
                input_text="hi",
                voice="alloy",
            )
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_429_rate_limit_propagates() -> None:
    provider = MoonshotProvider(api_key="sk-ms")
    try:
        with respx.mock() as router:
            router.post(MOONSHOT_CHAT_URL).respond(
                429,
                json={"error": {"message": "rate", "type": "rate_limit"}},
                headers={"retry-after": "2"},
            )
            with pytest.raises(ProviderRateLimitError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


def test_provider_name_is_moonshot() -> None:
    assert MoonshotProvider.name == "moonshot"


def test_default_base_url_is_moonshot_v1() -> None:
    """Guard against accidental OpenAI base_url leaking through inheritance."""
    provider = MoonshotProvider(api_key="sk-ms")
    try:
        assert str(provider._client.base_url).rstrip("/") == MOONSHOT_BASE
    finally:
        pass
