"""Tests for bot.middleware.whitelist."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.db import authorize_chat, init_db
from bot.middleware.whitelist import WhitelistMiddleware


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setenv("BRIDGE_BOT_DB_PATH", db)
    monkeypatch.setenv("BRIDGE_BOT_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("BRIDGE_BOT_BOT_USERNAME", "test_bot")
    monkeypatch.setenv("BRIDGE_BOT_DAEMON_URL", "http://daemon.test")
    return db


def _make_message(chat_id: int, text: str | None) -> MagicMock:
    msg = MagicMock()
    msg.chat = MagicMock(id=chat_id)
    msg.text = text
    msg.answer = AsyncMock()
    # Make isinstance(msg, Message) work for the middleware
    from aiogram.types import Message
    msg.__class__ = Message
    return msg


@pytest.mark.asyncio
async def test_authorized_chat_passes_through(db_env):
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42, nickname="user")

    handler = AsyncMock(return_value="handled")
    mw = WhitelistMiddleware()

    msg = _make_message(42, "hi")
    result = await mw(handler, msg, {})
    assert result == "handled"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_unauthorized_chat_blocked_with_message(db_env):
    await init_db(db_env)
    handler = AsyncMock()
    mw = WhitelistMiddleware()

    msg = _make_message(99, "hello bot")
    result = await mw(handler, msg, {})
    assert result is None
    handler.assert_not_called()
    msg.answer.assert_called_once()
    sent = msg.answer.call_args[0][0]
    assert "не привязан" in sent.lower() or "🚫" in sent


@pytest.mark.asyncio
async def test_start_command_passes_even_if_unauthorized(db_env):
    """/start must always reach the auth handler — that's how onboarding works."""
    await init_db(db_env)
    handler = AsyncMock(return_value="handled")
    mw = WhitelistMiddleware()

    msg = _make_message(11, "/start abc-token")
    result = await mw(handler, msg, {})
    assert result == "handled"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_help_command_passes_even_if_unauthorized(db_env):
    await init_db(db_env)
    handler = AsyncMock(return_value="handled")
    mw = WhitelistMiddleware()

    msg = _make_message(11, "/help")
    result = await mw(handler, msg, {})
    assert result == "handled"


@pytest.mark.asyncio
async def test_other_command_blocked_when_unauthorized(db_env):
    await init_db(db_env)
    handler = AsyncMock()
    mw = WhitelistMiddleware()

    msg = _make_message(99, "/sessions")
    result = await mw(handler, msg, {})
    assert result is None
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_event_without_chat_passes_through(db_env):
    """Random non-Message events shouldn't crash the middleware."""
    await init_db(db_env)
    handler = AsyncMock(return_value="handled")
    mw = WhitelistMiddleware()

    # raw TelegramObject — no chat_id extractable
    event = MagicMock()
    result = await mw(handler, event, {})
    assert result == "handled"
