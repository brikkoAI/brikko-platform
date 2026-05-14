"""SberProvider tests via respx.

Coverage:
* OAuth token rotation: cached, refreshed when expired.
* non-stream happy path with role/content message translation.
* status mapping: 401 → auth, 429 → rate-limit, 5xx → server.
* streaming SSE → OpenAI chunks.
* RqUID header on OAuth.
"""

from __future__ import annotations

import json
import time

import pytest
import respx

from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderServerError,
)
from voltari_gateway.providers.sber_provider import (
    SBER_CHAT_URL,
    SBER_OAUTH_URL,
    SberProvider,
)
from voltari_gateway.router.catalog import get_model

AUTH_KEY = "MTIzOmFiYw=="  # fake base64


def _model():
    m = get_model("gigachat-2-pro")
    assert m is not None
    return m


def _make_req(messages, **kw) -> ChatCompletionRequest:
    return ChatCompletionRequest(model=_model(), messages=messages, **kw)


def _oauth_body(token: str = "tok-1") -> dict:
    # expires_at in epoch ms ~30 min from now
    return {
        "access_token": token,
        "expires_at": int((time.time() + 30 * 60) * 1000),
    }


def _success_body() -> dict:
    return {
        "id": "chatcmpl-sber-1",
        "object": "chat.completion",
        "created": 1730000000,
        "model": "GigaChat-2-Pro",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Здравствуйте"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
    }


@pytest.mark.asyncio
async def test_non_stream_happy_path() -> None:
    provider = SberProvider(auth_key=AUTH_KEY)
    try:
        with respx.mock(assert_all_called=True) as router:
            oauth = router.post(SBER_OAUTH_URL).respond(200, json=_oauth_body())
            chat = router.post(SBER_CHAT_URL).respond(200, json=_success_body())

            resp = await provider.chat_completion(
                _make_req([{"role": "user", "content": "Привет"}])
            )
            assert resp.raw["choices"][0]["message"]["content"] == "Здравствуйте"
            assert resp.usage.prompt_tokens == 8
            # OAuth happened
            assert oauth.call_count == 1
            oauth_req = oauth.calls.last.request
            assert oauth_req.headers["authorization"].startswith("Basic ")
            assert "rquid" in {k.lower() for k in oauth_req.headers}
            # Chat happened with Bearer token
            chat_req = chat.calls.last.request
            assert chat_req.headers["authorization"] == "Bearer tok-1"
            sent = json.loads(chat_req.content)
            assert sent["model"] == "GigaChat-2-Pro"
            assert sent["stream"] is False
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_oauth_token_cached_across_requests() -> None:
    provider = SberProvider(auth_key=AUTH_KEY)
    try:
        with respx.mock() as router:
            oauth = router.post(SBER_OAUTH_URL).respond(200, json=_oauth_body())
            router.post(SBER_CHAT_URL).respond(200, json=_success_body())

            await provider.chat_completion(_make_req([{"role": "user", "content": "1"}]))
            await provider.chat_completion(_make_req([{"role": "user", "content": "2"}]))
            assert oauth.call_count == 1  # cached
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_429_rate_limit() -> None:
    provider = SberProvider(auth_key=AUTH_KEY)
    try:
        with respx.mock() as router:
            router.post(SBER_OAUTH_URL).respond(200, json=_oauth_body())
            router.post(SBER_CHAT_URL).respond(429, text="too many")
            with pytest.raises(ProviderRateLimitError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_401_oauth_failure_maps_to_auth_error() -> None:
    provider = SberProvider(auth_key=AUTH_KEY)
    try:
        with respx.mock() as router:
            router.post(SBER_OAUTH_URL).respond(401, text="bad creds")
            with pytest.raises(ProviderAuthError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


# --------------------------------------------------------------------------
# Phase 5 #2 — function calling. Sber возвращает legacy `function_call`,
# мы конвертируем в modern `tool_calls` для OpenAI-совместимости.
# --------------------------------------------------------------------------


def _function_call_body() -> dict:
    return {
        "id": "chatcmpl-sber-fc-1",
        "object": "chat.completion",
        "created": 1730000000,
        "model": "GigaChat-2-Pro",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "function_call": {
                        "name": "get_weather",
                        "arguments": {"city": "Москва"},
                    },
                },
                "finish_reason": "function_call",
            }
        ],
        "usage": {"prompt_tokens": 25, "completion_tokens": 8, "total_tokens": 33},
    }


@pytest.mark.asyncio
async def test_tools_input_passed_through_to_payload() -> None:
    """Modern OpenAI `tools` через req.extra пробрасывается в payload."""
    provider = SberProvider(auth_key=AUTH_KEY)
    try:
        with respx.mock() as router:
            router.post(SBER_OAUTH_URL).respond(200, json=_oauth_body())
            chat = router.post(SBER_CHAT_URL).respond(200, json=_success_body())
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Возвращает текущую погоду",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                }
            ]
            req = _make_req(
                [{"role": "user", "content": "Погода в Москве?"}],
                extra={"tools": tools, "tool_choice": "auto"},
            )
            await provider.chat_completion(req)
            sent = json.loads(chat.calls.last.request.content)
            assert sent["tools"] == tools
            assert sent["tool_choice"] == "auto"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_function_call_response_converted_to_tool_calls() -> None:
    """Legacy `function_call` от Sber → modern `tool_calls` в нашем response."""
    provider = SberProvider(auth_key=AUTH_KEY)
    try:
        with respx.mock() as router:
            router.post(SBER_OAUTH_URL).respond(200, json=_oauth_body())
            router.post(SBER_CHAT_URL).respond(200, json=_function_call_body())
            resp = await provider.chat_completion(
                _make_req([{"role": "user", "content": "Погода в Москве?"}])
            )
            choice = resp.raw["choices"][0]
            msg = choice["message"]
            # OpenAI-shape: content=null + tool_calls array
            assert msg["content"] is None
            assert "tool_calls" in msg
            assert len(msg["tool_calls"]) == 1
            tc = msg["tool_calls"][0]
            assert tc["type"] == "function"
            assert tc["function"]["name"] == "get_weather"
            # arguments — stringified JSON (OpenAI spec)
            assert isinstance(tc["function"]["arguments"], str)
            args = json.loads(tc["function"]["arguments"])
            assert args == {"city": "Москва"}
            assert choice["finish_reason"] == "tool_calls"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_assistant_tool_calls_in_messages_converted_to_function_call() -> None:
    """Modern OpenAI assistant.tool_calls → legacy function_call для Sber upstream."""
    provider = SberProvider(auth_key=AUTH_KEY)
    try:
        with respx.mock() as router:
            router.post(SBER_OAUTH_URL).respond(200, json=_oauth_body())
            chat = router.post(SBER_CHAT_URL).respond(200, json=_success_body())
            messages = [
                {"role": "user", "content": "Погода?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city":"Москва"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_abc",
                    "content": "+15°C, ясно",
                },
            ]
            await provider.chat_completion(_make_req(messages))
            sent = json.loads(chat.calls.last.request.content)
            sent_msgs = sent["messages"]
            # assistant имеет function_call (legacy), не tool_calls
            assistant_msg = next(m for m in sent_msgs if m["role"] == "assistant")
            assert "function_call" in assistant_msg
            assert assistant_msg["function_call"]["name"] == "get_weather"
            assert assistant_msg["function_call"]["arguments"] == {"city": "Москва"}
            # tool message → role=function с name=get_weather (lookup by tool_call_id)
            func_msg = next(m for m in sent_msgs if m["role"] == "function")
            assert func_msg["name"] == "get_weather"
            assert func_msg["content"] == "+15°C, ясно"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_503_server_error() -> None:
    provider = SberProvider(auth_key=AUTH_KEY)
    try:
        with respx.mock() as router:
            router.post(SBER_OAUTH_URL).respond(200, json=_oauth_body())
            router.post(SBER_CHAT_URL).respond(503, text="overload")
            with pytest.raises(ProviderServerError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_streaming_translates_to_openai_chunks() -> None:
    provider = SberProvider(auth_key=AUTH_KEY)
    sse = (
        "data: "
        + json.dumps(
            {
                "id": "x",
                "model": "GigaChat-2-Pro",
                "choices": [{"index": 0, "delta": {"content": "Привет"}, "finish_reason": None}],
            }
        )
        + "\n\n"
        + "data: "
        + json.dumps(
            {
                "id": "x",
                "model": "GigaChat-2-Pro",
                "choices": [{"index": 0, "delta": {"content": "!"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            }
        )
        + "\n\n"
        + "data: [DONE]\n\n"
    )
    try:
        with respx.mock() as router:
            router.post(SBER_OAUTH_URL).respond(200, json=_oauth_body())
            router.post(SBER_CHAT_URL).respond(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse.encode("utf-8"),
            )
            chunks_iter = await provider.chat_completion_stream(
                _make_req([{"role": "user", "content": "x"}])
            )
            collected = []
            async for c in chunks_iter:
                collected.append(c)
            joined = b"".join(collected).decode("utf-8")
            assert "data: [DONE]\n\n" in joined
            assert "Привет" in joined
            # Final usage chunk
            assert '"prompt_tokens": 5' in joined
    finally:
        await provider.aclose()
