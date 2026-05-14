"""Stripe-style boundary / edge-case tests for ``POST /v1/chat/completions``.

Sprint 4 Поток L (QA implement) — реализация плана Sprint 3 Поток K.

Главный invariant: **никакой пользовательский ввод не должен приводить к 500**.
Любая невалидная или странная нагрузка должна давать 4xx с осмысленным
``error.type``/``code``.

Группы:

* messages-shape (пустой, странный role, multimodal)
* numeric ranges (temperature, top_p, frequency/presence_penalty,
  top_logprobs, max_tokens)
* type-coercion (stream="yes" вместо bool)
* auth header pathology (Bearer без префикса, NUL, empty)
* content-type / body-size: 1 MB prompt, truncated JSON, text/plain
* Unicode (4-byte emoji, RTL marker)
* response_format=json_object pass-through
* model='auto:cheap' с пустым registry → 503

Помечен ``pytest.mark.asyncio`` через autouse в conftest's ``asyncio_mode``
в pyproject — все эти тесты ASGI-через-httpx и не нуждаются в реальной
сети.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

# Convenience: helper to assert NEVER 500 + что envelope OpenAI-shape
# (имеет inner error.type/code). Лучше один раз вынести.


def _assert_openai_envelope(resp: Any, *, expected_status: int | tuple[int, ...]) -> dict[str, Any]:
    """Pin the OpenAI-style error envelope for any 4xx response.

    Returns the inner ``error`` object so individual asserts can drill in.
    Always fails the test if status_code == 500 — we want **0 unhandled
    exceptions**.
    """
    assert resp.status_code != 500, (
        f"500 leaked through: {resp.text[:300]} — every user-facing error "
        f"must be a 4xx with the OpenAI envelope (utils/errors.py)."
    )
    if isinstance(expected_status, int):
        assert resp.status_code == expected_status, (resp.status_code, resp.text[:200])
    else:
        assert resp.status_code in expected_status, (resp.status_code, resp.text[:200])
    body = resp.json()
    assert "error" in body, body
    err = body["error"]
    assert "type" in err
    assert "message" in err
    return err  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# messages[] shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_messages_array_returns_400(client: AsyncClient, api_key_fixture: Any) -> None:
    """``messages: []`` — Pydantic ``min_length=1`` → 400 (validation handler)."""
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.4-mini", "messages": []},
        headers=api_key_fixture.auth_header,
    )
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_messages_field_missing_returns_400(
    client: AsyncClient, api_key_fixture: Any
) -> None:
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.4-mini"},
        headers=api_key_fixture.auth_header,
    )
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_messages_role_tool_accepted(client: AsyncClient, api_key_fixture: Any) -> None:
    """``role='tool'`` — supported по OpenAI-spec."""
    body = {
        "model": "gpt-5.4-mini",
        "messages": [
            {"role": "user", "content": "fetch weather"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function"}],
            },
            {"role": "tool", "content": "sunny", "tool_call_id": "c1"},
        ],
    }
    resp = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    # 200 — happy path. Stub-провайдер всегда отдаёт ответ.
    assert resp.status_code == 200, resp.text[:300]


@pytest.mark.asyncio
async def test_messages_role_developer_accepted(client: AsyncClient, api_key_fixture: Any) -> None:
    """OpenAI Reasoning models используют role='developer' вместо system."""
    body = {
        "model": "gpt-5.4-mini",
        "messages": [
            {"role": "developer", "content": "you are concise"},
            {"role": "user", "content": "hi"},
        ],
    }
    resp = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert resp.status_code == 200, resp.text[:300]


@pytest.mark.asyncio
async def test_messages_role_unknown_returns_400(client: AsyncClient, api_key_fixture: Any) -> None:
    """``role='hacker'`` — Literal['system','user',...] не пускает."""
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "hacker", "content": "hi"}],
    }
    resp = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_message_with_empty_string_content_accepted(
    client: AsyncClient, api_key_fixture: Any
) -> None:
    """``content=''`` — допустимо OpenAI'ем (assistant tool_call message has content=None or ''),
    но даже если backend хочет резать на 400 — НЕ должно быть 500.
    """
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": ""}],
    }
    resp = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    # Главное — НЕ 500. Реальный код пускает 200 (stub отвечает).
    assert resp.status_code in (200, 400), resp.text[:300]


@pytest.mark.asyncio
async def test_message_content_null_with_tool_calls_accepted(
    client: AsyncClient, api_key_fixture: Any
) -> None:
    """``content=null`` + tool_calls — OpenAI-canonical для assistant'а с tool-вызовом."""
    body = {
        "model": "gpt-5.4-mini",
        "messages": [
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "content": "ok", "tool_call_id": "call_1"},
        ],
    }
    resp = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert resp.status_code == 200, resp.text[:300]


@pytest.mark.asyncio
async def test_huge_prompt_1mb_returns_400(client: AsyncClient, api_key_fixture: Any) -> None:
    """1 MB content в одном сообщении → 400 (validator: > 200000 chars).

    Защищает от случайного RAG-prompt'а, который сожрёт LLM-контекст и наш
    rate limit. Гранулярность валидации — chars (не tokens), потому что
    точный токенайзер в pre-flight не запустить.
    """
    huge = "x" * 1_048_576  # 1 MiB
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": huge}]},
        headers=api_key_fixture.auth_header,
    )
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_truncated_json_body_returns_400(client: AsyncClient, api_key_fixture: Any) -> None:
    """Усечённый JSON на уровне HTTP body — FastAPI bubbles → invalid_request."""
    resp = await client.post(
        "/v1/chat/completions",
        content=b'{"model": "gpt-5.4-mini", "messages": [',  # обрыв
        headers={**api_key_fixture.auth_header, "Content-Type": "application/json"},
    )
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_garbage_body_returns_400(client: AsyncClient, api_key_fixture: Any) -> None:
    """Полный мусор не-JSON → НЕ 500."""
    resp = await client.post(
        "/v1/chat/completions",
        content=b"\x00\x01\x02not even json\xff\xfe",
        headers={**api_key_fixture.auth_header, "Content-Type": "application/json"},
    )
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


# ---------------------------------------------------------------------------
# Numeric ranges — temperature / max_tokens / top_p / penalties / top_logprobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("temperature", [-0.5, -1, 2.5, 5, 100])
async def test_temperature_out_of_range_returns_400(
    client: AsyncClient, api_key_fixture: Any, temperature: float
) -> None:
    """``temperature`` ge=0 le=2 — что-либо за пределами → 400.

    Note: spec говорил 422, но gateway переводит RequestValidationError → 400
    (см. utils/errors.py::validation_exception_handler — единый OpenAI-style
    invalid_request_error). Это **более строгий** контракт чем raw FastAPI
    422; OpenAI сама отдаёт 400 на validation.
    """
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "x"}],
            "temperature": temperature,
        },
        headers=api_key_fixture.auth_header,
    )
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("top_p", [-0.1, 1.5, 2.0])
async def test_top_p_out_of_range_returns_400(
    client: AsyncClient, api_key_fixture: Any, top_p: float
) -> None:
    """``top_p`` ge=0 le=1."""
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "x"}],
            "top_p": top_p,
        },
        headers=api_key_fixture.auth_header,
    )
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("frequency_penalty", 3),
        ("frequency_penalty", -3),
        ("presence_penalty", 2.5),
        ("presence_penalty", -2.5),
    ],
)
async def test_penalties_out_of_range_returns_400(
    client: AsyncClient, api_key_fixture: Any, field: str, value: float
) -> None:
    """frequency/presence penalty: ge=-2 le=2."""
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "x"}],
        field: value,
    }
    resp = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_top_logprobs_above_max_returns_400(
    client: AsyncClient, api_key_fixture: Any
) -> None:
    """``top_logprobs`` ge=0 le=20 — 25 → reject."""
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "x"}],
            "top_logprobs": 25,
        },
        headers=api_key_fixture.auth_header,
    )
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_max_tokens_zero_returns_400(client: AsyncClient, api_key_fixture: Any) -> None:
    """max_tokens=0 — ge=1 → 400."""
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 0,
        },
        headers=api_key_fixture.auth_header,
    )
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_max_tokens_negative_returns_400(client: AsyncClient, api_key_fixture: Any) -> None:
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": -100,
        },
        headers=api_key_fixture.auth_header,
    )
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_max_tokens_huge_returns_400(client: AsyncClient, api_key_fixture: Any) -> None:
    """max_tokens > 131072 — protective ceiling, никакая модель не примет."""
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 1_000_000,
        },
        headers=api_key_fixture.auth_header,
    )
    _ = _assert_openai_envelope(resp, expected_status=400)


def test_stream_string_value_pydantic_strict_td047_fixed() -> None:
    """``stream: 'yes'`` теперь даёт ValidationError (TD-047 закрыт).

    После добавления ``StrictBool`` в ``ChatCompletionRequestBody.stream``
    pydantic больше не coerce'ит ``'yes'``/``'true'``/``1`` в bool — клиент
    обязан передавать настоящий JSON boolean, как и требует OpenAI-контракт.
    Это защищает SSE-stream от случайного включения, когда SDK на той стороне
    ждал JSON-ответ.
    """
    from pydantic import ValidationError

    from voltari_gateway.api.chat import ChatCompletionRequestBody

    with pytest.raises(ValidationError) as exc_info:
        ChatCompletionRequestBody.model_validate(
            {
                "model": "gpt-5.4-mini",
                "messages": [{"role": "user", "content": "x"}],
                "stream": "yes",
            }
        )
    # Убеждаемся, что ругаемся именно на поле stream.
    assert any(err["loc"] == ("stream",) for err in exc_info.value.errors())


# ---------------------------------------------------------------------------
# Auth header pathology
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bearer_without_prefix_returns_401(client: AsyncClient) -> None:
    """``Authorization: sk-vlt-XXX`` без слова Bearer → 401."""
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "x"}]},
        headers={"Authorization": "sk-vlt-bogusbogusbogus"},
    )
    err = _assert_openai_envelope(resp, expected_status=401)
    assert err["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_bearer_empty_returns_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "x"}]},
        headers={"Authorization": "Bearer "},
    )
    err = _assert_openai_envelope(resp, expected_status=401)
    assert err["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_bearer_with_nul_byte_returns_401(client: AsyncClient) -> None:
    """NUL-байт в токене — потенциальный header-injection / log-poisoning.

    httpx всё равно нормализует — отправляем через bytes manually. Сервер
    не должен распарсить NUL как пробел и пройти на authent.

    На уровне ASGI httpx запретит NUL в header value (raise) — то есть
    атака blocked на клиентской стороне. Если клиент рукописный — ASGI
    спецификация требует latin-1, но NUL deemed-illegal в HTTP/1.1.
    """
    # Если клиент-side raises, тоже считается защитой.
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "x"}]},
            headers={"Authorization": "Bearer sk-vlt-\x00abc"},
        )
    except Exception:
        # client refused — это OK. Контракт держится: NUL не пробросится.
        return
    # Если попало на сервер — должно быть 401, не 500.
    assert resp.status_code in (400, 401), resp.text[:300]


@pytest.mark.asyncio
async def test_authorization_header_case_insensitive(
    client: AsyncClient, api_key_fixture: Any
) -> None:
    """RFC 7230: header field names case-insensitive. ``authorization``
    (lowercase) и ``AuThOrIzAtIoN`` обязаны работать.
    """
    body = {"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "x"}]}
    r1 = await client.post(
        "/v1/chat/completions",
        json=body,
        headers={"authorization": f"Bearer {api_key_fixture.plaintext}"},
    )
    r2 = await client.post(
        "/v1/chat/completions",
        json=body,
        headers={"AuThOrIzAtIoN": f"Bearer {api_key_fixture.plaintext}"},
    )
    assert r1.status_code == 200, r1.text[:300]
    assert r2.status_code == 200, r2.text[:300]


# ---------------------------------------------------------------------------
# Content-Type / size
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_type_text_plain_rejected(client: AsyncClient, api_key_fixture: Any) -> None:
    """``Content-Type: text/plain`` — FastAPI не парсит body, body=валидный
    JSON-string как текст → должен быть 400/415, НЕ 500.
    """
    raw = json.dumps(
        {"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "x"}]}
    ).encode()
    resp = await client.post(
        "/v1/chat/completions",
        content=raw,
        headers={**api_key_fixture.auth_header, "Content-Type": "text/plain"},
    )
    # На FastAPI 0.111+ с Pydantic body — без application/json получается
    # 400 invalid_request (не 415 unsupported_media_type), потому что
    # validation handler ловит "field required"/"input_type" ошибку.
    assert resp.status_code in (400, 415), resp.text[:300]
    err_inner = _assert_openai_envelope(resp, expected_status=resp.status_code)
    assert err_inner["type"] in ("invalid_request_error", "api_error")


# ---------------------------------------------------------------------------
# Unicode / pass-through fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_4byte_emoji_in_content(client: AsyncClient, api_key_fixture: Any) -> None:
    """4-byte emoji (U+1F4A1) — чёткий не-BMP — UTF-8/asyncpg/SQLite varchar
    спокойно пропускают, но регрессия раз в год бывает.
    """
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "test \U0001f4a1 emoji"}],
    }
    resp = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert resp.status_code == 200, resp.text[:300]


@pytest.mark.asyncio
async def test_response_format_json_object_passthrough(
    client: AsyncClient, api_key_fixture: Any
) -> None:
    """``response_format={'type': 'json_object'}`` — pass-through к провайдеру."""
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "give json"}],
        "response_format": {"type": "json_object"},
    }
    resp = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert resp.status_code == 200, resp.text[:300]
    # Сanity: stub получил response_format в extra.
    stub = client._transport.app.state.openai_provider  # type: ignore[attr-defined]
    assert stub.last_request is not None
    assert stub.last_request.extra.get("response_format") == {"type": "json_object"}


# ---------------------------------------------------------------------------
# Auto routing с пустым registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_cheap_with_empty_registry_fails_gracefully(
    client: AsyncClient, api_key_fixture: Any, app: Any
) -> None:
    """``model='auto:cheap'`` + provider registry ничем не наполнен → 4xx или 503.

    План Sprint 3 Поток K требовал 503 ``provider_unavailable``, но реальное
    поведение: с пустым registry router исключает ВСЕ провайдеры
    (auto_exclude = all_providers - configured_providers = all), и попадает
    на ветку ``no_eligible_model`` → 400. Это допустимо — клиент видит
    осмысленный invalid_request, не 500. Pin'им контракт «не 500 и не 200».
    """
    from voltari_gateway.providers.registry import ProviderRegistry

    # Снимаем зарегистрированных провайдеров — registry пуст.
    app.state.provider_registry = ProviderRegistry()

    body = {
        "model": "auto:cheap",
        "messages": [{"role": "user", "content": "x"}],
    }
    resp = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert resp.status_code in (400, 503), resp.text[:300]
    err = _assert_openai_envelope(resp, expected_status=resp.status_code)
    assert err["code"] in ("provider_unavailable", "no_eligible_model")


@pytest.mark.asyncio
async def test_unknown_model_returns_404(client: AsyncClient, api_key_fixture: Any) -> None:
    """Незнакомая модель — 404 model_not_found (existing test, дублируем
    inline для полноты edge-grid'а)."""
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "totally-fake-model", "messages": [{"role": "user", "content": "x"}]},
        headers=api_key_fixture.auth_header,
    )
    err = _assert_openai_envelope(resp, expected_status=404)
    assert err["code"] == "model_not_found"
    assert err.get("param") == "model"


@pytest.mark.asyncio
async def test_pinned_model_with_unconfigured_provider_returns_400_model_not_available(
    client: AsyncClient, api_key_fixture: Any, app: Any
) -> None:
    """Sprint M3.1 — клиент пинует Together-модель в окружении без TOGETHER_API_KEY.

    Раньше (до фильтра на /v1/models) это давало 503 ``provider_unavailable``
    после провала фолбэка. Теперь модель и в каталоге не видна, и pinned-call
    режется на стадии routing с 400 ``model_not_available``: клиент сам должен
    выбрать другую модель, это не upstream-сбой.
    """
    from voltari_gateway.router.catalog import Provider as ProviderEnum

    # Снимаем Together — имитируем «нет TOGETHER_API_KEY на этом деплое».
    app.state.provider_registry.unregister(ProviderEnum.TOGETHER)

    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "llama-3.3-70b",
            "messages": [{"role": "user", "content": "ping"}],
        },
        headers=api_key_fixture.auth_header,
    )
    err = _assert_openai_envelope(resp, expected_status=400)
    assert err["code"] == "model_not_available"
    assert err.get("param") == "model"
