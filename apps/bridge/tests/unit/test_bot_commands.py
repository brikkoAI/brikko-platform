"""Tests for bot.handlers.commands — /sessions /switch /status /cancel /help /yolo /safe."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.db import (
    audit,
    authorize_chat,
    get_active_session,
    init_db,
    set_active_session,
)
from bot.handlers.commands import (
    cmd_audit,
    cmd_cancel,
    cmd_help,
    cmd_safe,
    cmd_sessions,
    cmd_status,
    cmd_switch,
    cmd_yolo,
)
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
    msg.text = ""
    return msg


def _make_cmd(args: str | None = None) -> MagicMock:
    cmd = MagicMock()
    cmd.args = args
    return cmd


# ============================================================================
# /sessions
# ============================================================================

@pytest.mark.asyncio
async def test_sessions_renders_numbered_list(db_env, monkeypatch):
    fake_sessions = [
        {
            "session_id": "abc",
            "project_path": "C--Users-x-proj",
            "last_modified": 1700000000,
            "summary": "Working on auth bug",
        },
        {
            "session_id": "def",
            "project_path": "D--repo-bar",
            "last_modified": 1700000000,
            "summary": "Refactoring DB layer",
        },
    ]
    monkeypatch.setattr(
        "bot.handlers.commands.list_sessions", AsyncMock(return_value=fake_sessions)
    )
    msg = _make_msg()
    await cmd_sessions(msg)
    sent = msg.answer.call_args[0][0]
    assert "1." in sent and "2." in sent
    assert "auth bug" in sent
    assert "Refactoring" in sent
    assert "/switch" in sent


@pytest.mark.asyncio
async def test_sessions_empty_message(db_env, monkeypatch):
    monkeypatch.setattr(
        "bot.handlers.commands.list_sessions", AsyncMock(return_value=[])
    )
    msg = _make_msg()
    await cmd_sessions(msg)
    sent = msg.answer.call_args[0][0]
    assert "📭" in sent or "пока нет" in sent.lower()


@pytest.mark.asyncio
async def test_sessions_daemon_offline(db_env, monkeypatch):
    async def boom(*a, **kw):
        raise DaemonOffline("nope")

    monkeypatch.setattr("bot.handlers.commands.list_sessions", boom)
    msg = _make_msg()
    await cmd_sessions(msg)
    sent = msg.answer.call_args[0][0]
    assert "❌" in sent or "daemon" in sent.lower()


# ============================================================================
# /switch
# ============================================================================

@pytest.mark.asyncio
async def test_switch_sets_active_session(db_env, monkeypatch):
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42)

    fake_sessions = [
        {"session_id": "first", "project_path": "/x", "last_modified": 0, "summary": None},
        {"session_id": "second", "project_path": "/y", "last_modified": 0, "summary": None},
    ]
    monkeypatch.setattr(
        "bot.handlers.commands.list_sessions", AsyncMock(return_value=fake_sessions)
    )

    msg = _make_msg(42)
    await cmd_switch(msg, _make_cmd("2"))

    sess = await get_active_session(db_env, 42)
    assert sess is not None
    assert sess["session_id"] == "second"


@pytest.mark.asyncio
async def test_switch_invalid_arg(db_env):
    msg = _make_msg(42)
    await cmd_switch(msg, _make_cmd("abc"))
    sent = msg.answer.call_args[0][0]
    assert "Использование" in sent or "/switch" in sent


@pytest.mark.asyncio
async def test_switch_out_of_range(db_env, monkeypatch):
    monkeypatch.setattr(
        "bot.handlers.commands.list_sessions",
        AsyncMock(return_value=[{"session_id": "a", "project_path": "/x", "last_modified": 0, "summary": None}]),
    )
    msg = _make_msg(42)
    await cmd_switch(msg, _make_cmd("99"))
    sent = msg.answer.call_args[0][0]
    assert "99" in sent or "Нет сессии" in sent


# ============================================================================
# /status
# ============================================================================

@pytest.mark.asyncio
async def test_status_with_active_session(db_env, monkeypatch):
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42)
    await set_active_session(db_env, 42, "abcdef12345", "C--proj-foo")

    monkeypatch.setattr(
        "bot.handlers.commands.health",
        AsyncMock(return_value={"status": "ok", "version": "0.1.0", "claude_version": "claude 2.x"}),
    )
    monkeypatch.setattr(
        "bot.handlers.commands.active_sessions", AsyncMock(return_value=["abcdef12345"])
    )
    monkeypatch.setattr(
        "bot.handlers.commands.list_processes", AsyncMock(return_value=[])
    )

    msg = _make_msg(42)
    await cmd_status(msg)
    sent = msg.answer.call_args[0][0]
    assert "ok" in sent.lower() or "✓" in sent
    assert "abcdef12" in sent
    assert "🔧" in sent  # in-flight indicator


@pytest.mark.asyncio
async def test_status_filters_internal_worker_processes(db_env, monkeypatch):
    """Claude Cowork spawns ~13 worker procs into C:\\WINDOWS\\system32 —
    they never write to a user jsonl. /status must hide them so the user
    sees only their real project Claude instances."""
    await init_db(db_env)
    monkeypatch.setattr(
        "bot.handlers.commands.health",
        AsyncMock(return_value={"status": "ok", "version": "0.1.0", "claude_version": "claude 2.x"}),
    )
    monkeypatch.setattr(
        "bot.handlers.commands.active_sessions", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "bot.handlers.commands.list_processes",
        AsyncMock(
            return_value=[
                {"pid": 1736, "kind": "cli", "cwd": "C:\\WINDOWS\\system32", "started_at": 0},
                {"pid": 9416, "kind": "cli", "cwd": "C:/Program Files/Claude/...", "started_at": 0},
                {"pid": 26220, "kind": "cli", "cwd": "C:/Users/x/Стартап", "started_at": 0},
            ]
        ),
    )
    msg = _make_msg(42)
    await cmd_status(msg)
    sent = msg.answer.call_args[0][0]
    assert "Стартап" in sent
    assert "system32" not in sent.lower()
    assert "program files" not in sent.lower()
    # Count line shows filtered count (1 project, 1 proc), not raw 3
    assert "проектов: 1" in sent and "всего: 1" in sent


@pytest.mark.asyncio
async def test_status_shows_cli_claude_processes(db_env, monkeypatch):
    """When user has interactive Claude open in CLI, /status surfaces it."""
    await init_db(db_env)
    monkeypatch.setattr(
        "bot.handlers.commands.health",
        AsyncMock(return_value={"status": "ok", "version": "0.1.0", "claude_version": "claude 2.x"}),
    )
    monkeypatch.setattr(
        "bot.handlers.commands.active_sessions", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "bot.handlers.commands.list_processes",
        AsyncMock(
            return_value=[
                {"pid": 12345, "kind": "cli", "cwd": "C:/Users/x/Стартап", "started_at": 1700000000.0},
                {"pid": 67890, "kind": "daemon-spawned", "cwd": "C:/Users/x/Стартап", "started_at": 1700000100.0},
            ]
        ),
    )

    msg = _make_msg(42)
    await cmd_status(msg)
    sent = msg.answer.call_args[0][0]
    # CLI shown
    assert "🖥" in sent
    # Project label rendered (basename of cwd)
    assert "Стартап" in sent
    # daemon-spawned NOT in CLI section
    assert "67890" not in sent


@pytest.mark.asyncio
async def test_status_groups_cli_by_cwd(db_env, monkeypatch):
    """5 procs in 2 projects → 2 grouped rows with (×N) counts."""
    await init_db(db_env)
    monkeypatch.setattr(
        "bot.handlers.commands.health",
        AsyncMock(return_value={"status": "ok", "version": "0.1.0", "claude_version": "claude 2.x"}),
    )
    monkeypatch.setattr(
        "bot.handlers.commands.active_sessions", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "bot.handlers.commands.list_processes",
        AsyncMock(return_value=[
            {"pid": 1, "kind": "cli", "cwd": "C:/Users/x/Стартап", "started_at": 1000},
            {"pid": 2, "kind": "cli", "cwd": "C:/Users/x/Стартап", "started_at": 1100},
            {"pid": 3, "kind": "cli", "cwd": "C:/Users/x/Стартап", "started_at": 1200},
            {"pid": 4, "kind": "cli", "cwd": "C:/Users/x/Заказ-наряды", "started_at": 2000},
            {"pid": 5, "kind": "cli", "cwd": "C:/Users/x/Заказ-наряды", "started_at": 2100},
        ]),
    )
    msg = _make_msg(42)
    await cmd_status(msg)
    sent = msg.answer.call_args[0][0]
    assert "проектов: 2, всего: 5" in sent
    assert "(×3)" in sent  # Стартап group
    assert "(×2)" in sent  # Заказ-наряды group


@pytest.mark.asyncio
async def test_status_singleton_groups_omit_count(db_env, monkeypatch):
    """One proc per cwd → no ' (×1)' clutter."""
    await init_db(db_env)
    monkeypatch.setattr(
        "bot.handlers.commands.health",
        AsyncMock(return_value={"status": "ok", "version": "0.1.0", "claude_version": "claude 2.x"}),
    )
    monkeypatch.setattr(
        "bot.handlers.commands.active_sessions", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "bot.handlers.commands.list_processes",
        AsyncMock(return_value=[
            {"pid": 1, "kind": "cli", "cwd": "C:/proj", "started_at": 1000},
        ]),
    )
    msg = _make_msg(42)
    await cmd_status(msg)
    sent = msg.answer.call_args[0][0]
    assert "(×1)" not in sent
    assert "(×" not in sent


@pytest.mark.asyncio
async def test_status_daemon_offline(db_env, monkeypatch):
    await init_db(db_env)

    async def boom(*a, **kw):
        raise DaemonOffline("tunnel down")

    monkeypatch.setattr("bot.handlers.commands.health", boom)
    monkeypatch.setattr(
        "bot.handlers.commands.active_sessions", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "bot.handlers.commands.list_processes", AsyncMock(return_value=[])
    )

    msg = _make_msg(42)
    await cmd_status(msg)
    sent = msg.answer.call_args[0][0]
    assert "offline" in sent.lower() or "❌" in sent


# ============================================================================
# /cancel
# ============================================================================

@pytest.mark.asyncio
async def test_cancel_no_active_session(db_env):
    await init_db(db_env)
    msg = _make_msg(42)
    await cmd_cancel(msg)
    sent = msg.answer.call_args[0][0]
    assert "не выбрана" in sent.lower() or "/sessions" in sent


@pytest.mark.asyncio
async def test_cancel_kills_running_session(db_env, monkeypatch):
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42)
    await set_active_session(db_env, 42, "sess-1", "/x")

    monkeypatch.setattr(
        "bot.handlers.commands.cancel_session",
        AsyncMock(return_value={"ok": True, "killed": True}),
    )

    msg = _make_msg(42)
    await cmd_cancel(msg)
    sent = msg.answer.call_args[0][0]
    assert "🛑" in sent or "Прервано" in sent


@pytest.mark.asyncio
async def test_cancel_nothing_running(db_env, monkeypatch):
    await init_db(db_env)
    await authorize_chat(db_env, chat_id=42)
    await set_active_session(db_env, 42, "sess-1", "/x")

    monkeypatch.setattr(
        "bot.handlers.commands.cancel_session",
        AsyncMock(return_value={"ok": True, "killed": False, "reason": "not_running"}),
    )

    msg = _make_msg(42)
    await cmd_cancel(msg)
    sent = msg.answer.call_args[0][0]
    assert "ℹ" in sent or "не работает" in sent.lower()


# ============================================================================
# /help
# ============================================================================

@pytest.mark.asyncio
async def test_help_lists_commands():
    msg = _make_msg()
    await cmd_help(msg)
    sent = msg.answer.call_args[0][0]
    for cmd in ("/sessions", "/switch", "/status", "/cancel", "/yolo", "/safe"):
        assert cmd in sent


# ============================================================================
# /audit
# ============================================================================

@pytest.mark.asyncio
async def test_audit_empty(db_env):
    await init_db(db_env)
    msg = _make_msg(42)
    await cmd_audit(msg, _make_cmd(None))
    sent = msg.answer.call_args[0][0]
    assert "пуст" in sent.lower()


@pytest.mark.asyncio
async def test_audit_returns_recent_events(db_env):
    await init_db(db_env)
    await audit(db_env, chat_id=42, event="auth_success")
    await audit(db_env, chat_id=42, event="session_switched", session_id="abcdef1234567890")
    await audit(db_env, chat_id=42, event="prompt_sent", prompt_len=42)
    await audit(db_env, chat_id=42, event="preflight_blocked", reasons=["rm -rf"])

    msg = _make_msg(42)
    await cmd_audit(msg, _make_cmd(None))
    sent = msg.answer.call_args[0][0]
    assert "auth_success" in sent
    assert "session_switched" in sent
    assert "abcdef12" in sent  # truncated session id
    assert "42 chars" in sent or "42" in sent
    assert "rm -rf" in sent


@pytest.mark.asyncio
async def test_audit_respects_limit_arg(db_env):
    await init_db(db_env)
    for i in range(15):
        await audit(db_env, chat_id=42, event="prompt_sent", prompt_len=i)

    msg = _make_msg(42)
    await cmd_audit(msg, _make_cmd("3"))
    sent = msg.answer.call_args[0][0]
    # Header should reflect 3 rows
    assert "3 событ" in sent or "Последние 3" in sent


@pytest.mark.asyncio
async def test_audit_isolates_per_chat(db_env):
    await init_db(db_env)
    await audit(db_env, chat_id=42, event="auth_success")
    await audit(db_env, chat_id=99, event="prompt_sent", prompt_len=1)

    msg = _make_msg(42)
    await cmd_audit(msg, _make_cmd(None))
    sent = msg.answer.call_args[0][0]
    assert "auth_success" in sent
    assert "prompt_sent" not in sent  # chat 99's row must not leak


# ============================================================================
# /yolo and /safe modifiers
# ============================================================================

@pytest.mark.asyncio
async def test_yolo_without_args_shows_usage(db_env):
    msg = _make_msg(42)
    await cmd_yolo(msg, _make_cmd(""))
    sent = msg.answer.call_args[0][0]
    assert "/yolo" in sent


@pytest.mark.asyncio
async def test_safe_without_args_shows_usage(db_env):
    msg = _make_msg(42)
    await cmd_safe(msg, _make_cmd(""))
    sent = msg.answer.call_args[0][0]
    assert "/safe" in sent


@pytest.mark.asyncio
async def test_yolo_forwards_to_handle_prompt_with_skip_preflight(db_env, monkeypatch):
    fake_handle = AsyncMock()
    monkeypatch.setattr("bot.handlers.prompt.handle_prompt", fake_handle)

    msg = _make_msg(42)
    msg.text = "/yolo rm -rf /"
    await cmd_yolo(msg, _make_cmd("rm -rf /"))

    fake_handle.assert_awaited_once()
    kwargs = fake_handle.await_args.kwargs
    assert kwargs.get("skip_preflight") is True
    # Prompt is passed via text_override (Message is frozen, can't mutate)
    assert kwargs.get("text_override") == "rm -rf /"


@pytest.mark.asyncio
async def test_safe_forwards_to_handle_prompt_with_strict(db_env, monkeypatch):
    fake_handle = AsyncMock()
    monkeypatch.setattr("bot.handlers.prompt.handle_prompt", fake_handle)

    msg = _make_msg(42)
    msg.text = "/safe edit main.py"
    await cmd_safe(msg, _make_cmd("edit main.py"))

    fake_handle.assert_awaited_once()
    kwargs = fake_handle.await_args.kwargs
    assert kwargs.get("strict_preflight") is True
    assert kwargs.get("text_override") == "edit main.py"


@pytest.mark.asyncio
async def test_yolo_does_not_mutate_message(db_env, monkeypatch):
    """Regression: aiogram's Message is a frozen pydantic model.

    Mutating message.text raised ValidationError(frozen_instance).
    cmd_yolo/cmd_safe must pass the prompt out-of-band via text_override.
    """
    fake_handle = AsyncMock()
    monkeypatch.setattr("bot.handlers.prompt.handle_prompt", fake_handle)

    # Build a fake message that ERRORS on .text assignment, mimicking
    # the real frozen-pydantic behaviour.
    class FrozenMsg:
        def __init__(self):
            self.chat = MagicMock(id=42)
            self.answer = AsyncMock()
            self._text = "/yolo привет"

        @property
        def text(self):
            return self._text

        @text.setter
        def text(self, _v):
            raise RuntimeError("frozen")

    msg = FrozenMsg()
    # Must not raise — proves we no longer try to set message.text
    await cmd_yolo(msg, _make_cmd("привет"))
    fake_handle.assert_awaited_once()
    assert fake_handle.await_args.kwargs.get("text_override") == "привет"
