"""Tests for bot.handlers.follow — /follow on|off|status."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fakeredis.aioredis import FakeRedis

from bot.db import authorize_chat, init_db, set_active_session
from bot.handlers.follow import cmd_follow, set_redis
from shared.follow_protocol import (
    FOLLOW_CONTROL_CHANNEL,
    FollowAction,
    FollowControl,
)


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setenv("BRIDGE_BOT_DB_PATH", db)
    monkeypatch.setenv("BRIDGE_BOT_BOT_TOKEN", "fake")
    monkeypatch.setenv("BRIDGE_BOT_BOT_USERNAME", "test")
    monkeypatch.setenv("BRIDGE_BOT_DAEMON_URL", "http://d.test")
    return db


def _msg(chat_id: int = 42) -> MagicMock:
    m = MagicMock()
    m.chat = MagicMock(id=chat_id)
    m.answer = AsyncMock()
    return m


def _cmd(args: str | None = None) -> MagicMock:
    c = MagicMock()
    c.args = args
    return c


async def _seed(db: str, chat_id: int = 42, session_id: str = "sid-1") -> None:
    await init_db(db)
    await authorize_chat(db, chat_id, "ceo")
    await set_active_session(db, chat_id, session_id, "C:/proj")


# ---------------------------------------------------------------------------
# /follow help / unknown args
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_follow_with_no_arg_shows_help(db_env):
    msg = _msg()
    await cmd_follow(msg, _cmd(args=None))
    sent = msg.answer.call_args[0][0]
    assert "on" in sent and "off" in sent and "status" in sent


@pytest.mark.asyncio
async def test_follow_with_unknown_arg_shows_help(db_env):
    msg = _msg()
    await cmd_follow(msg, _cmd(args="banana"))
    sent = msg.answer.call_args[0][0]
    assert "/follow on" in sent or "follow on" in sent


# ---------------------------------------------------------------------------
# /follow on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_follow_on_publishes_register_control(db_env):
    await _seed(db_env)
    redis = FakeRedis()
    set_redis(redis)
    pubsub = redis.pubsub()
    await pubsub.subscribe(FOLLOW_CONTROL_CHANNEL)
    try:
        msg = _msg()
        await cmd_follow(msg, _cmd(args="on"))

        # Drain pubsub
        published: list[FollowControl] = []
        for _ in range(5):
            x = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if x and x.get("type") == "message":
                data = x["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                published.append(FollowControl.model_validate_json(data))
                break
        assert len(published) == 1
        assert published[0].action == FollowAction.REGISTER
        assert published[0].tg_chat_id == 42
        assert published[0].session_id == "sid-1"
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()


@pytest.mark.asyncio
async def test_follow_on_without_active_session_tells_user_to_switch(db_env):
    redis = FakeRedis()
    set_redis(redis)
    # No /switch yet
    await init_db(db_env)
    await authorize_chat(db_env, 42, "ceo")

    msg = _msg(42)
    await cmd_follow(msg, _cmd(args="on"))
    sent = msg.answer.call_args[0][0]
    assert "/sessions" in sent or "/switch" in sent


@pytest.mark.asyncio
async def test_follow_on_publish_failure_surfaces_error(db_env):
    await _seed(db_env)

    class BrokenRedis:
        async def publish(self, ch, msg):  # noqa: ARG002
            raise RuntimeError("nope")

    set_redis(BrokenRedis())
    msg = _msg()
    await cmd_follow(msg, _cmd(args="on"))
    sent = msg.answer.call_args[0][0]
    assert "Redis" in sent or "недоступен" in sent.lower() or "daemon" in sent.lower()


# ---------------------------------------------------------------------------
# /follow off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_follow_off_publishes_unregister_control(db_env):
    await _seed(db_env)
    redis = FakeRedis()
    set_redis(redis)
    pubsub = redis.pubsub()
    await pubsub.subscribe(FOLLOW_CONTROL_CHANNEL)
    try:
        msg = _msg()
        await cmd_follow(msg, _cmd(args="off"))
        published: list[FollowControl] = []
        for _ in range(5):
            x = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if x and x.get("type") == "message":
                data = x["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                published.append(FollowControl.model_validate_json(data))
                break
        assert len(published) == 1
        assert published[0].action == FollowAction.UNREGISTER
        assert published[0].session_id == "sid-1"
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()


@pytest.mark.asyncio
async def test_follow_off_all_publishes_unregister_all(db_env):
    await _seed(db_env)
    redis = FakeRedis()
    set_redis(redis)
    pubsub = redis.pubsub()
    await pubsub.subscribe(FOLLOW_CONTROL_CHANNEL)
    try:
        msg = _msg()
        await cmd_follow(msg, _cmd(args="off all"))
        published: list[FollowControl] = []
        for _ in range(5):
            x = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if x and x.get("type") == "message":
                data = x["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                published.append(FollowControl.model_validate_json(data))
                break
        assert len(published) == 1
        assert published[0].action == FollowAction.UNREGISTER_ALL
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()


# ---------------------------------------------------------------------------
# /follow status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_follow_status_empty(db_env, monkeypatch):
    await _seed(db_env)
    monkeypatch.setattr(
        "bot.services.daemon_client.list_follows", AsyncMock(return_value=[])
    )
    msg = _msg()
    await cmd_follow(msg, _cmd(args="status"))
    sent = msg.answer.call_args[0][0]
    assert "ничего" in sent or "📭" in sent


@pytest.mark.asyncio
async def test_follow_status_with_entries(db_env, monkeypatch):
    await _seed(db_env)
    fake_follows = [
        {
            "chat_id": 42,
            "session_id": "abcdef-1234",
            "path": "C:/Users/x/.claude/projects/foo/abcdef-1234.jsonl",
            "bytes_read": 999,
            "lines_parsed": 8,
            "events_published": 7,
            "started_at": 0,
            "last_activity_at": 0,
            "ended": False,
        },
    ]
    monkeypatch.setattr(
        "bot.services.daemon_client.list_follows",
        AsyncMock(return_value=fake_follows),
    )
    msg = _msg()
    await cmd_follow(msg, _cmd(args="status"))
    sent = msg.answer.call_args[0][0]
    assert "abcdef-1" in sent  # short id
    assert "7" in sent  # events_published
