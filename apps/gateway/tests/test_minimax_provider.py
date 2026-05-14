"""MiniMaxProvider tests via respx.

Surface mirrors test_moonshot_provider.py — MiniMax is OpenAI-compatible,
we subclass OpenAIProvider, so the things that can break are:

* base_url is api.minimaxi.chat (mind the trailing ``i``).
* upstream_id mapping (public ``minimax-m1`` → upstream ``MiniMax-M1``).
* streaming pass-through.
* audio_speech is disabled (MiniMax TTS is not OpenAI-shaped).
"""

from __future__ import annotations

import json

import pytest
import respx

from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ProviderAuthError,
)
from voltari_gateway.providers.minimax_provider import MiniMaxProvider
from voltari_gateway.router.catalog import get_model

MINIMAX_BASE = "https://api.minimaxi.chat/v1"
MINIMAX_CHAT_URL = f"{MINIMAX_BASE}/chat/completions"


def _model(model_id: str = "minimax-m1"):
    m = get_model(model_id)
    assert m is not None, f"catalog missing {model_id}"
    return m


def _make_req(messages, *, model_id: str = "minimax-m1", **kw) -> ChatCompletionRequest:
    return ChatCompletionRequest(model=_model(model_id), messages=messages, **kw)


def _success_body(model: str = "MiniMax-M1") -> dict:
    return {
        "id": "chatcmpl-mm-1",
        "object": "chat.completion",
        "created": 1730000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 6,
            "completion_tokens": 1,
            "total_tokens": 7,
        },
    }


@pytest.mark.asyncio
async def test_chat_completion_uses_minimax_base_url_and_upstream_id() -> None:
    """Sanity: requests go to api.minimaxi.chat, NOT api.openai.com,
    and we map public ``minimax-m1`` to upstream ``MiniMax-M1``."""
    provider = MiniMaxProvider(api_key="sk-mm")
    try:
        with respx.mock(assert_all_called=False) as router:
            openai_route = router.post("https://api.openai.com/v1/chat/completions")
            mm_route = router.post(MINIMAX_CHAT_URL).respond(200, json=_success_body())

            resp = await provider.chat_completion(_make_req([{"role": "user", "content": "hi"}]))

            assert openai_route.call_count == 0
            assert mm_route.call_count == 1
            sent = json.loads(mm_route.calls.last.request.content)
            assert sent["model"] == "MiniMax-M1"
            assert resp.model_id == "minimax-m1"
            assert resp.provider == "minimax"
            assert resp.usage.prompt_tokens == 6
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_chat_completion_stream_passes_through() -> None:
    provider = MiniMaxProvider(api_key="sk-mm")
    sse = (
        "data: "
        + json.dumps(
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "model": "MiniMax-M1",
                "choices": [{"index": 0, "delta": {"content": "h"}, "finish_reason": None}],
            }
        )
        + "\n\n"
        + "data: "
        + json.dumps(
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "model": "MiniMax-M1",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            }
        )
        + "\n\n"
        + "data: [DONE]\n\n"
    )
    try:
        with respx.mock() as router:
            router.post(MINIMAX_CHAT_URL).respond(
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
            assert "MiniMax-M1" in joined
            assert '"prompt_tokens": 3' in joined
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_audio_speech_raises_not_implemented() -> None:
    provider = MiniMaxProvider(api_key="sk-mm")
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
async def test_401_auth_error() -> None:
    provider = MiniMaxProvider(api_key="sk-mm")
    try:
        with respx.mock() as router:
            router.post(MINIMAX_CHAT_URL).respond(
                401, json={"error": {"message": "bad key", "type": "auth_error"}}
            )
            with pytest.raises(ProviderAuthError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


def test_provider_name_is_minimax() -> None:
    assert MiniMaxProvider.name == "minimax"


def test_default_base_url_is_minimax_v1() -> None:
    """Guard against accidental OpenAI base_url leaking through inheritance.

    ALSO guards against typoing minimaxi → minimax — easy mistake, the
    actual public domain has the trailing ``i``.
    """
    provider = MiniMaxProvider(api_key="sk-mm")
    try:
        assert str(provider._client.base_url).rstrip("/") == MINIMAX_BASE
        # explicit: catch a future "minimax.chat" typo
        assert "minimaxi.chat" in str(provider._client.base_url)
    finally:
        pass
