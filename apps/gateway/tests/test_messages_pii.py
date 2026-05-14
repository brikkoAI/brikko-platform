"""Integration tests for PII masking in /v1/messages (Sprint 13 / Privacy v2 — Phase 4).

Coverage parity with ``test_chat_pii.py`` for the Anthropic-shape endpoint:

* Header opt-in (``X-PII-Protect: true``) masks outgoing prompt and unmasks
  the Anthropic ``content[].text`` block in the response.
* Body field opt-in (``"pii_protect": true``) does the same.
* Account-level ``pii_masking_enabled=True`` triggers masking without per-
  request flags (parity with chat — Anthropic-shape doesn't have a separate
  retrieval-semantics carve-out).
* Default (no flags) → prompt forwarded unchanged.
* ``system`` field is masked too (Anthropic ships it as a top-level param).
* Streaming path: ``content_block_delta.delta.text`` is unmasked through
  the StreamUnmasker carry buffer.

Strategy: respx-mock the real Anthropic upstream, plant a placeholder
inside the mocked response, then assert (a) the upstream received masked
bytes, (b) the response shown to the client has the original surface form
restored.
"""

from __future__ import annotations

import json

import pytest
import respx

from voltari_gateway.db.models import Account
from voltari_gateway.providers.anthropic_provider import AnthropicProvider
from voltari_gateway.router.catalog import Provider as ProviderEnum

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def _anthropic_response_with_placeholder(text: str) -> dict:
    """Anthropic Messages success body whose ``content[0].text`` contains
    a placeholder we expect the unmask path to restore."""
    return {
        "id": "msg_pii_stub",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 12,
            "output_tokens": 7,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


# ---------------------------------------------------------------------------
# Header opt-in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_header_masks_prompt_and_unmasks_response(
    client, api_key_fixture, app, redis_client
):
    """X-PII-Protect: true → name replaced by placeholder upstream,
    placeholder restored in the response shown to the client."""
    real_anthropic = AnthropicProvider(api_key="sk-ant-test")
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, real_anthropic)
    try:
        with respx.mock(assert_all_called=True) as router_mock:
            route = router_mock.post(ANTHROPIC_URL).respond(
                200,
                json=_anthropic_response_with_placeholder("Принял, передам сообщение <NAME_1>."),
            )
            r = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4.6",
                    "max_tokens": 100,
                    "messages": [
                        {
                            "role": "user",
                            "content": ("Передай Иванов Иван Иванович что встреча в 15:00."),
                        }
                    ],
                },
                headers={
                    **api_key_fixture.auth_header,
                    "X-PII-Protect": "true",
                },
            )
            assert r.status_code == 200, r.text

            # 1. Upstream Anthropic received masked content.
            sent = json.loads(route.calls.last.request.content)
            sent_user_content = sent["messages"][0]["content"]
            assert "Иванов Иван Иванович" not in sent_user_content
            assert "<NAME_1>" in sent_user_content

            # 2. Response shown to client has the original surface form
            #    restored in the Anthropic ``content[].text`` block.
            data = r.json()
            assistant_text = data["content"][0]["text"]
            assert "<NAME_1>" not in assistant_text
            assert "Иванов Иван Иванович" in assistant_text
    finally:
        await real_anthropic.aclose()


# ---------------------------------------------------------------------------
# Body field opt-in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_body_flag_enables_masking(client, api_key_fixture, app, redis_client):
    """``"pii_protect": true`` in body → masking on (no header needed)."""
    real_anthropic = AnthropicProvider(api_key="sk-ant-test")
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, real_anthropic)
    try:
        with respx.mock(assert_all_called=True) as router_mock:
            route = router_mock.post(ANTHROPIC_URL).respond(
                200,
                json=_anthropic_response_with_placeholder("Письмо отправлено на <EMAIL_1>."),
            )
            r = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4.6",
                    "max_tokens": 100,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Напиши на alice@example.io подтверждение.",
                        }
                    ],
                    "pii_protect": True,
                },
                headers=api_key_fixture.auth_header,
            )
            assert r.status_code == 200, r.text

            sent = json.loads(route.calls.last.request.content)
            sent_user_content = sent["messages"][0]["content"]
            assert "alice@example.io" not in sent_user_content
            assert "<EMAIL_1>" in sent_user_content

            data = r.json()
            assert "alice@example.io" in data["content"][0]["text"]
    finally:
        await real_anthropic.aclose()


# ---------------------------------------------------------------------------
# Account flag — strongest commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_account_flag_enables_without_per_request_optin(
    client, api_key_fixture, app, db, redis_client
):
    """Account.pii_masking_enabled=True → masking without header / body flag."""
    account = await db.get(Account, api_key_fixture.account.id)
    account.pii_masking_enabled = True
    db.add(account)
    await db.commit()

    real_anthropic = AnthropicProvider(api_key="sk-ant-test")
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, real_anthropic)
    try:
        with respx.mock(assert_all_called=True) as router_mock:
            route = router_mock.post(ANTHROPIC_URL).respond(
                200,
                json=_anthropic_response_with_placeholder("Звоню <PHONE_1>."),
            )
            r = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4.6",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": "Перезвони мне на +79161234567."}],
                },
                headers=api_key_fixture.auth_header,  # NO X-PII-Protect / pii_protect
            )
            assert r.status_code == 200, r.text

            sent = json.loads(route.calls.last.request.content)
            sent_user_content = sent["messages"][0]["content"]
            assert "+79161234567" not in sent_user_content
            assert "<PHONE_1>" in sent_user_content

            data = r.json()
            assert "+79161234567" in data["content"][0]["text"]
    finally:
        await real_anthropic.aclose()


# ---------------------------------------------------------------------------
# Disabled by default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_disabled_by_default(client, api_key_fixture, app):
    """Without any flag → prompt forwarded unchanged (no placeholder)."""
    real_anthropic = AnthropicProvider(api_key="sk-ant-test")
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, real_anthropic)
    try:
        with respx.mock(assert_all_called=True) as router_mock:
            route = router_mock.post(ANTHROPIC_URL).respond(
                200,
                json=_anthropic_response_with_placeholder("ok"),
            )
            r = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4.6",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": "Email is dev@x.io"}],
                },
                headers=api_key_fixture.auth_header,
            )
            assert r.status_code == 200, r.text

            sent = json.loads(route.calls.last.request.content)
            assert "dev@x.io" in sent["messages"][0]["content"]
            assert "<EMAIL_" not in sent["messages"][0]["content"]
    finally:
        await real_anthropic.aclose()


# ---------------------------------------------------------------------------
# System field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_system_field_is_masked(client, api_key_fixture, app, redis_client):
    """``system`` is a top-level Anthropic param — must be masked too."""
    real_anthropic = AnthropicProvider(api_key="sk-ant-test")
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, real_anthropic)
    try:
        with respx.mock(assert_all_called=True) as router_mock:
            route = router_mock.post(ANTHROPIC_URL).respond(
                200, json=_anthropic_response_with_placeholder("ok")
            )
            r = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4.6",
                    "max_tokens": 50,
                    "system": "Менеджер аккаунта: alice@example.io",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "pii_protect": True,
                },
                headers=api_key_fixture.auth_header,
            )
            assert r.status_code == 200, r.text
            sent = json.loads(route.calls.last.request.content)
            assert "alice@example.io" not in sent["system"]
            assert "<EMAIL_" in sent["system"]
    finally:
        await real_anthropic.aclose()


# ---------------------------------------------------------------------------
# Multi-type PII — round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_multiple_types_roundtrip(client, api_key_fixture, app, redis_client):
    """Email + phone + name in one prompt — all masked, all restored."""
    real_anthropic = AnthropicProvider(api_key="sk-ant-test")
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, real_anthropic)
    try:
        with respx.mock(assert_all_called=True) as router_mock:
            route = router_mock.post(ANTHROPIC_URL).respond(
                200,
                json=_anthropic_response_with_placeholder(
                    "Контакт сохранён: <NAME_1>, <EMAIL_1>, <PHONE_1>."
                ),
            )
            r = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4.6",
                    "max_tokens": 100,
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
                },
                headers=api_key_fixture.auth_header,
            )
            assert r.status_code == 200, r.text

            sent = json.loads(route.calls.last.request.content)
            sent_user = sent["messages"][0]["content"]
            assert "Иванов Иван Сергеевич" not in sent_user
            assert "ivan@example.io" not in sent_user
            assert "+79161234567" not in sent_user

            text = r.json()["content"][0]["text"]
            assert "Иванов Иван Сергеевич" in text
            assert "ivan@example.io" in text
            assert "+79161234567" in text
            assert "<NAME" not in text
            assert "<EMAIL" not in text
            assert "<PHONE" not in text
    finally:
        await real_anthropic.aclose()


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_stream_unmasks_content_block_delta(client, api_key_fixture, app, redis_client):
    """Streaming: ``content_block_delta.delta.text`` is unmasked through
    StreamUnmasker carry buffer. The placeholder may even be split across
    two SSE chunks; the buffer guarantees it gets restored before the
    bytes leave the gateway."""
    real_anthropic = AnthropicProvider(api_key="sk-ant-test")
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, real_anthropic)

    # Build SSE body where the placeholder is split across two
    # content_block_delta events.  The unmasker should rejoin them.
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_pii_stream",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 5,
                        "output_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        # Split the placeholder ``<NAME_1>`` mid-token.
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Привет, <NAM"},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "E_1>!"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 4},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    sse_body = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for (e, d) in events)

    try:
        with respx.mock() as router_mock:
            router_mock.post(ANTHROPIC_URL).respond(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse_body.encode("utf-8"),
            )
            r = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4.6",
                    "max_tokens": 50,
                    "stream": True,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Привет от Иванов Иван!",
                        }
                    ],
                    "pii_protect": True,
                },
                headers=api_key_fixture.auth_header,
            )
            assert r.status_code == 200, r.text

            body = r.text
            # The raw placeholder MUST NOT leak to the client.
            assert "<NAME_1>" not in body
            # The original surface form MUST appear (via unmask).
            assert "Иванов Иван" in body
            # Anthropic event shape preserved.
            assert "event: content_block_delta" in body
            assert "event: message_stop" in body
    finally:
        await real_anthropic.aclose()


# ---------------------------------------------------------------------------
# No PII → no Redis row, no mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_header_no_pii_in_prompt_is_noop(client, api_key_fixture, app, redis_client):
    """Header on, but prompt has no PII — body passes through unchanged."""
    real_anthropic = AnthropicProvider(api_key="sk-ant-test")
    app.state.provider_registry.register(ProviderEnum.ANTHROPIC, real_anthropic)
    try:
        with respx.mock(assert_all_called=True) as router_mock:
            route = router_mock.post(ANTHROPIC_URL).respond(
                200, json=_anthropic_response_with_placeholder("ok")
            )
            r = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4.6",
                    "max_tokens": 50,
                    "messages": [{"role": "user", "content": "Hello, what is the weather?"}],
                },
                headers={
                    **api_key_fixture.auth_header,
                    "X-PII-Protect": "true",
                },
            )
            assert r.status_code == 200, r.text
            sent = json.loads(route.calls.last.request.content)
            assert sent["messages"][0]["content"] == "Hello, what is the weather?"
    finally:
        await real_anthropic.aclose()
