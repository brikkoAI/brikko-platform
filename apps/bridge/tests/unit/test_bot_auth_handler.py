"""Tests for bot.handlers.auth — /start [token]."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import respx
import httpx
from aiogram.filters import CommandObject

from bot.db import init_db, is_authorized
from bot.handlers.auth import cmd_start


@pytest.fixture
def daemon_url():
    return "http://daemon.test"


@pytest.fixture
def settings_env(tmp_path, monkeypatch, daemon_url):
    db = str(tmp_path / "test.db")
    monkeypatch.setenv("BRIDGE_BOT_DB_PATH", db)
    monkeypatch.setenv("BRIDGE_BOT_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("BRIDGE_BOT_BOT_USERNAME", "test_bot")
    monkeypatch.setenv("BRIDGE_BOT_DAEMON_URL", daemon_url)
    return db


def _make_msg(chat_id: int = 42, full_name: str = "Test User") -> MagicMock:
    msg = MagicMock()
    msg.chat = MagicMock(id=chat_id, type="private")
    msg.from_user = MagicMock(full_name=full_name)
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
@respx.mock
async def test_start_with_valid_token_authorizes_chat(settings_env, daemon_url):
    db = settings_env
    await init_db(db)

    respx.post(f"{daemon_url}/auth/tokens/abc-token/consume").mock(
        return_value=httpx.Response(200, json={"ok": True, "already_paired": False})
    )

    msg = _make_msg(chat_id=42)
    cmd = CommandObject(args="abc-token")
    await cmd_start(msg, cmd)

    assert await is_authorized(db, 42)
    msg.answer.assert_called_once()
    sent_text = msg.answer.call_args[0][0]
    assert "Готово" in sent_text or "привязан" in sent_text.lower()


@pytest.mark.asyncio
@respx.mock
async def test_start_with_invalid_token_rejected(settings_env, daemon_url):
    db = settings_env
    await init_db(db)

    respx.post(f"{daemon_url}/auth/tokens/bad-token/consume").mock(
        return_value=httpx.Response(200, json={"ok": False, "reason": "expired"})
    )

    msg = _make_msg(chat_id=99)
    cmd = CommandObject(args="bad-token")
    await cmd_start(msg, cmd)

    assert not await is_authorized(db, 99)
    sent = msg.answer.call_args[0][0]
    assert "невалидн" in sent.lower() or "истёк" in sent.lower() or "❌" in sent


@pytest.mark.asyncio
@respx.mock
async def test_start_when_daemon_offline_friendly_message(settings_env, daemon_url):
    db = settings_env
    await init_db(db)

    respx.post(f"{daemon_url}/auth/tokens/x/consume").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    msg = _make_msg(chat_id=33)
    cmd = CommandObject(args="x")
    await cmd_start(msg, cmd)

    assert not await is_authorized(db, 33)
    sent = msg.answer.call_args[0][0]
    assert "daemon" in sent.lower() or "ПК" in sent or "туннел" in sent.lower()


@pytest.mark.asyncio
async def test_start_without_token_shows_setup_instructions(settings_env):
    db = settings_env
    await init_db(db)

    msg = _make_msg(chat_id=11)
    cmd = CommandObject(args=None)
    await cmd_start(msg, cmd)

    assert not await is_authorized(db, 11)
    sent = msg.answer.call_args[0][0]
    assert "start-bridge" in sent.lower() or "supervisor" in sent.lower()


@pytest.mark.asyncio
@respx.mock
async def test_start_idempotent_for_already_paired_chat(settings_env, daemon_url):
    """If CEO re-opens deep-link on same device, daemon returns already_paired=True
    and we still successfully authorize."""
    db = settings_env
    await init_db(db)

    respx.post(f"{daemon_url}/auth/tokens/old-token/consume").mock(
        return_value=httpx.Response(200, json={"ok": True, "already_paired": True})
    )

    msg = _make_msg(chat_id=42)
    cmd = CommandObject(args="old-token")
    await cmd_start(msg, cmd)

    assert await is_authorized(db, 42)
