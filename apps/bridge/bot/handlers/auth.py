"""Deep-link /start handler.

CEO opens https://t.me/<bot>?start=<token> on Android. Telegram sends
us /start <token>. We forward token to daemon for verification; if
daemon says ok, we whitelist the chat_id.
"""
from __future__ import annotations

import logging

import httpx
from aiogram import Dispatcher
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message

from bot.config import get_settings
from bot.db import audit, authorize_chat

log = logging.getLogger(__name__)

WELCOME_AUTHORIZED = (
    "✅ Готово, твой Telegram привязан к Claude Code на ПК.\n\n"
    "Команды:\n"
    "  /sessions  — список Claude-сессий\n"
    "  /switch <n> — выбрать сессию\n"
    "  /status    — что Claude сейчас делает\n"
    "  /cancel    — прервать текущую сессию\n"
    "  /yolo <prompt> — запустить без pre-flight проверки\n\n"
    "Просто пиши промпт текстом — я отправлю его в Claude."
)

WELCOME_NO_TOKEN = (
    "👋 Привет! Я — Brikko Bridge Bot, мост из Telegram в Claude Code на твоём ПК.\n\n"
    "Чтобы подключиться:\n"
    "1. На своём ПК запусти `start-bridge.bat` (из папки apps/bridge/deploy/).\n"
    "2. В логе supervisor появится ссылка вида\n"
    "   `https://t.me/{bot_username}?start=...`\n"
    "3. Открой эту ссылку на телефоне — я свяжу твой чат с твоим ПК."
)

ERR_TOKEN_INVALID = (
    "❌ Токен невалидный, истёкший или уже использован другим устройством.\n"
    "Запусти `start-bridge.bat` на ПК заново — supervisor сгенерирует новый токен."
)

ERR_DAEMON_OFFLINE = (
    "❌ Не могу достучаться до daemon на твоём ПК.\n"
    "Проверь: запущен ли `start-bridge.bat`? Активен ли SSH-туннель?"
)


async def _verify_token_with_daemon(token: str, chat_id: int) -> tuple[bool, str | None]:
    """Returns (ok, reason). reason is filled when ok=False."""
    settings = get_settings()
    url = f"{settings.daemon_url}/auth/tokens/{token}/consume"
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(url, json={"chat_id": chat_id})
        if r.status_code != 200:
            return False, f"daemon_status_{r.status_code}"
        data = r.json()
        if data.get("ok"):
            return True, None
        return False, data.get("reason", "unknown")
    except (httpx.HTTPError, OSError) as e:
        log.warning("daemon unreachable: %s", e)
        return False, "daemon_offline"


async def cmd_start(message: Message, command: CommandObject) -> None:
    """/start [token]"""
    settings = get_settings()
    token = (command.args or "").strip() if command else ""

    if not token:
        await message.answer(WELCOME_NO_TOKEN.format(bot_username=settings.bot_username))
        return

    ok, reason = await _verify_token_with_daemon(token, message.chat.id)
    if not ok:
        if reason == "daemon_offline":
            await message.answer(ERR_DAEMON_OFFLINE)
        else:
            await message.answer(ERR_TOKEN_INVALID)
        await audit(
            settings.db_path,
            chat_id=message.chat.id,
            event="auth_failed",
            reason=reason,
        )
        return

    await authorize_chat(
        settings.db_path,
        chat_id=message.chat.id,
        nickname=message.from_user.full_name if message.from_user else None,
    )
    await audit(settings.db_path, chat_id=message.chat.id, event="auth_success")
    log.info("authorized chat_id=%s", message.chat.id)
    await message.answer(WELCOME_AUTHORIZED)


def register_handlers(dp: Dispatcher) -> None:
    dp.message.register(cmd_start, CommandStart(deep_link=True))
    dp.message.register(cmd_start, CommandStart())
