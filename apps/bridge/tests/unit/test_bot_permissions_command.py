"""Tests for bot.handlers.permissions — /permissions [reset] command."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.db import init_db, set_active_session
from bot.handlers.permissions import cmd_permissions
from bot.services.daemon_client import DaemonOffline


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setenv("BRIDGE_BOT_DB_PATH", db)
    monkeypatch.setenv("BRIDGE_BOT_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("BRIDGE_BOT_BOT_USERNAME", "test_bot")
    monkeypatch.setenv("BRIDGE_BOT_DAEMON_URL", "http://daemon.test")
    return db


def _make_msg(chat_id: int = 42) -> MagicMock:
    msg = MagicMock()
    msg.chat = MagicMock(id=chat_id)
    msg.answer = AsyncMock()
    return msg


def _make_cmd(args: str | None = None) -> MagicMock:
    cmd = MagicMock()
    cmd.args = args
    return cmd


@pytest.mark.asyncio
async def test_permissions_without_session_shows_hint(db_env):
    await init_db(db_env)
    msg = _make_msg()

    await cmd_permissions(msg, _make_cmd(None))

    msg.answer.assert_called_once()
    body = msg.answer.call_args[0][0]
    assert "/switch" in body


@pytest.mark.asyncio
async def test_permissions_list_empty_shows_help_text(db_env):
    await init_db(db_env)
    await set_active_session(
        db_env, chat_id=42, session_id="sid-abcdefgh", project_path="/x"
    )

    with patch(
        "bot.handlers.permissions.get_permissions",
        new=AsyncMock(return_value={"always_allow": [], "always_deny": []}),
    ):
        msg = _make_msg()
        await cmd_permissions(msg, _make_cmd(None))

    body = msg.answer.call_args[0][0]
    assert "всё спрашивается у тебя" in body
    assert "/permissions reset" in body


@pytest.mark.asyncio
async def test_permissions_list_with_cache_shows_entries(db_env):
    await init_db(db_env)
    await set_active_session(
        db_env, chat_id=42, session_id="sid-abcdefgh", project_path="/x"
    )

    with patch(
        "bot.handlers.permissions.get_permissions",
        new=AsyncMock(
            return_value={
                "always_allow": ["Edit", "Write"],
                "always_deny": ["Bash"],
            }
        ),
    ):
        msg = _make_msg()
        await cmd_permissions(msg, _make_cmd(None))

    body = msg.answer.call_args[0][0]
    assert "Edit" in body
    assert "Write" in body
    assert "Bash" in body
    assert "Always allow" in body
    assert "Always deny" in body


@pytest.mark.asyncio
async def test_permissions_reset_calls_daemon_and_confirms(db_env):
    await init_db(db_env)
    await set_active_session(
        db_env, chat_id=42, session_id="sid-abcdefgh", project_path="/x"
    )

    reset_mock = AsyncMock(return_value={"ok": True})
    with patch("bot.handlers.permissions.reset_permissions", new=reset_mock):
        msg = _make_msg()
        await cmd_permissions(msg, _make_cmd("reset"))

    reset_mock.assert_called_once()
    body = msg.answer.call_args[0][0]
    assert "сброшен" in body.lower()


@pytest.mark.asyncio
async def test_permissions_daemon_offline_for_list(db_env):
    await init_db(db_env)
    await set_active_session(
        db_env, chat_id=42, session_id="sid-abcdefgh", project_path="/x"
    )

    with patch(
        "bot.handlers.permissions.get_permissions",
        new=AsyncMock(side_effect=DaemonOffline("ssh tunnel down")),
    ):
        msg = _make_msg()
        await cmd_permissions(msg, _make_cmd(None))

    body = msg.answer.call_args[0][0]
    assert "Daemon недоступен" in body


@pytest.mark.asyncio
async def test_permissions_daemon_offline_for_reset(db_env):
    await init_db(db_env)
    await set_active_session(
        db_env, chat_id=42, session_id="sid-abcdefgh", project_path="/x"
    )

    with patch(
        "bot.handlers.permissions.reset_permissions",
        new=AsyncMock(side_effect=DaemonOffline("ssh tunnel down")),
    ):
        msg = _make_msg()
        await cmd_permissions(msg, _make_cmd("reset"))

    body = msg.answer.call_args[0][0]
    assert "Daemon недоступен" in body
