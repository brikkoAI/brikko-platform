"""GoogleProvider tests.

google-genai is mocked at the SDK boundary (we don't have a stable HTTP
endpoint to mock — the SDK swaps base_urls between v1/v1beta freely).
We replace the client's ``aio.models`` namespace with a stub that
records calls and returns fixed objects.

Coverage:
* non-stream → OpenAI-shape envelope.
* function_call response → tool_calls.
* tiered pricing applied via model.expected_cost_kop after a long input.
* streaming yields openai chunks + final usage + DONE.
* error mapping via genai_errors.APIError.
* tools translation: OpenAI tools → Gemini function_declarations.
"""

from __future__ import annotations

import json
import types
from collections.abc import AsyncIterator

import pytest

from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ProviderRateLimitError,
)
from voltari_gateway.router.catalog import get_model

# Skip the whole module if google-genai is not installed in the venv.
genai = pytest.importorskip("google.genai")
from voltari_gateway.providers import google_provider as gp  # noqa: E402


def _model():
    m = get_model("gemini-3.1-pro")
    assert m is not None
    return m


def _make_req(messages, **kw) -> ChatCompletionRequest:
    return ChatCompletionRequest(model=_model(), messages=messages, **kw)


# ----- Stub client objects -----------------------------------------------------


class _StubUsageMeta:
    def __init__(self, prompt: int = 10, completion: int = 4, cached: int = 0) -> None:
        self.prompt_token_count = prompt
        self.candidates_token_count = completion
        self.cached_content_token_count = cached


class _StubFinishReason:
    def __init__(self, name: str = "STOP") -> None:
        self.name = name


class _StubContent:
    def __init__(self, parts):
        self.parts = parts


class _StubCandidate:
    def __init__(self, parts, finish_reason="STOP"):
        self.content = _StubContent(parts)
        self.finish_reason = _StubFinishReason(finish_reason)


class _StubResponse:
    def __init__(self, candidates, usage=None):
        self.candidates = candidates
        self.usage_metadata = usage


class _StubModelsAio:
    """Stub for ``client.aio.models``."""

    def __init__(self) -> None:
        self.last_kwargs: dict | None = None
        self.next_response: _StubResponse | None = None
        self.next_stream_chunks: list[_StubResponse] | None = None
        self.next_error: Exception | None = None

    async def generate_content(self, **kw):
        self.last_kwargs = kw
        if self.next_error is not None:
            raise self.next_error
        return self.next_response

    async def generate_content_stream(self, **kw):
        self.last_kwargs = kw
        if self.next_error is not None:
            raise self.next_error
        chunks = self.next_stream_chunks or []

        async def _it() -> AsyncIterator[_StubResponse]:
            for c in chunks:
                yield c

        return _it()


@pytest.fixture
def stub_provider(monkeypatch):
    """Build a GoogleProvider whose ``_client`` is a stub.

    google-genai >=1.0 made ``Client.aio`` a read-only property, so we
    can no longer poke ``provider._client.aio = ...``. Instead, we
    replace the whole ``_client`` attribute with a SimpleNamespace
    exposing the same shape (``client.aio.models``).
    """
    provider = gp.GoogleProvider(api_key="g-test")
    stub_models = _StubModelsAio()
    fake_client = types.SimpleNamespace(aio=types.SimpleNamespace(models=stub_models))
    monkeypatch.setattr(provider, "_client", fake_client)
    return provider, stub_models


@pytest.mark.asyncio
async def test_non_stream_happy_path(stub_provider) -> None:
    provider, stub = stub_provider
    stub.next_response = _StubResponse(
        [_StubCandidate(parts=[{"text": "Привет!"}])],
        usage=_StubUsageMeta(prompt=10, completion=2),
    )
    try:
        resp = await provider.chat_completion(_make_req([{"role": "user", "content": "ping"}]))
        assert resp.raw["choices"][0]["message"]["content"] == "Привет!"
        assert resp.raw["choices"][0]["finish_reason"] == "stop"
        assert resp.usage.prompt_tokens == 10
        assert resp.usage.completion_tokens == 2
        # Verify the SDK was called with the upstream id (preview-suffixed
        # while Gemini 3.x has no stable alias on v1beta — see catalog.py).
        assert stub.last_kwargs["model"] == "gemini-3.1-pro-preview"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_function_call_translates_to_tool_calls(stub_provider) -> None:
    provider, stub = stub_provider
    stub.next_response = _StubResponse(
        [
            _StubCandidate(
                parts=[{"function_call": {"name": "get_weather", "args": {"city": "Moscow"}}}]
            )
        ],
        usage=_StubUsageMeta(),
    )
    try:
        resp = await provider.chat_completion(_make_req([{"role": "user", "content": "weather?"}]))
        msg = resp.raw["choices"][0]["message"]
        assert msg["content"] is None
        assert msg["tool_calls"][0]["function"]["name"] == "get_weather"
        assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"city": "Moscow"}
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_streaming_emits_openai_chunks(stub_provider) -> None:
    provider, stub = stub_provider
    stub.next_stream_chunks = [
        _StubResponse([_StubCandidate(parts=[{"text": "Hi"}])]),
        _StubResponse([_StubCandidate(parts=[{"text": " there"}])]),
        _StubResponse(
            [_StubCandidate(parts=[], finish_reason="STOP")],
            usage=_StubUsageMeta(prompt=5, completion=2),
        ),
    ]
    try:
        chunks_iter = await provider.chat_completion_stream(
            _make_req([{"role": "user", "content": "x"}])
        )
        collected = []
        async for c in chunks_iter:
            collected.append(c)
        joined = b"".join(collected).decode("utf-8")
        assert "data: [DONE]\n\n" in joined
        assert "Hi" in joined
        assert "there" in joined
        # Final-usage chunk.
        assert '"prompt_tokens": 5' in joined
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_apierror_maps_to_provider_error(stub_provider) -> None:
    provider, stub = stub_provider
    # google-genai >=1.0 changed APIError's signature to ``(code, response)``
    # where ``response`` must be a ``requests.Response`` or a
    # ``ReplayResponse``. Build a minimal Response so the constructor can
    # parse JSON body without touching the network.
    import requests

    fake_resp = requests.Response()
    fake_resp.status_code = 429
    fake_resp._content = b'{"error": {"message": "throttle", "status": "RESOURCE_EXHAUSTED"}}'
    err = gp.genai_errors.APIError(429, fake_resp)
    stub.next_error = err
    try:
        with pytest.raises(ProviderRateLimitError):
            await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


# ---------------------------------------------------------------------------
# Regression — 2026-05-01 fix (commit 32e6d3b). Gemini 3.x family has no
# stable v1beta alias yet (Google still ships them as ``-preview``). If
# anyone "simplifies" the catalog by dropping ``upstream_id``, the SDK 404s.
# Pin both 3.x ids on -preview at catalog AND wire level.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("public_id", "expected_upstream"),
    [
        ("gemini-3-flash", "gemini-3-flash-preview"),
        ("gemini-3.1-pro", "gemini-3.1-pro-preview"),
    ],
)
@pytest.mark.asyncio
async def test_gemini_upstream_id_pinned_to_preview_suffix(
    stub_provider, public_id: str, expected_upstream: str
) -> None:
    spec = get_model(public_id)
    assert spec is not None, f"missing catalog entry: {public_id}"
    # Catalog invariant — preview suffix present, upstream_id != id.
    assert spec.upstream_id == expected_upstream
    assert spec.upstream_id.endswith("-preview")
    assert spec.upstream_id != spec.id

    # End-to-end — adapter forwards the suffixed id to the SDK.
    provider, stub = stub_provider
    stub.next_response = _StubResponse(
        [_StubCandidate(parts=[{"text": "ok"}])],
        usage=_StubUsageMeta(),
    )
    req = ChatCompletionRequest(model=spec, messages=[{"role": "user", "content": "ping"}])
    await provider.chat_completion(req)
    assert stub.last_kwargs["model"] == expected_upstream
    assert stub.last_kwargs["model"].endswith("-preview")


def test_tiered_pricing_applies_above_threshold() -> None:
    """``gemini-3.1-pro`` charges 4 USD/M (32 kop/1k) above 200k tokens of input."""
    m = _model()
    # 100k tokens input → base rate (16 kop/1k).
    in_kop, _, out_kop = m.effective_pricing(100_000)
    assert in_kop == 16
    assert out_kop == 96  # 12 USD/M = 96 kop/1k base
    # 250k tokens → tier-2 (32 kop/1k input, 192 kop/1k output).
    in_kop2, _, out_kop2 = m.effective_pricing(250_000)
    assert in_kop2 == 32
    assert out_kop2 == 192
    # Cost reflects tier:
    base_cost = m.expected_cost_kop(100_000, 1_000)
    tiered_cost = m.expected_cost_kop(250_000, 1_000)
    assert tiered_cost > base_cost * 2


def test_convert_tools_to_gemini_shape() -> None:
    out = gp._convert_tools_to_gemini(
        [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ]
    )
    assert len(out) == 1
    decls = out[0]["function_declarations"]
    assert decls[0]["name"] == "get_weather"
    assert decls[0]["parameters"]["properties"]["city"]["type"] == "string"


# ---------------------------------------------------------------------------
# Sprint 9 Task 4 — usage_metadata.cached_content_token_count surfaces on
# ChatCompletionUsage.cached_tokens. Already implemented; this test pins it.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_cached_tokens_surface_on_usage(stub_provider) -> None:
    provider, stub = stub_provider
    stub.next_response = _StubResponse(
        [_StubCandidate(parts=[{"text": "ok"}])],
        usage=_StubUsageMeta(prompt=120, completion=20, cached=80),
    )
    try:
        resp = await provider.chat_completion(_make_req([{"role": "user", "content": "ping"}]))
        assert resp.usage.cached_tokens == 80
        assert resp.raw["usage"]["prompt_tokens_details"]["cached_tokens"] == 80
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_gemini_response_schema_set_when_strict_json(stub_provider) -> None:
    """JSON Schema strict mode (Task 3 wiring) makes the adapter set
    response_mime_type and response_schema on the SDK call."""
    provider, stub = stub_provider
    stub.next_response = _StubResponse(
        [_StubCandidate(parts=[{"text": '{"answer":"x"}'}])],
        usage=_StubUsageMeta(),
    )
    try:
        rf = {
            "type": "json_schema",
            "json_schema": {
                "name": "answer",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                },
            },
        }
        await provider.chat_completion(
            _make_req([{"role": "user", "content": "ping"}], extra={"response_format": rf})
        )
        cfg = stub.last_kwargs["config"]
        assert cfg["response_mime_type"] == "application/json"
        assert cfg["response_schema"]["type"] == "OBJECT"
        assert cfg["response_schema"]["properties"]["answer"]["type"] == "STRING"
    finally:
        await provider.aclose()
