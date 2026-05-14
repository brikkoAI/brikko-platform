"""Direct unit tests for ``OpenAIProvider`` adapter.

The wider chat_completions tests use a stub provider; this module
exercises the OpenAI-specific code paths (kwargs building, error
mapping, streaming usage parsing) so the adapter coverage stops being
a blind spot.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIStatusError, APITimeoutError, RateLimitError

from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ProviderAuthError,
    ProviderClientError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)
from voltari_gateway.providers.openai_provider import (
    OpenAIProvider,
    _map_status_error,
)
from voltari_gateway.router.catalog import ModelSpec, ModelTier, Provider


def _model() -> ModelSpec:
    return ModelSpec(
        id="gpt-test",
        provider=Provider.OPENAI,
        tier=ModelTier.MID,
        input_price_kop_per_1k=10,
        cached_price_kop_per_1k=1,
        output_price_kop_per_1k=20,
        context_window=4096,
        latency_p50_ms=500,
        quality_score=70,
        supports_streaming=True,
        supports_tools=False,
        ru_legal=False,
    )


def _request(stream: bool = False, **extra: Any) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=_model(),
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
        top_p=0.95,
        max_tokens=128,
        stream=stream,
        stop=None,
        extra=extra,
    )


# ---------- _map_status_error ------------------------------------------------


def _make_status_error(
    status_code: int, *, headers: dict[str, str] | None = None
) -> APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code, headers=headers or {}, request=request)
    return APIStatusError(message=f"oops_{status_code}", response=response, body=None)


def test_map_status_error_401_returns_auth_error():
    err = _map_status_error(_make_status_error(401))
    assert isinstance(err, ProviderAuthError)


def test_map_status_error_403_returns_auth_error():
    err = _map_status_error(_make_status_error(403))
    assert isinstance(err, ProviderAuthError)


def test_map_status_error_429_returns_rate_limit_error_with_retry_after():
    err = _map_status_error(_make_status_error(429, headers={"retry-after": "5"}))
    assert isinstance(err, ProviderRateLimitError)
    assert err.retry_after_s == 5.0


def test_map_status_error_429_handles_invalid_retry_after_header():
    """A garbage Retry-After header must not crash the error-translation path."""
    err = _map_status_error(_make_status_error(429, headers={"retry-after": "soon"}))
    assert isinstance(err, ProviderRateLimitError)
    assert err.retry_after_s is None


def test_map_status_error_500_returns_server_error():
    err = _map_status_error(_make_status_error(503))
    assert isinstance(err, ProviderServerError)


def test_map_status_error_400_returns_client_error():
    err = _map_status_error(_make_status_error(400))
    assert isinstance(err, ProviderClientError)


def test_map_status_error_unknown_status_returns_generic():
    err = _map_status_error(_make_status_error(308))  # weird redirect
    assert isinstance(err, ProviderError)
    assert not isinstance(err, ProviderClientError)
    assert not isinstance(err, ProviderServerError)


# ---------- OpenAIProvider._build_kwargs -------------------------------------


def test_build_kwargs_passes_through_explicit_fields():
    kwargs = OpenAIProvider._build_kwargs(_request(), stream=False)
    assert kwargs["model"] == "gpt-test"
    assert kwargs["temperature"] == 0.7
    assert kwargs["top_p"] == 0.95
    assert kwargs["max_tokens"] == 128
    assert "max_completion_tokens" not in kwargs
    assert kwargs["stream"] is False
    assert "stream_options" not in kwargs  # only added when streaming


def _reasoning_model() -> ModelSpec:
    """Reasoning-class model that requires max_completion_tokens."""
    return ModelSpec(
        id="gpt-reasoning-test",
        provider=Provider.OPENAI,
        tier=ModelTier.PREMIUM,
        input_price_kop_per_1k=10,
        cached_price_kop_per_1k=1,
        output_price_kop_per_1k=20,
        context_window=4096,
        latency_p50_ms=500,
        quality_score=70,
        supports_streaming=True,
        supports_tools=False,
        ru_legal=False,
        requires_max_completion_tokens=True,
    )


def test_build_kwargs_translates_max_tokens_for_reasoning_models():
    """gpt-5.x / o3 / o4-mini reject ``max_tokens`` — translate to
    ``max_completion_tokens`` (OpenAI 2025-Q4 deprecation)."""
    req = ChatCompletionRequest(
        model=_reasoning_model(),
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
        top_p=0.95,
        max_tokens=256,
        stream=False,
        stop=None,
        extra={},
    )
    kwargs = OpenAIProvider._build_kwargs(req, stream=False)
    assert kwargs["max_completion_tokens"] == 256
    assert "max_tokens" not in kwargs


# ---------------------------------------------------------------------------
# Regression — 2026-05-01 fix (commit 0b2da28). OpenAI reasoning models
# (gpt-5.x / o3 / o4-mini) deprecated ``max_tokens`` and 400 with
# ``unsupported_parameter``; we must translate to ``max_completion_tokens``
# only when the spec opts in. Pre-reasoning models (gpt-4o etc.) keep the
# legacy field — the negative test guards against an over-eager translator
# that would break them.
# ---------------------------------------------------------------------------


def test_build_kwargs_keeps_max_tokens_when_flag_is_false():
    """Negative — ``requires_max_completion_tokens=False`` keeps the legacy
    field. Default ``_model()`` has the flag off."""
    kwargs = OpenAIProvider._build_kwargs(_request(), stream=False)
    assert kwargs["max_tokens"] == 128
    assert "max_completion_tokens" not in kwargs


@pytest.mark.parametrize(
    "model_id",
    [
        "gpt-5.4-mini",
        "gpt-5.4",
        "gpt-5",
        "gpt-5.5",
        "gpt-5.5-pro",
        "o3",
        "o4-mini",
    ],
)
def test_build_kwargs_translates_max_tokens_for_all_reasoning_catalog_models(
    model_id: str,
):
    """Every reasoning-class catalog entry must get the translation."""
    from voltari_gateway.router.catalog import get_model

    spec = get_model(model_id)
    assert spec is not None, f"missing catalog entry: {model_id}"
    assert spec.requires_max_completion_tokens is True

    req = ChatCompletionRequest(
        model=spec,
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
        top_p=0.95,
        max_tokens=512,
        stream=False,
        stop=None,
        extra={},
    )
    kwargs = OpenAIProvider._build_kwargs(req, stream=False)
    assert kwargs["max_completion_tokens"] == 512
    assert "max_tokens" not in kwargs


def test_build_kwargs_streaming_includes_usage_by_default():
    kwargs = OpenAIProvider._build_kwargs(_request(stream=True), stream=True)
    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}


def test_build_kwargs_streaming_preserves_caller_supplied_stream_options():
    kwargs = OpenAIProvider._build_kwargs(
        _request(stream=True, stream_options={"include_usage": False, "extra_field": "x"}),
        stream=True,
    )
    # Caller's setting wins because we only ``setdefault`` on include_usage.
    assert kwargs["stream_options"]["include_usage"] is False
    assert kwargs["stream_options"]["extra_field"] == "x"


def test_build_kwargs_forwards_extra_fields():
    kwargs = OpenAIProvider._build_kwargs(
        _request(stream=False, response_format={"type": "json_object"}, seed=42),
        stream=False,
    )
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["seed"] == 42


def test_build_kwargs_does_not_overwrite_canonical_fields():
    """``extra`` shouldn't clobber model/messages/temperature already set."""
    kwargs = OpenAIProvider._build_kwargs(
        _request(stream=False, model="evil-model", messages=[{"x": 1}]),
        stream=False,
    )
    assert kwargs["model"] == "gpt-test"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


# ---------- _wrap_error ------------------------------------------------------


def test_wrap_error_rate_limit():
    """RateLimitError → ProviderRateLimitError via status-error path."""
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(429, request=request)
    err = RateLimitError(message="too many", response=response, body=None)
    wrapped = OpenAIProvider._wrap_error(err)
    assert isinstance(wrapped, ProviderRateLimitError)


def test_wrap_error_timeout():
    err = APITimeoutError(request=httpx.Request("POST", "x"))
    wrapped = OpenAIProvider._wrap_error(err)
    assert isinstance(wrapped, ProviderTimeoutError)


def test_wrap_error_unexpected_returns_generic():
    err = ValueError("totally unrelated bug")
    wrapped = OpenAIProvider._wrap_error(err)
    assert isinstance(wrapped, ProviderError)
    assert "totally unrelated bug" in str(wrapped)


# ---------- chat_completion (full flow against a mocked SDK) -----------------


@pytest.mark.asyncio
async def test_chat_completion_returns_normalized_usage(monkeypatch):
    """The adapter must extract usage including ``cached_tokens`` from
    ``prompt_tokens_details``.
    """
    provider = OpenAIProvider(api_key="sk-test")

    class _FakeCompletion:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def model_dump(self) -> dict[str, Any]:
            return self._payload

    payload = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1730000000,
        "model": "gpt-test",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 30},
        },
    }
    fake_completion = _FakeCompletion(payload)
    create_mock = AsyncMock(return_value=fake_completion)
    monkeypatch.setattr(provider._client.chat.completions, "create", create_mock)

    resp = await provider.chat_completion(_request())
    assert resp.raw == payload
    assert resp.usage.prompt_tokens == 100
    assert resp.usage.completion_tokens == 50
    assert resp.usage.cached_tokens == 30
    assert resp.provider == "openai"
    create_mock.assert_called_once()


@pytest.mark.asyncio
async def test_chat_completion_raises_provider_timeout_on_sdk_timeout(monkeypatch):
    provider = OpenAIProvider(api_key="sk-test")
    err = APITimeoutError(request=httpx.Request("POST", "x"))
    monkeypatch.setattr(provider._client.chat.completions, "create", AsyncMock(side_effect=err))
    with pytest.raises(ProviderTimeoutError):
        await provider.chat_completion(_request())


@pytest.mark.asyncio
async def test_chat_completion_raises_auth_error_on_401(monkeypatch):
    provider = OpenAIProvider(api_key="sk-bogus")
    err = _make_status_error(401)
    monkeypatch.setattr(provider._client.chat.completions, "create", AsyncMock(side_effect=err))
    with pytest.raises(ProviderAuthError):
        await provider.chat_completion(_request())


# ---------- chat_completion_stream ------------------------------------------


@pytest.mark.asyncio
async def test_chat_completion_stream_yields_data_lines_and_done(monkeypatch):
    """SSE generator must emit each chunk as ``data: ...\\n\\n`` followed by
    ``data: [DONE]\\n\\n``.
    """
    provider = OpenAIProvider(api_key="sk-test")

    class _FakeChunk:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def model_dump(self) -> dict[str, Any]:
            return self._payload

    chunks = [
        _FakeChunk({"id": "1", "choices": [{"index": 0, "delta": {"content": "a"}}]}),
        _FakeChunk(
            {
                "id": "1",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ),
    ]

    class _FakeStream:
        def __init__(self, items: list[_FakeChunk]) -> None:
            self._items = items

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._items:
                raise StopAsyncIteration
            return self._items.pop(0)

    monkeypatch.setattr(
        provider._client.chat.completions,
        "create",
        AsyncMock(return_value=_FakeStream(chunks)),
    )

    gen = await provider.chat_completion_stream(_request(stream=True))
    received: list[bytes] = []
    async for line in gen:
        received.append(line)

    # 2 data chunks + final [DONE]
    assert len(received) == 3
    assert received[-1] == b"data: [DONE]\n\n"
    parsed = json.loads(received[0][len(b"data: ") :].decode())
    assert parsed["id"] == "1"


@pytest.mark.asyncio
async def test_chat_completion_stream_raises_on_pre_iterate_error(monkeypatch):
    """If the SDK fails BEFORE the first chunk, raise the wrapped ProviderError."""
    provider = OpenAIProvider(api_key="sk-test")
    err = _make_status_error(503)
    monkeypatch.setattr(provider._client.chat.completions, "create", AsyncMock(side_effect=err))
    with pytest.raises(ProviderServerError):
        await provider.chat_completion_stream(_request(stream=True))
