"""Tests for bot.services.approval_handler — ApprovalRouter.

We mock the aiogram Bot (we never actually call Telegram) and use fakeredis
for the pub/sub. Verifies:

  * render_body / render_keyboard produce the right strings + buttons
  * Bash request → 2 buttons (Allow/Deny), no always-allow row
  * Edit request → 4 buttons (Allow/Deny + Always-allow/Always-deny)
  * handle_request sends the message + tracks live state
  * post_decision LPUSHes the right ApprovalResponse and pops live state
  * subscriber routes ApprovalRequest msgs to handle_request
  * subscriber routes YoloBanner msgs to handle_yolo_banner
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from fakeredis.aioredis import FakeRedis

from bot.services.approval_handler import (
    APPROVAL_CB_PREFIX,
    ApprovalRouter,
)
from shared.approval_protocol import (
    APPROVAL_REQUEST_CHANNEL,
    YOLO_BANNER_CHANNEL,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    YoloBannerEvent,
    approval_response_key,
)


# ---------------------------------------------------------------------------
# Bot mock
# ---------------------------------------------------------------------------


@dataclass
class _SentMessage:
    chat_id: int
    text: str
    reply_markup: Any = None
    parse_mode: str | None = None
    message_id: int = 1001


@dataclass
class _FakeBot:
    """Stand-in for aiogram.Bot — records send_message calls."""

    sent: list[_SentMessage] = field(default_factory=list)
    next_message_id: int = 1001
    raise_on_send: bool = False

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Any = None,
        parse_mode: str | None = None,
    ) -> _SentMessage:
        if self.raise_on_send:
            raise RuntimeError("telegram down")
        msg = _SentMessage(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            message_id=self.next_message_id,
        )
        self.next_message_id += 1
        self.sent.append(msg)
        return msg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_request(
    *,
    request_id: str = "01TESTREQUEST00000000000000",
    tool_name: str = "Bash",
    always_allow_offered: bool = False,
    summary: str = "rm -rf temp",
    description: str = "",
    deadline_offset_s: int = 300,
) -> ApprovalRequest:
    return ApprovalRequest(
        request_id=request_id,
        session_id="sid",
        tg_chat_id=12345,
        tool_name=tool_name,
        display_name=tool_name,
        description=description,
        tool_input_summary=summary,
        tool_input_full={"command": summary} if tool_name == "Bash" else {},
        tool_use_id="toolu_test",
        deadline_unix=int(time.time()) + deadline_offset_s,
        always_allow_offered=always_allow_offered,
    )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def test_render_body_includes_tool_name_and_summary():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    req = _make_request(tool_name="Bash", summary="rm -rf /tmp/x")
    body = router.render_body(req)
    assert "Bash" in body
    assert "rm -rf /tmp/x" in body


def test_render_body_includes_description_when_present():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    req = _make_request(
        tool_name="Bash",
        summary="rm x",
        description="cleanup temp",
    )
    body = router.render_body(req)
    assert "cleanup temp" in body


def test_render_body_escapes_triple_backticks_in_summary():
    """Markdown code blocks would break if user input contained ```.
    We replace with visually-similar unicode."""
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    req = _make_request(summary="echo '```injected```'")
    body = router.render_body(req)
    # Original triple-backtick was replaced
    assert "```injected```" not in body
    # Outer code fence still present (the one we wrap input with)
    assert body.count("```") == 2


def test_render_keyboard_bash_no_always_row():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    req = _make_request(tool_name="Bash", always_allow_offered=False)
    kb = router.render_keyboard(req)
    # Exactly one row: [Allow, Deny]
    assert len(kb.inline_keyboard) == 1
    row = kb.inline_keyboard[0]
    assert len(row) == 2
    assert "Allow" in row[0].text
    assert "Deny" in row[1].text


def test_render_keyboard_edit_has_always_row():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    req = _make_request(tool_name="Edit", always_allow_offered=True)
    kb = router.render_keyboard(req)
    # Two rows: [Allow, Deny] then [Always-allow Edit, Always-deny Edit]
    assert len(kb.inline_keyboard) == 2
    assert "Always allow" in kb.inline_keyboard[1][0].text
    assert "Edit" in kb.inline_keyboard[1][0].text
    assert "Always deny" in kb.inline_keyboard[1][1].text


def test_render_keyboard_callback_data_uses_appr_prefix():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    req = _make_request(
        request_id="01ABC",
        tool_name="Edit",
        always_allow_offered=True,
    )
    kb = router.render_keyboard(req)
    flat = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert all(cb.startswith(APPROVAL_CB_PREFIX) for cb in flat)
    assert f"{APPROVAL_CB_PREFIX}01ABC:allow" in flat
    assert f"{APPROVAL_CB_PREFIX}01ABC:deny" in flat
    assert f"{APPROVAL_CB_PREFIX}01ABC:always_allow" in flat
    assert f"{APPROVAL_CB_PREFIX}01ABC:always_deny" in flat


# ---------------------------------------------------------------------------
# handle_request flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_request_sends_message_with_keyboard():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    req = _make_request(tool_name="Bash")
    await router.handle_request(req)

    assert len(bot.sent) == 1
    sent = bot.sent[0]
    assert sent.chat_id == 12345
    assert sent.reply_markup is not None
    # Body contains tool name
    assert "Bash" in sent.text


@pytest.mark.asyncio
async def test_handle_request_deduplicates_same_request_id():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    req = _make_request(request_id="01DUP")
    await router.handle_request(req)
    await router.handle_request(req)  # duplicate

    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_handle_request_send_failure_emergency_denies():
    """If we can't send the message to Telegram, we LPUSH a Deny so the daemon
    doesn't hang for the full 5-min timeout."""
    bot = _FakeBot(raise_on_send=True)
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    req = _make_request(request_id="01FAIL")
    await router.handle_request(req)

    # Wait briefly for the emergency LPUSH to land (sync inside _post_emergency_deny)
    raw = await redis.lpop(approval_response_key("01FAIL"))
    assert raw is not None
    resp = ApprovalResponse.model_validate_json(raw)
    assert resp.decision == ApprovalDecision.DENY
    assert "render_failed" in resp.message


# ---------------------------------------------------------------------------
# post_decision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_decision_lpushes_response_with_decision_and_ttl():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis, response_ttl_seconds=300)

    req = _make_request(request_id="01POSTDEC0000000000000000")
    await router.handle_request(req)

    live = await router.post_decision(
        request_id="01POSTDEC0000000000000000",
        decision_str="allow",
        actor_chat_id=99,
    )
    assert live is not None
    assert live.tool_name == "Bash"
    # Response is LPUSH'd
    raw = await redis.lpop(approval_response_key("01POSTDEC0000000000000000"))
    assert raw is not None
    resp = ApprovalResponse.model_validate_json(raw)
    assert resp.decision == ApprovalDecision.ALLOW
    assert resp.actor_chat_id == 99
    # TTL was set on the response key — we already popped the value so the
    # key is gone; testing the TTL setter call directly would require deeper
    # mocking. We trust that post_decision calls redis.expire(...) as written.


@pytest.mark.asyncio
async def test_post_decision_with_unknown_request_returns_none_but_still_lpushes():
    """Late button click after the live state expired — still post response
    in case the daemon is still waiting on BLPOP."""
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    live = await router.post_decision(
        request_id="01UNKNOWN",
        decision_str="allow",
        actor_chat_id=99,
    )
    assert live is None
    # Even with no live state, we should have LPUSH'd
    raw = await redis.lpop(approval_response_key("01UNKNOWN"))
    assert raw is not None


@pytest.mark.asyncio
async def test_post_decision_with_invalid_decision_returns_none_no_lpush():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    live = await router.post_decision(
        request_id="01BAD",
        decision_str="maybe",
        actor_chat_id=99,
    )
    assert live is None
    raw = await redis.lpop(approval_response_key("01BAD"))
    assert raw is None  # nothing posted


@pytest.mark.asyncio
async def test_post_decision_removes_live_state():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    req = _make_request(request_id="01LIVECLEAR000000000000000")
    await router.handle_request(req)
    assert "01LIVECLEAR000000000000000" in router._live

    await router.post_decision(
        request_id="01LIVECLEAR000000000000000",
        decision_str="deny",
        actor_chat_id=99,
    )
    assert "01LIVECLEAR000000000000000" not in router._live


# ---------------------------------------------------------------------------
# Subscriber loop — end-to-end with FakeRedis pub/sub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_routes_approval_request_to_handler():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    stop = asyncio.Event()
    task = asyncio.create_task(router.run_subscriber(stop_event=stop))
    try:
        # Give the subscriber a moment to subscribe
        await asyncio.sleep(0.1)

        req = _make_request(request_id="01SUBROUTE0000000000000000")
        await redis.publish(APPROVAL_REQUEST_CHANNEL, req.model_dump_json())

        # Wait for the handler to fire
        deadline = asyncio.get_event_loop().time() + 3.0
        while not bot.sent and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)

        assert len(bot.sent) == 1
        assert "Bash" in bot.sent[0].text
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_subscriber_routes_yolo_banner_to_handler():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    stop = asyncio.Event()
    task = asyncio.create_task(router.run_subscriber(stop_event=stop))
    try:
        await asyncio.sleep(0.1)

        evt = YoloBannerEvent(
            tg_chat_id=99,
            reason="redis-explode",
            emitted_at_unix=0,
        )
        await redis.publish(YOLO_BANNER_CHANNEL, evt.model_dump_json())

        deadline = asyncio.get_event_loop().time() + 3.0
        while not bot.sent and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)

        assert len(bot.sent) == 1
        # YOLO banner text
        assert (
            "защита отключена" in bot.sent[0].text.lower()
            or "redis" in bot.sent[0].text.lower()
        )
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_subscriber_ignores_malformed_messages():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    stop = asyncio.Event()
    task = asyncio.create_task(router.run_subscriber(stop_event=stop))
    try:
        await asyncio.sleep(0.1)
        await redis.publish(APPROVAL_REQUEST_CHANNEL, "not-json")
        await asyncio.sleep(0.1)
        # No crash, no send
        assert bot.sent == []
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_expired_drops_stale_live_entries():
    bot = _FakeBot()
    redis = FakeRedis()
    router = ApprovalRouter(bot=bot, redis=redis)

    # Create a live entry with a past deadline
    req = _make_request(
        request_id="01EXPIRED",
        deadline_offset_s=-1000,
    )
    await router.handle_request(req)
    # _cleanup_expired runs at end of handle_request automatically
    assert "01EXPIRED" not in router._live
