"""Reject messages from non-authorized chats.

Exception: /start and /help — those are how a new user discovers what to do.
Everything else (slash commands, plain text) requires the chat to be in
``authorized_chats`` (handled in handlers/auth.py:cmd_start).
"""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import get_settings
from bot.db import is_authorized, touch_last_seen


_PUBLIC_COMMANDS = ("/start", "/help")


class WhitelistMiddleware(BaseMiddleware):
    """Drops updates from chats not in `authorized_chats`."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id = _extract_chat_id(event)
        if chat_id is None:
            return await handler(event, data)

        # Public commands always pass — auth handler validates token itself.
        text = _extract_text(event) or ""
        if any(text.startswith(c) for c in _PUBLIC_COMMANDS):
            return await handler(event, data)

        settings = get_settings()
        if not await is_authorized(settings.db_path, chat_id):
            await _send_unauthorized(event)
            return None

        await touch_last_seen(settings.db_path, chat_id)
        return await handler(event, data)


def _extract_chat_id(event: TelegramObject) -> int | None:
    if isinstance(event, Message):
        return event.chat.id
    if isinstance(event, CallbackQuery) and event.message:
        return event.message.chat.id
    return None


def _extract_text(event: TelegramObject) -> str | None:
    if isinstance(event, Message):
        return event.text
    return None


async def _send_unauthorized(event: TelegramObject) -> None:
    text = (
        "🚫 Этот чат не привязан. "
        "Запусти `start-bridge.bat` на ПК — supervisor выведет ссылку для подключения."
    )
    if isinstance(event, Message):
        try:
            await event.answer(text)
        except Exception:
            pass
    elif isinstance(event, CallbackQuery):
        try:
            await event.answer(text, show_alert=True)
        except Exception:
            pass
