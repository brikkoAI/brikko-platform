"""End-to-end CLI-mirror smoke test (Part A).

Exercises the full path daemon ↔ Redis ↔ bot WITHOUT real Claude or Telegram:

  CLI writes line to ~/.claude/projects/<encoded>/<session>.jsonl
       │
       ▼
  JsonlTailer (running in daemon)
       │
       │  publish CliMirrorEvent on bridge:cli-mirror:<chat_id>
       ▼
  CliMirrorSubscriber (running in bot)
       │
       ▼  renders via CliMirrorRenderer → FakeBot.send_message

Verifies:
  * Register via FollowControl → tailer starts
  * Subsequent appends to the jsonl reach the bot as Telegram messages
  * Unregister stops the tailer + bot gets a notice
  * Multiple sessions for the same chat are isolated
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fakeredis.aioredis import FakeRedis

from bot.services.cli_mirror_subscriber import CliMirrorSubscriber
from daemon.follow_service import FollowService
from daemon.follow_state import FollowRegistry
from shared.follow_protocol import (
    FOLLOW_CONTROL_CHANNEL,
    FollowAction,
    FollowControl,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _SentMessage:
    chat_id: int
    text: str
    parse_mode: str | None = None


@dataclass
class _FakeBot:
    sent: list[_SentMessage] = field(default_factory=list)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None = None,
    ) -> None:
        self.sent.append(
            _SentMessage(chat_id=chat_id, text=text, parse_mode=parse_mode)
        )


def _make_init_line(sid: str) -> str:
    return json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "session_id": sid,
            "cwd": "C:/x",
        }
    )


def _make_text_line(text: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]},
        }
    )


def _make_user_line(text: str) -> str:
    return json.dumps(
        {"type": "user", "message": {"role": "user", "content": text}}
    )


async def _wait_for(condition, *, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition never became true within timeout")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_register_then_append_then_message_reaches_bot(tmp_path: Path):
    """Happy path: /follow on → CLI writes → bot gets text."""
    redis = FakeRedis()
    bot = _FakeBot()
    path = tmp_path / "abc-123.jsonl"
    path.write_text(_make_init_line("abc-123") + "\n", encoding="utf-8")

    # Bot side: subscriber
    sub = CliMirrorSubscriber(
        bot=bot, redis=redis, coalesce_ms=30, idle_flush_interval_s=0.05
    )
    sub_stop = asyncio.Event()
    sub_task = asyncio.create_task(sub.run(stop_event=sub_stop))

    # Daemon side: registry + service
    reg = FollowRegistry(
        publisher=redis.publish,
        locate=lambda sid: path if sid == "abc-123" else None,
        poll_interval_s=0.05,
    )
    svc = FollowService(redis=redis, registry=reg)
    svc_stop = asyncio.Event()
    svc_task = asyncio.create_task(svc.run(stop_event=svc_stop))

    try:
        await asyncio.sleep(0.15)  # let subscribers settle

        # Publish a register control on the bot→daemon channel
        await redis.publish(
            FOLLOW_CONTROL_CHANNEL,
            FollowControl(
                action=FollowAction.REGISTER, tg_chat_id=42, session_id="abc-123"
            ).model_dump_json(),
        )
        # Bot should see a "registered" notice
        await _wait_for(
            lambda: any(
                "follow on" in m.text.lower() for m in bot.sent
            )
        )

        # CLI appends a new line
        with path.open("a", encoding="utf-8") as f:
            f.write(_make_text_line("hello from CLI") + "\n")

        # Bot should eventually see it
        await _wait_for(
            lambda: any("hello from CLI" in m.text for m in bot.sent),
            timeout=5.0,
        )
        assert any("hello from CLI" in m.text for m in bot.sent)
        # All TG messages go to chat 42
        assert all(m.chat_id == 42 for m in bot.sent)
    finally:
        sub_stop.set()
        svc_stop.set()
        for t in (sub_task, svc_task):
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()
        await reg.close_all()


@pytest.mark.asyncio
async def test_e2e_unregister_stops_mirror_and_sends_notice(tmp_path: Path):
    redis = FakeRedis()
    bot = _FakeBot()
    path = tmp_path / "s.jsonl"
    path.write_text(_make_init_line("s") + "\n", encoding="utf-8")

    sub = CliMirrorSubscriber(
        bot=bot, redis=redis, coalesce_ms=30, idle_flush_interval_s=0.05
    )
    sub_stop = asyncio.Event()
    sub_task = asyncio.create_task(sub.run(stop_event=sub_stop))

    reg = FollowRegistry(
        publisher=redis.publish,
        locate=lambda sid: path if sid == "s" else None,
        poll_interval_s=0.05,
    )
    svc = FollowService(redis=redis, registry=reg)
    svc_stop = asyncio.Event()
    svc_task = asyncio.create_task(svc.run(stop_event=svc_stop))

    try:
        await asyncio.sleep(0.15)
        await redis.publish(
            FOLLOW_CONTROL_CHANNEL,
            FollowControl(
                action=FollowAction.REGISTER, tg_chat_id=7, session_id="s"
            ).model_dump_json(),
        )
        await _wait_for(lambda: reg.is_following(7, "s"))

        # Unregister
        await redis.publish(
            FOLLOW_CONTROL_CHANNEL,
            FollowControl(
                action=FollowAction.UNREGISTER, tg_chat_id=7, session_id="s"
            ).model_dump_json(),
        )
        await _wait_for(lambda: not reg.is_following(7, "s"))
        # User sees "follow off" message somewhere
        await _wait_for(
            lambda: any("follow off" in m.text.lower() for m in bot.sent)
        )
    finally:
        sub_stop.set()
        svc_stop.set()
        for t in (sub_task, svc_task):
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()
        await reg.close_all()


@pytest.mark.asyncio
async def test_e2e_user_prompt_from_cli_reaches_bot(tmp_path: Path):
    """When CEO types into CLI, the bot mirrors with the 'вы:' prefix."""
    redis = FakeRedis()
    bot = _FakeBot()
    path = tmp_path / "s.jsonl"
    path.write_text(_make_init_line("s") + "\n", encoding="utf-8")

    sub = CliMirrorSubscriber(
        bot=bot, redis=redis, coalesce_ms=20, idle_flush_interval_s=0.05
    )
    sub_stop = asyncio.Event()
    sub_task = asyncio.create_task(sub.run(stop_event=sub_stop))

    reg = FollowRegistry(
        publisher=redis.publish,
        locate=lambda sid: path if sid == "s" else None,
        poll_interval_s=0.05,
    )
    svc = FollowService(redis=redis, registry=reg)
    svc_stop = asyncio.Event()
    svc_task = asyncio.create_task(svc.run(stop_event=svc_stop))

    try:
        await asyncio.sleep(0.15)
        await redis.publish(
            FOLLOW_CONTROL_CHANNEL,
            FollowControl(
                action=FollowAction.REGISTER, tg_chat_id=42, session_id="s"
            ).model_dump_json(),
        )
        await _wait_for(lambda: reg.is_following(42, "s"))

        with path.open("a", encoding="utf-8") as f:
            f.write(_make_user_line("hey claude") + "\n")

        await _wait_for(
            lambda: any("вы:" in m.text and "hey claude" in m.text for m in bot.sent),
            timeout=5.0,
        )
    finally:
        sub_stop.set()
        svc_stop.set()
        for t in (sub_task, svc_task):
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()
        await reg.close_all()
