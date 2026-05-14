"""Tests for the inline 🛑 Прервать button callback."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.db import init_db
from bot.handlers.prompt import CANCEL_CB_PREFIX, cb_cancel
from bot.services.daemon_client import DaemonOffline


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setenv("BRIDGE_BOT_DB_PATH", db)
    monkeypatch.setenv("BRIDGE_BOT_BOT_TOKEN", "fake")
    monkeypatch.setenv("BRIDGE_BOT_BOT_USERNAME", "test_bot")
    monkeypatch.setenv("BRIDGE_BOT_DAEMON_URL", "http://daemon.test")
    return db


def _query(data: str, chat_id: int = 42) -> MagicMock:
    q = MagicMock()
    q.data = data
    q.answer = AsyncMock()
    q.message = MagicMock()
    q.message.chat = MagicMock(id=chat_id)
    return q


@pytest.mark.asyncio
async def test_cb_cancel_kills_daemon_session(db_env, monkeypatch):
    await init_db(db_env)
    monkeypatch.setattr(
        "bot.handlers.prompt.cancel_session",
        AsyncMock(return_value={"ok": True, "killed": True}),
    )
    q = _query(f"{CANCEL_CB_PREFIX}sess-1")
    await cb_cancel(q)
    q.answer.assert_awaited_once()
    msg = q.answer.await_args.args[0] if q.answer.await_args.args else q.answer.await_args.kwargs.get("text", "")
    assert "🛑" in msg or "Прервано" in msg


@pytest.mark.asyncio
async def test_cb_cancel_handles_already_finished(db_env, monkeypatch):
    await init_db(db_env)
    monkeypatch.setattr(
        "bot.handlers.prompt.cancel_session",
        AsyncMock(return_value={"ok": True, "killed": False, "reason": "not_running"}),
    )
    q = _query(f"{CANCEL_CB_PREFIX}sess-1")
    await cb_cancel(q)
    msg = q.answer.await_args.args[0] if q.answer.await_args.args else q.answer.await_args.kwargs.get("text", "")
    assert "ℹ" in msg or "уже" in msg.lower()


@pytest.mark.asyncio
async def test_cb_cancel_daemon_offline(db_env, monkeypatch):
    await init_db(db_env)
    monkeypatch.setattr(
        "bot.handlers.prompt.cancel_session",
        AsyncMock(side_effect=DaemonOffline("nope")),
    )
    q = _query(f"{CANCEL_CB_PREFIX}sess-1")
    await cb_cancel(q)
    # Should still answer the callback to clear the spinner
    q.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_cancel_ignores_unrelated_data(db_env, monkeypatch):
    """Defensive: if dispatcher routes us a different callback by mistake."""
    await init_db(db_env)
    cancel_mock = AsyncMock()
    monkeypatch.setattr("bot.handlers.prompt.cancel_session", cancel_mock)
    q = _query("not-a-cancel:xyz")
    await cb_cancel(q)
    cancel_mock.assert_not_called()
    q.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_cancel_empty_session_id(db_env, monkeypatch):
    """Defensive: malformed callback_data."""
    await init_db(db_env)
    cancel_mock = AsyncMock()
    monkeypatch.setattr("bot.handlers.prompt.cancel_session", cancel_mock)
    q = _query(CANCEL_CB_PREFIX)  # prefix without id
    await cb_cancel(q)
    cancel_mock.assert_not_called()
