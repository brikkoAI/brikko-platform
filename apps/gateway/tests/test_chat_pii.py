"""Integration tests for PII-маскинг in /v1/chat/completions (Sprint 4 Поток M).

Strategy: stub provider records the messages it received → assert that
those messages are masked. Stub returns a response containing a
placeholder → assert the JSON returned to the user has the original
text restored.

Account-level flag, header opt-in, and body opt-in are all exercised.
"""

from __future__ import annotations

import pytest

from voltari_gateway.db.models import Account
from voltari_gateway.providers.base import (
    ChatCompletionResponse,
    ChatCompletionUsage,
)


def _stub_response_with_placeholder(
    req_model_id: str, placeholder: str = "<NAME_1>"
) -> ChatCompletionResponse:
    """Build a stub response that mentions a placeholder in its content,
    so unmask path can be verified."""
    return ChatCompletionResponse(
        raw={
            "id": "chatcmpl-pii-stub",
            "object": "chat.completion",
            "created": 1730000000,
            "model": req_model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"OK, I addressed your message to {placeholder}.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 10,
                "total_tokens": 22,
            },
        },
        usage=ChatCompletionUsage(prompt_tokens=12, completion_tokens=10, total_tokens=22),
        model_id=req_model_id,
        provider="openai",
    )


@pytest.mark.asyncio
async def test_pii_header_enables_masking_and_unmasks_response(
    client, app, api_key_fixture, redis_client
) -> None:
    """X-PII-Protect: true → email replaced by placeholder in outgoing prompt,
    then placeholder restored in response."""
    stub = app.state.openai_provider
    # Pre-set stub response to mention NAME_1 — we'll plant "Иванов Иван
    # Иванович" in the prompt and expect that exact name to come back.
    stub.next_response = _stub_response_with_placeholder("gpt-5.4-mini", "<NAME_1>")

    payload = {
        "model": "gpt-5.4-mini",
        "messages": [
            {
                "role": "user",
                "content": "Hello, my name is Иванов Иван Иванович, please reply.",
            }
        ],
    }
    resp = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers={
            **api_key_fixture.auth_header,
            "X-PII-Protect": "true",
        },
    )
    assert resp.status_code == 200, resp.text

    # 1. Provider received masked content.
    assert stub.last_request is not None
    sent_content = stub.last_request.messages[0]["content"]
    assert "Иванов Иван Иванович" not in sent_content
    assert "<NAME_1>" in sent_content

    # 2. Response shown to user has original name restored.
    body = resp.json()
    assistant_text = body["choices"][0]["message"]["content"]
    assert "<NAME_1>" not in assistant_text
    assert "Иванов Иван Иванович" in assistant_text


@pytest.mark.asyncio
async def test_pii_body_flag_enables_masking(client, app, api_key_fixture) -> None:
    stub = app.state.openai_provider
    stub.next_response = _stub_response_with_placeholder("gpt-5.4-mini", "<EMAIL_1>")

    payload = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "Reply to alice@example.io please."}],
        "pii_protect": True,
    }
    resp = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 200, resp.text

    sent_content = stub.last_request.messages[0]["content"]
    assert "alice@example.io" not in sent_content
    assert "<EMAIL_1>" in sent_content
    assistant_text = resp.json()["choices"][0]["message"]["content"]
    assert "alice@example.io" in assistant_text


@pytest.mark.asyncio
async def test_pii_account_flag_overrides_no_header(client, app, api_key_fixture, db) -> None:
    """Account.pii_masking_enabled=True → masking happens without per-request opt-in."""
    # Flip the account flag.
    account = await db.get(Account, api_key_fixture.account.id)
    account.pii_masking_enabled = True
    db.add(account)
    await db.commit()

    stub = app.state.openai_provider
    stub.next_response = _stub_response_with_placeholder("gpt-5.4-mini", "<PHONE_1>")

    payload = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "Call me at +79161234567."}],
    }
    resp = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers=api_key_fixture.auth_header,  # NO X-PII-Protect / pii_protect
    )
    assert resp.status_code == 200, resp.text

    sent = stub.last_request.messages[0]["content"]
    assert "+79161234567" not in sent
    assert "<PHONE_1>" in sent

    assistant_text = resp.json()["choices"][0]["message"]["content"]
    assert "+79161234567" in assistant_text


@pytest.mark.asyncio
async def test_pii_disabled_by_default(client, app, api_key_fixture) -> None:
    """Without account flag / header / body flag, prompt goes through unchanged."""
    stub = app.state.openai_provider
    # Default stub response is fine — we check the OUTGOING prompt.

    payload = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "Email is dev@x.io"}],
    }
    resp = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 200, resp.text

    sent = stub.last_request.messages[0]["content"]
    assert "dev@x.io" in sent  # unchanged
    assert "<EMAIL_" not in sent


@pytest.mark.asyncio
async def test_pii_no_pii_in_prompt_no_op(client, app, api_key_fixture) -> None:
    """Header enables, but if no PII in prompt — body passes through, no Redis row."""
    stub = app.state.openai_provider

    payload = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "Hello, what is the weather?"}],
    }
    resp = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers={**api_key_fixture.auth_header, "X-PII-Protect": "true"},
    )
    assert resp.status_code == 200, resp.text
    sent = stub.last_request.messages[0]["content"]
    assert sent == "Hello, what is the weather?"


@pytest.mark.asyncio
async def test_pii_header_false_value_does_not_enable(client, app, api_key_fixture) -> None:
    """Header value 'false'/'0' — masking off."""
    stub = app.state.openai_provider

    payload = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "Email a@b.io"}],
    }
    resp = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers={**api_key_fixture.auth_header, "X-PII-Protect": "false"},
    )
    assert resp.status_code == 200, resp.text
    sent = stub.last_request.messages[0]["content"]
    assert "a@b.io" in sent  # not masked


@pytest.mark.asyncio
async def test_pii_redis_unavailable_still_masks_outgoing(client, app, api_key_fixture) -> None:
    """Redis save fails silently — masking still happens; user just sees
    raw placeholder in response (degraded UX, no PII leak)."""
    from voltari_gateway.auth.middleware import set_redis

    set_redis(None)  # simulate Redis down for store

    stub = app.state.openai_provider
    stub.next_response = _stub_response_with_placeholder("gpt-5.4-mini", "<NAME_1>")

    # Pure Russian prompt — Natasha (Sprint 13 / Privacy v2) is monolingual,
    # mixed Latin/Cyrillic context can split the FIO triplet.  The
    # compliance contract being verified here is masking + round-trip,
    # not Natasha's mixed-script tokenisation.
    payload = {
        "model": "gpt-5.4-mini",
        "messages": [
            {"role": "user", "content": "Клиент Иванов Иван Петрович задал вопрос."},
        ],
    }
    resp = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers={**api_key_fixture.auth_header, "X-PII-Protect": "true"},
    )
    assert resp.status_code == 200, resp.text

    sent = stub.last_request.messages[0]["content"]
    # Outgoing prompt MUST be masked (this is the compliance guarantee).
    assert "Иванов Иван Петрович" not in sent
    assert "<NAME_1>" in sent
    # Without Redis the in-memory mapping is still used for unmask
    # (see _json_response — falls back to local mapping).
    assistant_text = resp.json()["choices"][0]["message"]["content"]
    assert "Иванов Иван Петрович" in assistant_text


@pytest.mark.asyncio
async def test_pii_multiple_types_in_one_request(client, app, api_key_fixture) -> None:
    """Email + phone + name in one prompt — all masked, all restored."""
    stub = app.state.openai_provider
    stub.next_response = ChatCompletionResponse(
        raw={
            "id": "chatcmpl-pii-multi",
            "object": "chat.completion",
            "created": 1730000000,
            "model": "gpt-5.4-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Got it: <NAME_1>, <EMAIL_1>, <PHONE_1>.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 12,
                "total_tokens": 24,
            },
        },
        usage=ChatCompletionUsage(prompt_tokens=12, completion_tokens=12, total_tokens=24),
        model_id="gpt-5.4-mini",
        provider="openai",
    )

    payload = {
        "model": "gpt-5.4-mini",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Иванов Иван Сергеевич писал на ivan@example.io "
                    "телефон +79161234567 запиши контакт."
                ),
            }
        ],
        "pii_protect": True,
    }
    resp = await client.post(
        "/v1/chat/completions", json=payload, headers=api_key_fixture.auth_header
    )
    assert resp.status_code == 200, resp.text

    text = resp.json()["choices"][0]["message"]["content"]
    assert "Иванов Иван Сергеевич" in text
    assert "ivan@example.io" in text
    assert "+79161234567" in text
    assert "<NAME" not in text
    assert "<EMAIL" not in text
    assert "<PHONE" not in text


def test_stream_split_placeholder_regression() -> None:
    """Regression (Sprint 13 / Privacy v2 — Phase 3, was V1.5 known limit):
    a placeholder split across two SSE chunks is now correctly unmasked.

    Exercises the chat.py-internal helper plus the StreamUnmasker pipe
    without the full ASGI flow — the integration of those two pieces is
    what changed; the SSE parsing/billing logic above is unchanged.
    """
    from voltari_gateway.api.chat import _stream_unmask_chunk
    from voltari_gateway.pii import PiiMapping, StreamUnmasker
    from voltari_gateway.pii.masker import mask_text

    mapping = PiiMapping()
    masked = mask_text("Иванов Иван", mapping)
    assert masked == "<NAME_1>"

    unmasker = StreamUnmasker(mapping=mapping)

    # Provider sent two chunks that split the placeholder mid-token.
    chunk1 = {"choices": [{"index": 0, "delta": {"content": "Привет, <NAM"}}]}
    chunk2 = {"choices": [{"index": 0, "delta": {"content": "E_1>!"}}]}

    _stream_unmask_chunk(chunk1, unmasker)
    _stream_unmask_chunk(chunk2, unmasker)
    tail = unmasker.flush()

    emitted = (
        chunk1["choices"][0]["delta"]["content"] + chunk2["choices"][0]["delta"]["content"] + tail
    )
    # No raw placeholder leaked, original surface form present.
    assert "<NAME_" not in emitted
    assert "Иванов Иван" in emitted
    assert emitted == "Привет, Иванов Иван!"
