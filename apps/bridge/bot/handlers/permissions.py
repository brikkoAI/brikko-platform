"""``/permissions`` command — list / reset the in-session always-allow cache.

CEO decision 2026-05-11 #4: there must be a way to clear "always-allow"
decisions for the current session, in case CEO taps one by mistake.

Usage:
  /permissions             — show current cache for the active session
  /permissions reset       — clear the entire cache for the active session
  /permissions list        — alias of bare /permissions
"""

from __future__ import annotations

import logging

from aiogram import Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import get_settings
from bot.db import get_active_session
from bot.services.daemon_client import DaemonOffline, get_permissions, reset_permissions

log = logging.getLogger(__name__)


NO_ACTIVE_SESSION = "Сначала выбери сессию: /sessions → /switch <n>."


async def cmd_permissions(message: Message, command: CommandObject) -> None:
    settings = get_settings()
    sess = await get_active_session(settings.db_path, message.chat.id)
    if not sess:
        await message.answer(NO_ACTIVE_SESSION)
        return

    session_id = sess["session_id"]
    arg = (command.args or "").strip().lower() if command else ""

    if arg == "reset":
        try:
            await reset_permissions(settings.daemon_url, session_id)
        except DaemonOffline:
            await message.answer("❌ Daemon недоступен.")
            return
        await message.answer(
            "🔄 Permissions сброшены — все always-allow / always-deny "
            f"для сессии `{session_id[:8]}…` удалены."
        )
        return

    # Default = list
    try:
        perms = await get_permissions(settings.daemon_url, session_id)
    except DaemonOffline:
        await message.answer("❌ Daemon недоступен.")
        return

    allow = sorted(perms.get("always_allow", []))
    deny = sorted(perms.get("always_deny", []))
    if not allow and not deny:
        await message.answer(
            f"📋 Permissions для сессии `{session_id[:8]}…`:\n"
            "_всё спрашивается у тебя_\n\n"
            "Команды:\n"
            "  /permissions reset — сбросить (если случайно нажал «всегда»)"
        )
        return

    lines = [f"📋 Permissions для сессии `{session_id[:8]}…`:"]
    if allow:
        lines.append("")
        lines.append("✅ Always allow:")
        for t in allow:
            lines.append(f"  • `{t}`")
    if deny:
        lines.append("")
        lines.append("🔒 Always deny:")
        for t in deny:
            lines.append(f"  • `{t}`")
    lines.append("")
    lines.append("Сбросить: /permissions reset")
    await message.answer("\n".join(lines))


def register_handlers(dp: Dispatcher) -> None:
    dp.message.register(cmd_permissions, Command("permissions"))
