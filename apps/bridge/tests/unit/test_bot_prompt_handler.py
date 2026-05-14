"""Tests for bot.handlers.prompt — text msg → daemon SSE → Telegram edits."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.db import authorize_chat, init_db, set_active_session
from bot.handlers.prompt import handle_prompt
from bot.services.daemon_client import DaemonOffline
from shared.events import StreamEnd, TextDelta


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setenv("BRIDGE_BOT_DB_PATH", db)
    monkeypatch.setenv("BRIDGE_BOT_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("BRIDGE_BOT_BOT_USERNAME", "test_bot")
    monkeypatch.setenv("BRIDGE_BOT_DAEMON_URL", "http://daemon.test")
    return db


def _make_msg(text: str, chat_id: int = 42) -> MagicMock:
    msg = MagicMock()
    msg.chat = MagicMock(id=chat_id)
    msg.text = text
    placeholder = MagicMock()
    placeholder.edit_text = AsyncMock()
    msg.answer = AsyncMock(return_value=placeholder)
    msg._placeholder = placeholder  # for assertions
    return msg


async def _async_stream(events):
    for e in events:
        yield e


@pytest.mark.asyncio
async def test_no_active_session_prompts_user(db_env, monkeypatch):
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42)

    # Don't set an active session — handler should bail out early.
    fake_stream = AsyncMock()
    monkeypatch.setattr(
        "bot.handlers.prompt.stream_send", lambda *a, **kw: _async_stream([])
    )

    msg = _make_msg("do a thing", chat_id=42)
    await handle_prompt(msg)

    msg.answer.assert_called_once()
    sent = msg.answer.call_args[0][0]
    assert "/sessions" in sent or "/switch" in sent


@pytest.mark.asyncio
async def test_empty_text_is_ignored(db_env):
    await init_db(db_env)
    msg = _make_msg("   ", chat_id=42)
    await handle_prompt(msg)
    msg.answer.assert_not_called()


@pytest.mark.asyncio
async def test_slash_command_is_ignored(db_env):
    """Slash commands are routed elsewhere; prompt handler must skip them."""
    await init_db(db_env)
    msg = _make_msg("/sessions", chat_id=42)
    await handle_prompt(msg)
    msg.answer.assert_not_called()


@pytest.mark.asyncio
async def test_streams_text_into_telegram(db_env, monkeypatch):
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42)
    await set_active_session(db_env, 42, "sess-1", "/proj")

    events = [
        TextDelta(delta="Hello"),
        TextDelta(delta=" world"),
        StreamEnd(is_error=False, tokens_in=3, tokens_out=2, cost_usd=0.001),
    ]
    monkeypatch.setattr(
        "bot.handlers.prompt.stream_send", lambda *a, **kw: _async_stream(events)
    )
    # Force every flush — disable throttle.
    monkeypatch.setattr("bot.handlers.prompt.EDIT_THROTTLE_SEC", 0)

    msg = _make_msg("hi claude", chat_id=42)
    await handle_prompt(msg)

    placeholder = msg._placeholder
    assert placeholder.edit_text.await_count >= 1
    final_text = placeholder.edit_text.call_args[0][0]
    assert "Hello world" in final_text
    assert "in:3" in final_text
    assert "out:2" in final_text


@pytest.mark.asyncio
async def test_daemon_offline_shows_friendly_message(db_env, monkeypatch):
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42)
    await set_active_session(db_env, 42, "sess-1", "/proj")

    async def boom(*args, **kwargs):
        raise DaemonOffline("tunnel down")
        yield  # pragma: no cover — make this an async-gen

    monkeypatch.setattr("bot.handlers.prompt.stream_send", boom)

    msg = _make_msg("anything", chat_id=42)
    await handle_prompt(msg)

    placeholder = msg._placeholder
    placeholder.edit_text.assert_awaited()
    final = placeholder.edit_text.call_args[0][0]
    assert "daemon" in final.lower() or "туннел" in final.lower() or "❌" in final


@pytest.mark.asyncio
async def test_unhandled_exception_shows_error(db_env, monkeypatch):
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42)
    await set_active_session(db_env, 42, "sess-1", "/proj")

    async def boom(*args, **kwargs):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover

    monkeypatch.setattr("bot.handlers.prompt.stream_send", boom)

    msg = _make_msg("anything", chat_id=42)
    await handle_prompt(msg)

    final = msg._placeholder.edit_text.call_args[0][0]
    assert "❌" in final


@pytest.mark.asyncio
async def test_preflight_blocks_risky_prompt(db_env, monkeypatch):
    """A clearly destructive prompt must be blocked, not forwarded."""
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42)
    await set_active_session(db_env, 42, "sess-1", "/proj")

    # If preflight passes, this stream would run; we assert it's NOT called.
    streamed = AsyncMock()
    monkeypatch.setattr("bot.handlers.prompt.stream_send", streamed)

    msg = _make_msg("rm -rf / now please", chat_id=42)
    await handle_prompt(msg)

    # Bot answered with a warning, not a placeholder + stream
    streamed.assert_not_called()
    msg.answer.assert_called_once()
    sent = msg.answer.call_args[0][0]
    assert "Pre-flight" in sent or "/yolo" in sent


@pytest.mark.asyncio
async def test_skip_preflight_lets_risky_prompt_through(db_env, monkeypatch):
    """/yolo path: skip the safety net."""
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42)
    await set_active_session(db_env, 42, "sess-1", "/proj")

    events = [StreamEnd(is_error=False, tokens_in=1, tokens_out=1, cost_usd=0.0)]
    monkeypatch.setattr(
        "bot.handlers.prompt.stream_send", lambda *a, **kw: _async_stream(events)
    )
    monkeypatch.setattr("bot.handlers.prompt.EDIT_THROTTLE_SEC", 0)

    msg = _make_msg("rm -rf /tmp/foo", chat_id=42)
    await handle_prompt(msg, skip_preflight=True)

    # Stream was opened (placeholder sent) — not blocked
    msg.answer.assert_called_once()
    assert msg.answer.call_args[0][0] == "…"


@pytest.mark.asyncio
async def test_strict_preflight_blocks_write_intent(db_env, monkeypatch):
    """/safe path: even benign 'edit' verb is blocked."""
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42)
    await set_active_session(db_env, 42, "sess-1", "/proj")

    streamed = AsyncMock()
    monkeypatch.setattr("bot.handlers.prompt.stream_send", streamed)

    msg = _make_msg("edit src/main.py", chat_id=42)
    await handle_prompt(msg, strict_preflight=True)

    streamed.assert_not_called()
    sent = msg.answer.call_args[0][0]
    assert "Pre-flight" in sent


@pytest.mark.asyncio
async def test_empty_response_shows_placeholder_text(db_env, monkeypatch):
    """If Claude says nothing at all, user shouldn't see a bare '…'."""
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42)
    await set_active_session(db_env, 42, "sess-1", "/proj")

    monkeypatch.setattr(
        "bot.handlers.prompt.stream_send", lambda *a, **kw: _async_stream([])
    )

    msg = _make_msg("ping", chat_id=42)
    await handle_prompt(msg)

    final = msg._placeholder.edit_text.call_args[0][0]
    assert "пусто" in final.lower() or "ничего" in final.lower()
