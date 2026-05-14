"""TogetherProvider tests via respx.

Together.ai is OpenAI-compat so the surface mirrors test_deepseek_provider.py.
We focus on:

* upstream_id mapping (public ``llama-3.3-70b`` → upstream
  ``meta-llama/Llama-3.3-70B-Instruct-Turbo``).
* base_url is api.together.xyz (NOT api.openai.com — guards against
  the inheritance chain leaking the parent's URL).
* streaming pass-through with [DONE] terminator.
* image_generate inherits from OpenAIProvider unchanged.
* audio_speech is explicitly disabled (Together has no TTS).
* status-code mapping reuses OpenAI taxonomy.
"""

from __future__ import annotations

import json

import pytest
import respx

from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ProviderAuthError,
    ProviderRateLimitError,
)
from voltari_gateway.providers.together_provider import TogetherProvider
from voltari_gateway.router.catalog import get_model

TOGETHER_BASE = "https://api.together.xyz/v1"
TOGETHER_CHAT_URL = f"{TOGETHER_BASE}/chat/completions"
TOGETHER_IMAGES_URL = f"{TOGETHER_BASE}/images/generations"


def _model(model_id: str = "llama-3.3-70b"):
    m = get_model(model_id)
    assert m is not None, f"catalog missing {model_id}"
    return m


def _make_req(messages, *, model_id: str = "llama-3.3-70b", **kw) -> ChatCompletionRequest:
    return ChatCompletionRequest(model=_model(model_id), messages=messages, **kw)


def _success_body(model: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo") -> dict:
    return {
        "id": "chatcmpl-tg-1",
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
            "prompt_tokens": 12,
            "completion_tokens": 5,
            "total_tokens": 17,
        },
    }


# ---------- chat_completion --------------------------------------------------


@pytest.mark.asyncio
async def test_chat_completion_uses_together_base_url_and_upstream_id() -> None:
    """Sanity: requests go to api.together.xyz, NOT api.openai.com,
    and we map the public model id to the Together upstream id."""
    provider = TogetherProvider(api_key="sk-tg")
    try:
        with respx.mock(assert_all_called=False) as router:
            # Block any accidental call to OpenAI's URL — must hit Together.
            openai_route = router.post("https://api.openai.com/v1/chat/completions")
            tg_route = router.post(TOGETHER_CHAT_URL).respond(200, json=_success_body())

            resp = await provider.chat_completion(_make_req([{"role": "user", "content": "hi"}]))

            assert openai_route.call_count == 0
            assert tg_route.call_count == 1
            sent = json.loads(tg_route.calls.last.request.content)
            assert sent["model"] == "meta-llama/Llama-3.3-70B-Instruct-Turbo"
            assert resp.model_id == "llama-3.3-70b"
            assert resp.provider == "together"
            assert resp.usage.prompt_tokens == 12
            assert resp.usage.completion_tokens == 5
    finally:
        await provider.aclose()


# ---------- chat_completion_stream -------------------------------------------


@pytest.mark.asyncio
async def test_chat_completion_stream_passes_through() -> None:
    provider = TogetherProvider(api_key="sk-tg")
    sse = (
        "data: "
        + json.dumps(
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                "choices": [{"index": 0, "delta": {"content": "he"}, "finish_reason": None}],
            }
        )
        + "\n\n"
        + "data: "
        + json.dumps(
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
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
            router.post(TOGETHER_CHAT_URL).respond(
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
            # Inherited OpenAI streaming does NOT remap the model id (only
            # DeepSeek does). Together's upstream id propagates verbatim.
            assert "Llama-3.3-70B-Instruct-Turbo" in joined
            assert '"prompt_tokens": 4' in joined
    finally:
        await provider.aclose()


# ---------- image_generate (FLUX) --------------------------------------------


@pytest.mark.asyncio
async def test_image_generate_flux_pro() -> None:
    """image_generate is inherited from OpenAIProvider; verify it points
    at the Together base URL when called on a TogetherProvider."""
    provider = TogetherProvider(api_key="sk-tg")
    try:
        with respx.mock() as router:
            payload = {
                "created": 1730000000,
                "data": [{"b64_json": "iVBOR..."}],
            }
            route = router.post(TOGETHER_IMAGES_URL).respond(200, json=payload)
            resp = await provider.image_generate(
                model="black-forest-labs/FLUX.1-pro",
                prompt="a sunny meadow",
                n=1,
                size="1024x1024",
            )
            assert route.call_count == 1
            sent = json.loads(route.calls.last.request.content)
            assert sent["model"] == "black-forest-labs/FLUX.1-pro"
            assert sent["prompt"] == "a sunny meadow"
            assert sent["n"] == 1
            assert sent["size"] == "1024x1024"
            assert resp == payload
    finally:
        await provider.aclose()


# ---------- audio_speech is disabled -----------------------------------------


@pytest.mark.asyncio
async def test_audio_speech_raises_not_implemented() -> None:
    """Together has no TTS; calling audio_speech must fail fast."""
    provider = TogetherProvider(api_key="sk-tg")
    try:
        with pytest.raises(NotImplementedError):
            await provider.audio_speech(
                model="tts-1",
                input_text="hi",
                voice="alloy",
            )
    finally:
        await provider.aclose()


# ---------- error mapping ----------------------------------------------------


@pytest.mark.asyncio
async def test_429_rate_limit_propagates() -> None:
    provider = TogetherProvider(api_key="sk-tg")
    try:
        with respx.mock() as router:
            router.post(TOGETHER_CHAT_URL).respond(
                429,
                json={"error": {"message": "rate", "type": "rate_limit"}},
                headers={"retry-after": "2"},
            )
            with pytest.raises(ProviderRateLimitError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_401_auth_error() -> None:
    provider = TogetherProvider(api_key="sk-tg")
    try:
        with respx.mock() as router:
            router.post(TOGETHER_CHAT_URL).respond(
                401, json={"error": {"message": "bad key", "type": "auth_error"}}
            )
            with pytest.raises(ProviderAuthError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


# ---------- name + identity --------------------------------------------------


def test_provider_name_is_together() -> None:
    assert TogetherProvider.name == "together"


def test_default_base_url_is_together_v1() -> None:
    """Guard against accidental OpenAI base_url leaking through inheritance."""
    provider = TogetherProvider(api_key="sk-tg")
    try:
        # AsyncOpenAI normalises base_url with a trailing slash; strip
        # for comparison.
        assert str(provider._client.base_url).rstrip("/") == TOGETHER_BASE
    finally:
        # aclose is async; tests that need closed state should await it.
        # Here we only inspected the SDK client URL — leave the resource
        # cleanup to the event loop on test teardown.
        pass
