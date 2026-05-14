"""Tests for bot.services.cli_mirror_subscriber — renderer + subscriber."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from fakeredis.aioredis import FakeRedis

from bot.services.cli_mirror_subscriber import (
    CliMirrorRenderer,
    CliMirrorSubscriber,
    MASKED_FOOTER,
    render_notice,
)
from shared.events import (
    StreamEnd,
    SystemInit,
    TextDelta,
    ToolResult,
    ToolUse,
    UserPrompt,
)
from shared.follow_protocol import CliMirrorEvent, cli_mirror_channel


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _SentMsg:
    chat_id: int
    text: str
    parse_mode: str | None = None


@dataclass
class _FakeBot:
    sent: list[_SentMsg] = field(default_factory=list)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None = None,
    ) -> Any:
        self.sent.append(_SentMsg(chat_id=chat_id, text=text, parse_mode=parse_mode))


async def _flush(renderer: CliMirrorRenderer) -> None:
    await renderer.force_flush()


def _make_renderer(bot: _FakeBot, chat_id: int = 1, coalesce_ms: int = 50):
    return CliMirrorRenderer(
        chat_id=chat_id,
        send_text=lambda body, mode, cid=chat_id: bot.send_message(
            chat_id=cid, text=body, parse_mode=mode
        ),
        coalesce_ms=coalesce_ms,
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renderer_buffers_text_until_flush():
    bot = _FakeBot()
    r = _make_renderer(bot)
    await r.feed(TextDelta(delta="Hello "))
    await r.feed(TextDelta(delta="world"))
    # Nothing sent yet (still in coalesce window, no other event flushed it)
    assert bot.sent == []
    await _flush(r)
    assert len(bot.sent) == 1
    # CLI prefix applied
    assert bot.sent[0].text.startswith("🖥 ")
    assert "Hello world" in bot.sent[0].text


@pytest.mark.asyncio
async def test_renderer_idle_flush_after_window():
    """After ``coalesce_ms`` of inactivity, maybe_flush_idle drains the buffer."""
    bot = _FakeBot()
    r = _make_renderer(bot, coalesce_ms=20)
    await r.feed(TextDelta(delta="abc"))
    await asyncio.sleep(0.05)
    await r.maybe_flush_idle()
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_renderer_tool_use_flushes_pending_text():
    bot = _FakeBot()
    r = _make_renderer(bot)
    await r.feed(TextDelta(delta="thinking..."))
    await r.feed(ToolUse(tool="Bash", input={"command": "ls"}))
    # 2 sends — the pending text was flushed first, then the ToolUse line
    assert len(bot.sent) == 2
    assert "thinking" in bot.sent[0].text
    assert "Bash" in bot.sent[1].text


@pytest.mark.asyncio
async def test_renderer_user_prompt_rendered_with_prefix():
    bot = _FakeBot()
    r = _make_renderer(bot)
    await r.feed(UserPrompt(text="привет"))
    assert len(bot.sent) == 1
    assert "вы:" in bot.sent[0].text
    assert "привет" in bot.sent[0].text


@pytest.mark.asyncio
async def test_renderer_tool_result_ok_and_error_icons():
    bot = _FakeBot()
    r = _make_renderer(bot)
    await r.feed(ToolResult(tool="Bash", ok=True, summary="ok"))
    await r.feed(ToolResult(tool="Bash", ok=False, summary="boom"))
    assert "✓" in bot.sent[0].text
    assert "❌" in bot.sent[1].text


@pytest.mark.asyncio
async def test_renderer_stream_end_renders_footer():
    bot = _FakeBot()
    r = _make_renderer(bot)
    await r.feed(StreamEnd(reason="stop", tokens_in=5, tokens_out=10, cost_usd=0.02))
    assert len(bot.sent) == 1
    assert "done" in bot.sent[0].text
    assert "in:5" in bot.sent[0].text
    assert "out:10" in bot.sent[0].text


@pytest.mark.asyncio
async def test_renderer_system_init_silent():
    bot = _FakeBot()
    r = _make_renderer(bot)
    await r.feed(SystemInit(session_id="x", cwd="C:/x"))
    assert bot.sent == []


@pytest.mark.asyncio
async def test_renderer_masked_footer_added_once():
    """When a TextDelta was masked, the footer is added on the next send."""
    bot = _FakeBot()
    r = _make_renderer(bot)
    await r.feed(TextDelta(delta="key=[REDACTED:openai_key]"), masked=True)
    await _flush(r)
    assert len(bot.sent) == 1
    assert MASKED_FOOTER in bot.sent[0].text

    # Next send (no masking) — no footer
    await r.feed(TextDelta(delta="another sentence"))
    await _flush(r)
    assert len(bot.sent) == 2
    assert MASKED_FOOTER not in bot.sent[1].text


# ---------------------------------------------------------------------------
# Notice helper
# ---------------------------------------------------------------------------


def test_render_notice_registered():
    text = render_notice("registered", "abcdef-12345", "")
    assert "follow on" in text
    assert "abcdef-1" in text  # short id


def test_render_notice_unregistered():
    text = render_notice("unregistered", "abcdef-12345", "")
    assert "follow off" in text


def test_render_notice_session_ended():
    text = render_notice("session_ended", "abcdef-12345", "")
    assert "завершилась" in text


def test_render_notice_error_falls_back_to_message():
    text = render_notice("error", "", "permission denied")
    assert "⚠️" in text
    assert "permission denied" in text


# ---------------------------------------------------------------------------
# Subscriber dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_dispatch_text_event_routes_to_chat():
    bot = _FakeBot()
    redis = FakeRedis()
    sub = CliMirrorSubscriber(bot=bot, redis=redis, coalesce_ms=10)

    wire = CliMirrorEvent(
        chat_id=42,
        session_id="sid",
        source="cli",
        event={"type": "text", "delta": "hi"},
    )
    await sub.dispatch(wire)
    # Force flush via direct renderer access (no idle timer in unit test)
    renderer = sub._renderers[42]
    await renderer.force_flush()
    assert len(bot.sent) == 1
    assert bot.sent[0].chat_id == 42
    assert "hi" in bot.sent[0].text


@pytest.mark.asyncio
async def test_subscriber_dispatch_follow_notice_renders_directly():
    bot = _FakeBot()
    redis = FakeRedis()
    sub = CliMirrorSubscriber(bot=bot, redis=redis)

    wire = CliMirrorEvent(
        chat_id=42,
        session_id="abc-1234",
        source="cli",
        event={
            "type": "follow_notice",
            "chat_id": 42,
            "session_id": "abc-1234",
            "kind": "registered",
            "message": "",
        },
    )
    await sub.dispatch(wire)
    assert len(bot.sent) == 1
    assert "follow on" in bot.sent[0].text


@pytest.mark.asyncio
async def test_subscriber_dispatch_invalid_payload_silently_dropped():
    bot = _FakeBot()
    redis = FakeRedis()
    sub = CliMirrorSubscriber(bot=bot, redis=redis)
    wire = CliMirrorEvent(
        chat_id=42, session_id="s", source="cli", event={"type": "nope"}
    )
    await sub.dispatch(wire)
    assert bot.sent == []


@pytest.mark.asyncio
async def test_subscriber_per_chat_renderer_isolation():
    """Two chats get separate renderers — text for chat A doesn't leak to B."""
    bot = _FakeBot()
    redis = FakeRedis()
    sub = CliMirrorSubscriber(bot=bot, redis=redis, coalesce_ms=10)
    await sub.dispatch(
        CliMirrorEvent(
            chat_id=1, session_id="a", source="cli",
            event={"type": "text", "delta": "to one"},
        )
    )
    await sub.dispatch(
        CliMirrorEvent(
            chat_id=2, session_id="b", source="cli",
            event={"type": "text", "delta": "to two"},
        )
    )
    await sub._renderers[1].force_flush()
    await sub._renderers[2].force_flush()
    chats = {m.chat_id for m in bot.sent}
    assert chats == {1, 2}
    assert any(m.chat_id == 1 and "to one" in m.text for m in bot.sent)
    assert any(m.chat_id == 2 and "to two" in m.text for m in bot.sent)


# ---------------------------------------------------------------------------
# End-to-end through Redis pubsub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_e2e_via_redis_pubsub():
    bot = _FakeBot()
    redis = FakeRedis()
    sub = CliMirrorSubscriber(
        bot=bot,
        redis=redis,
        coalesce_ms=20,
        idle_flush_interval_s=0.05,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sub.run(stop_event=stop))
    try:
        await asyncio.sleep(0.15)  # let psubscribe settle
        wire = CliMirrorEvent(
            chat_id=42, session_id="sid", source="cli",
            event={"type": "text", "delta": "hello via redis"},
        )
        await redis.publish(cli_mirror_channel(42), wire.model_dump_json())

        # Wait for the idle flush to fire
        deadline = asyncio.get_event_loop().time() + 3.0
        while asyncio.get_event_loop().time() < deadline:
            if bot.sent:
                break
            await asyncio.sleep(0.05)
        assert len(bot.sent) >= 1
        assert "hello via redis" in bot.sent[0].text
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
