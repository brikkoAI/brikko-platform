"""Sprint 8 F3 — Smart-routing metadata surfaced to clients.

UX audit P2-3: when the user posts ``model: "auto:cheap"`` we picked a
model on their behalf, but they had no way to see *which* — important
for billing reconciliation and debug.

The contract added here:

* ``X-Router-Decision``       — composite header (was already there).
* ``X-Router-Strategy``       — public strategy name (cheap | smart |
                                 fast | ru-legal | pinned).
* ``X-Router-Reason``         — short reason string, ASCII-safe.
* ``X-Router-Fallback-Used``  — "true" / "false".
* body.routing                — same fields as a structured object so
                                 SDK callers don't have to parse headers.

Streaming and non-streaming both carry the headers; only non-streaming
carries the body block (an SSE stream's body is owned by the provider).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_non_stream_routing_block_pinned_model(client, api_key_fixture, app):
    """Pinned model id → strategy=pinned, model_chosen echoes the request."""
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "ping"}],
    }
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200, r.text
    data = r.json()

    assert "routing" in data
    routing = data["routing"]
    assert routing["model_chosen"] == "gpt-5.4-mini"
    assert routing["model_requested"] == "gpt-5.4-mini"
    assert routing["strategy"] == "pinned"
    assert routing["fallback_used"] is False
    # Reason follows the convention "pinned:<model_id>".
    assert routing["reason"].startswith("pinned:gpt-5.4-mini")


@pytest.mark.asyncio
async def test_non_stream_routing_headers_pinned(client, api_key_fixture):
    """Discrete X-Router-* headers complement the composite one."""
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "ping"}],
    }
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200

    # Composite (legacy) header is preserved.
    assert "gpt-5.4-mini" in r.headers["x-router-decision"]
    # Discrete headers (new in Sprint 8).
    assert r.headers["x-router-strategy"] == "pinned"
    assert "pinned" in r.headers["x-router-reason"]
    assert r.headers["x-router-fallback-used"] == "false"


@pytest.mark.asyncio
async def test_non_stream_routing_block_auto_cheap(client, api_key_fixture, app):
    """auto:cheap → strategy=cheap, chosen model is real catalogue id."""
    body = {
        "model": "auto:cheap",
        "messages": [{"role": "user", "content": "hello"}],
    }
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200, r.text
    data = r.json()

    routing = data["routing"]
    assert routing["strategy"] == "cheap"
    # The stub provider only knows OpenAI models — the cheap strategy
    # must still pick something we can serve. The header should carry
    # the resolved id, not "auto:cheap".
    assert not routing["model_chosen"].startswith("auto")
    assert routing["model_requested"] == "auto:cheap"
    assert routing["fallback_used"] is False
    assert r.headers["x-router-strategy"] == "cheap"


def test_ascii_header_strips_cyrillic() -> None:
    """``_ascii_header`` keeps the function safe for arbitrary reason strings.

    The streaming response also sets X-Router-* headers (same code path
    as non-streaming below); we don't drive the SSE generator from a
    test here because doing so leaves an anyio event listener pinned
    to the test's loop and crashes neighbouring tests on tear-down
    (see test_chat_stream_cancel.py module docstring). The unit-level
    coverage of ``_ascii_header`` is enough — the stream handler
    threads the same value through its headers dict.
    """
    from voltari_gateway.api.chat import _ascii_header

    assert _ascii_header("auto:cheap-CHAT") == "auto:cheap-CHAT"
    assert _ascii_header("автоматический") == ""  # all Cyrillic stripped
    assert _ascii_header("mix mix русский") == "mix mix "


@pytest.mark.asyncio
async def test_routing_block_carries_category(client, api_key_fixture):
    """Routing.category is set so analytics dashboards can group by it."""
    body = {
        "model": "auto:cheap",
        "messages": [{"role": "user", "content": "tell me a story"}],
    }
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    data = r.json()
    assert "category" in data["routing"]
    # Category is the str() of TaskCategory enum — non-empty word.
    assert isinstance(data["routing"]["category"], str)
    assert data["routing"]["category"]


@pytest.mark.asyncio
async def test_routing_block_failover_used_false_on_success(client, api_key_fixture):
    """Successful first-try call → fallback_used is False.

    Failover-true paths are exercised in test_chat_with_router /
    test_provider_failure_matrix; here we just lock in that the new
    field is wired and reflects the no-fallback case.
    """
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "no fallback please"}],
    }
    r = await client.post(
        "/v1/chat/completions",
        json=body,
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["routing"]["fallback_used"] is False
    assert r.headers["x-router-fallback-used"] == "false"
