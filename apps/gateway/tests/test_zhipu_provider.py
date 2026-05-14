"""ZhipuProvider tests via respx.

Surface mirrors test_moonshot_provider.py — Zhipu is OpenAI-compatible on
the v4 endpoint, we subclass OpenAIProvider, the things that can break:

* base_url is open.bigmodel.cn/api/paas/v4 (not api.openai.com).
* upstream_id mapping (public ``glm-4.5`` → upstream ``glm-4.5``).
* streaming pass-through.
* Direct Bearer auth — the ``<id>.<secret>`` API key is forwarded
  verbatim in the Authorization header (no JWT signing).
* audio_speech is disabled.
"""

from __future__ import annotations

import json

import pytest
import respx

from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ProviderRateLimitError,
)
from voltari_gateway.providers.zhipu_provider import ZhipuProvider
from voltari_gateway.router.catalog import get_model

ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
ZHIPU_CHAT_URL = f"{ZHIPU_BASE}/chat/completions"


def _model(model_id: str = "glm-4.5"):
    m = get_model(model_id)
    assert m is not None, f"catalog missing {model_id}"
    return m


def _make_req(messages, *, model_id: str = "glm-4.5", **kw) -> ChatCompletionRequest:
    return ChatCompletionRequest(model=_model(model_id), messages=messages, **kw)


def _success_body(model: str = "glm-4.5") -> dict:
    return {
        "id": "chatcmpl-zh-1",
        "object": "chat.completion",
        "created": 1730000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 5,
            "completion_tokens": 1,
            "total_tokens": 6,
        },
    }


@pytest.mark.asyncio
async def test_chat_completion_uses_zhipu_base_url_and_direct_bearer() -> None:
    """Sanity: requests go to open.bigmodel.cn/api/paas/v4, NOT api.openai.com.

    Verifies Direct Bearer auth: the ``<id>.<secret>`` API key is sent
    verbatim in the Authorization header — no JWT signing, no token
    exchange.
    """
    api_key = "abc123.deadbeef"  # Zhipu format is id.secret
    provider = ZhipuProvider(api_key=api_key)
    try:
        with respx.mock(assert_all_called=False) as router:
            openai_route = router.post("https://api.openai.com/v1/chat/completions")
            zh_route = router.post(ZHIPU_CHAT_URL).respond(200, json=_success_body())

            resp = await provider.chat_completion(_make_req([{"role": "user", "content": "hi"}]))

            assert openai_route.call_count == 0
            assert zh_route.call_count == 1
            sent = json.loads(zh_route.calls.last.request.content)
            assert sent["model"] == "glm-4.5"
            assert resp.model_id == "glm-4.5"
            assert resp.provider == "zhipu"
            # Direct Bearer: the api_key string goes through unmodified.
            auth_header = zh_route.calls.last.request.headers.get("authorization", "")
            assert auth_header == f"Bearer {api_key}", (
                f"expected Direct Bearer, got {auth_header!r}"
            )
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_chat_completion_stream_passes_through() -> None:
    provider = ZhipuProvider(api_key="abc.def")
    sse = (
        "data: "
        + json.dumps(
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "model": "glm-4.5",
                "choices": [{"index": 0, "delta": {"content": "o"}, "finish_reason": None}],
            }
        )
        + "\n\n"
        + "data: "
        + json.dumps(
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "model": "glm-4.5",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            }
        )
        + "\n\n"
        + "data: [DONE]\n\n"
    )
    try:
        with respx.mock() as router:
            router.post(ZHIPU_CHAT_URL).respond(
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
            assert "glm-4.5" in joined
            assert '"prompt_tokens": 2' in joined
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_audio_speech_raises_not_implemented() -> None:
    provider = ZhipuProvider(api_key="abc.def")
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
    provider = ZhipuProvider(api_key="abc.def")
    try:
        with respx.mock() as router:
            # Zhipu's error JSON shape uses string codes, not ints — but
            # _map_status_error keys off HTTP status, not body, so the
            # mapping still works.
            router.post(ZHIPU_CHAT_URL).respond(
                429,
                json={"error": {"code": "1234", "message": "rate"}},
                headers={"retry-after": "3"},
            )
            with pytest.raises(ProviderRateLimitError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


def test_provider_name_is_zhipu() -> None:
    assert ZhipuProvider.name == "zhipu"


def test_default_base_url_is_bigmodel_v4() -> None:
    """Guard against accidental OpenAI base_url leaking through inheritance."""
    provider = ZhipuProvider(api_key="abc.def")
    try:
        assert str(provider._client.base_url).rstrip("/") == ZHIPU_BASE
        # explicit: paas/v4 NOT v1 — Zhipu's OpenAI-compat lives on v4.
        assert "/api/paas/v4" in str(provider._client.base_url)
    finally:
        pass
