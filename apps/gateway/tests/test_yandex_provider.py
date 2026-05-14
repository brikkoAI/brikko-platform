"""YandexProvider tests via respx.

Coverage:
* non-stream happy path with API key auth.
* IAM-token rotation: cached, refreshed when expired.
* model_uri construction from public id.
* status mapping: 401 → auth, 429 → rate-limit, 503 → server.
* synthetic stream emits [DONE] + final usage.
* request shape: messages translated to ``{role, text}``.
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
from voltari_gateway.providers.yandex_provider import (
    YANDEX_COMPLETION_URL,
    YANDEX_IAM_URL,
    YandexProvider,
    _model_uri,
)
from voltari_gateway.router.catalog import get_model

FOLDER_ID = "b1ggwwxyz"


def _model():
    m = get_model("yandexgpt-5.1-pro")
    assert m is not None
    return m


def _make_req(messages, **kw) -> ChatCompletionRequest:
    return ChatCompletionRequest(model=_model(), messages=messages, **kw)


def _success_body(text: str = "Привет!") -> dict:
    return {
        "result": {
            "alternatives": [
                {
                    "message": {"role": "assistant", "text": text},
                    "status": "ALTERNATIVE_STATUS_FINAL",
                }
            ],
            "usage": {
                "inputTextTokens": "20",
                "completionTokens": "5",
                "totalTokens": "25",
            },
            "modelVersion": "rc",
        }
    }


@pytest.mark.asyncio
async def test_non_stream_with_api_key() -> None:
    provider = YandexProvider(folder_id=FOLDER_ID, api_key="AQVN-test")
    try:
        with respx.mock(assert_all_called=True) as router:
            route = router.post(YANDEX_COMPLETION_URL).respond(200, json=_success_body())
            resp = await provider.chat_completion(
                _make_req(
                    [
                        {"role": "system", "content": "Будь краток."},
                        {"role": "user", "content": "Привет"},
                    ]
                )
            )
            assert resp.usage.prompt_tokens == 20
            assert resp.usage.completion_tokens == 5
            assert resp.raw["choices"][0]["message"]["content"] == "Привет!"
            assert resp.raw["choices"][0]["finish_reason"] == "stop"

            req = route.calls.last.request
            # Auth via Api-Key, no IAM round-trip.
            assert req.headers["authorization"].startswith("Api-Key ")
            sent = json.loads(req.content)
            assert sent["modelUri"].startswith(f"gpt://{FOLDER_ID}/yandexgpt/")
            assert sent["messages"] == [
                {"role": "system", "text": "Будь краток."},
                {"role": "user", "text": "Привет"},
            ]
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_iam_token_cached_and_reused() -> None:
    """First call fetches IAM, second hits cache (no second IAM POST)."""
    expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 12 * 3600))

    def jwt_factory() -> str:
        return "fake.jwt.token"

    provider = YandexProvider(folder_id=FOLDER_ID, iam_jwt_factory=jwt_factory)
    try:
        with respx.mock() as router:
            iam_route = router.post(YANDEX_IAM_URL).respond(
                200, json={"iamToken": "iam-1", "expiresAt": expires_at}
            )
            comp_route = router.post(YANDEX_COMPLETION_URL).respond(200, json=_success_body())

            await provider.chat_completion(_make_req([{"role": "user", "content": "1"}]))
            await provider.chat_completion(_make_req([{"role": "user", "content": "2"}]))
            assert iam_route.call_count == 1  # cached!
            assert comp_route.call_count == 2
            for call in comp_route.calls:
                assert call.request.headers["authorization"] == "Bearer iam-1"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_429_rate_limit() -> None:
    provider = YandexProvider(folder_id=FOLDER_ID, api_key="AQVN-test")
    try:
        with respx.mock() as router:
            router.post(YANDEX_COMPLETION_URL).respond(429, json={"error": {"message": "throttle"}})
            with pytest.raises(ProviderRateLimitError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_401_auth_error() -> None:
    provider = YandexProvider(folder_id=FOLDER_ID, api_key="AQVN-test")
    try:
        with respx.mock() as router:
            router.post(YANDEX_COMPLETION_URL).respond(401, json={"error": {"message": "bad key"}})
            with pytest.raises(ProviderAuthError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_503_server_error() -> None:
    provider = YandexProvider(folder_id=FOLDER_ID, api_key="AQVN-test")
    try:
        with respx.mock() as router:
            router.post(YANDEX_COMPLETION_URL).respond(503, text="busy")
            with pytest.raises(ProviderServerError):
                await provider.chat_completion(_make_req([{"role": "user", "content": "x"}]))
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_synthetic_stream_emits_done_and_usage() -> None:
    provider = YandexProvider(folder_id=FOLDER_ID, api_key="AQVN-test")
    try:
        with respx.mock() as router:
            router.post(YANDEX_COMPLETION_URL).respond(200, json=_success_body("Hi"))
            chunks_iter = await provider.chat_completion_stream(
                _make_req([{"role": "user", "content": "x"}])
            )
            collected = []
            async for c in chunks_iter:
                collected.append(c)
            text = b"".join(collected).decode("utf-8")
            assert "data: [DONE]\n\n" in text
            # Final-usage chunk present.
            assert '"prompt_tokens": 20' in text
    finally:
        await provider.aclose()


# ----------------------------------------------------------------------------
# Phase 5 #2 — function calling. Yandex native API: tools на корне body,
# response через `toolCallList.toolCalls[]` с arguments как object.
# ----------------------------------------------------------------------------


def _tool_call_body(name: str = "calculator", args: dict | None = None) -> dict:
    return {
        "result": {
            "alternatives": [
                {
                    "message": {
                        "role": "assistant",
                        "text": "",
                        "toolCallList": {
                            "toolCalls": [
                                {
                                    "name": name,
                                    "arguments": args or {"expression": "2+2"},
                                }
                            ]
                        },
                    },
                    "status": "ALTERNATIVE_STATUS_FINAL",
                }
            ],
            "usage": {
                "inputTextTokens": "30",
                "completionTokens": "10",
                "totalTokens": "40",
            },
        }
    }


@pytest.mark.asyncio
async def test_tools_input_passed_as_yandex_format() -> None:
    """OpenAI tools=[{type:function, function:{...}}] → Yandex [{function:{...}}]."""
    provider = YandexProvider(folder_id=FOLDER_ID, api_key="AQVN-test")
    try:
        with respx.mock() as router:
            chat = router.post(YANDEX_COMPLETION_URL).respond(200, json=_success_body("ok"))
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "description": "Evaluates arithmetic",
                        "parameters": {
                            "type": "object",
                            "properties": {"expression": {"type": "string"}},
                            "required": ["expression"],
                        },
                        "strict": True,
                    },
                }
            ]
            await provider.chat_completion(
                _make_req(
                    [{"role": "user", "content": "2+2?"}],
                    extra={"tools": tools, "tool_choice": "auto"},
                )
            )
            sent = json.loads(chat.calls.last.request.content)
            assert "tools" in sent
            assert sent["tools"][0]["function"]["name"] == "calculator"
            assert sent["tools"][0]["function"]["strict"] is True
            # Yandex не использует "type" wrapper — только {function: {...}}
            assert "type" not in sent["tools"][0]
            assert sent["toolChoice"] == "auto"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_tool_call_response_converted_to_openai_shape() -> None:
    """Yandex toolCallList → OpenAI tool_calls. arguments-object → JSON-string."""
    provider = YandexProvider(folder_id=FOLDER_ID, api_key="AQVN-test")
    try:
        with respx.mock() as router:
            router.post(YANDEX_COMPLETION_URL).respond(200, json=_tool_call_body())
            resp = await provider.chat_completion(_make_req([{"role": "user", "content": "2+2?"}]))
            choice = resp.raw["choices"][0]
            msg = choice["message"]
            assert msg["content"] is None
            assert "tool_calls" in msg and len(msg["tool_calls"]) == 1
            tc = msg["tool_calls"][0]
            assert tc["type"] == "function"
            assert tc["function"]["name"] == "calculator"
            # OpenAI spec: arguments — stringified JSON.
            assert isinstance(tc["function"]["arguments"], str)
            assert json.loads(tc["function"]["arguments"]) == {"expression": "2+2"}
            assert choice["finish_reason"] == "tool_calls"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_assistant_tool_calls_converted_to_yandex_toolCallList() -> None:  # noqa: N802
    """Modern assistant.tool_calls → Yandex message.toolCallList в payload."""
    provider = YandexProvider(folder_id=FOLDER_ID, api_key="AQVN-test")
    try:
        with respx.mock() as router:
            chat = router.post(YANDEX_COMPLETION_URL).respond(200, json=_success_body())
            messages = [
                {"role": "user", "content": "2+2?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "calculator",
                                "arguments": '{"expression":"2+2"}',
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_abc", "content": "4"},
            ]
            await provider.chat_completion(_make_req(messages))
            sent = json.loads(chat.calls.last.request.content)
            sent_msgs = sent["messages"]
            # assistant message с toolCallList (legacy → Yandex format)
            assistant_msg = next((m for m in sent_msgs if "toolCallList" in m), None)
            assert assistant_msg is not None
            assert assistant_msg["role"] == "assistant"
            calls = assistant_msg["toolCallList"]["toolCalls"]
            assert len(calls) == 1
            assert calls[0]["name"] == "calculator"
            # Yandex принимает arguments как object (не stringified)
            assert calls[0]["arguments"] == {"expression": "2+2"}
            # tool result message → assistant с toolResultList
            result_msg = next((m for m in sent_msgs if "toolResultList" in m), None)
            assert result_msg is not None
            assert result_msg["toolResultList"]["toolResults"][0]["name"] == "calculator"
            assert result_msg["toolResultList"]["toolResults"][0]["content"] == "4"
    finally:
        await provider.aclose()


def test_model_uri_uses_folder_and_alias() -> None:
    uri = _model_uri(FOLDER_ID, "yandexgpt-5.1-pro")
    assert uri == f"gpt://{FOLDER_ID}/yandexgpt/rc"
    assert _model_uri(FOLDER_ID, "yandexgpt-5-lite") == f"gpt://{FOLDER_ID}/yandexgpt-lite/rc"


def test_model_uri_passthrough_for_full_uri() -> None:
    assert _model_uri(FOLDER_ID, "gpt://other/abc/v1") == "gpt://other/abc/v1"
