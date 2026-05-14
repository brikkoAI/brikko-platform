"""Plain-text prompt handler.

When the CEO sends a regular message (not a slash command) from an
authorized chat, treat it as a Claude prompt:

  1. Look up the active session for this chat (must have run /switch first)
  2. POST it to daemon /sessions/{id}/send and stream the SSE response
  3. Render BridgeEvents into Telegram text via StreamRenderer
  4. Edit the same message in place every ~0.5s as new content arrives
  5. When the message would overflow 4096 chars, send a new message and
     continue rendering into it

Telegram silently rejects ``edit_message_text`` when the new text is
identical to the current one, so we track ``last_sent_text`` per chunk
to skip no-op edits.
"""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from aiogram.types import CallbackQuery

from bot.config import get_settings
from bot.db import audit, get_active_session
from bot.services import preflight
from bot.services.daemon_client import DaemonOffline, cancel_session, stream_send
from bot.services.stream_renderer import StreamRenderer

log = logging.getLogger(__name__)

# Minimum gap between consecutive edits of the same message. Telegram's
# per-chat edit limit is roughly 1/sec; we leave headroom.
EDIT_THROTTLE_SEC = 0.6

# Callback prefix for the inline Cancel button. Format: ``cancel:<sid>``
# so multiple in-flight prompts (rare) target the right session.
CANCEL_CB_PREFIX = "cancel:"


def _cancel_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🛑 Прервать",
                callback_data=f"{CANCEL_CB_PREFIX}{session_id}",
            )
        ]]
    )

NO_ACTIVE_SESSION = (
    "У тебя пока нет выбранной Claude-сессии.\n"
    "Команды:\n"
    "  /sessions  — показать список\n"
    "  /switch <n> — выбрать одну из них"
)

DAEMON_OFFLINE_MSG = (
    "❌ Не могу достучаться до daemon на твоём ПК.\n"
    "Проверь что `start-bridge.bat` запущен и SSH-туннель активен."
)


async def _safe_edit(
    msg: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit a message, swallowing benign Telegram errors.

    Telegram returns ``message is not modified`` when nothing changed,
    and 429 retry-after if we edit too fast — both are non-fatal.
    """
    try:
        await msg.edit_text(text, reply_markup=reply_markup)
    except TelegramRetryAfter as e:
        # Telegram-imposed back-off; pause and try once more.
        await asyncio.sleep(e.retry_after)
        try:
            await msg.edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest:
            pass
    except TelegramBadRequest as e:
        # "message is not modified" or "message to edit not found" —
        # both are recoverable by ignoring.
        if "not modified" not in str(e).lower():
            log.debug("edit_text failed: %s", e)


async def _flush(
    chat_msgs: list[Message],
    chunks: list[str],
    last_sent: list[str],
    source: Message,
    cancel_kb: InlineKeyboardMarkup | None = None,
) -> None:
    """Apply all current chunks to Telegram, sending new messages as needed.

    The cancel keyboard is attached only to the LAST chunk message — that's
    where the user is reading right now, and Telegram only renders inline
    keyboards on the message they're under. Mid-thread chunks stay clean.
    """
    last_idx = len(chunks) - 1
    for i, chunk in enumerate(chunks):
        kb = cancel_kb if i == last_idx else None
        if i < len(chat_msgs):
            # Existing message — edit only if text changed
            if last_sent[i] != chunk:
                await _safe_edit(chat_msgs[i], chunk, reply_markup=kb)
                last_sent[i] = chunk
        else:
            # Need a new message for this overflow chunk
            new_msg = await source.answer(chunk, reply_markup=kb)
            chat_msgs.append(new_msg)
            last_sent.append(chunk)


async def handle_prompt(
    message: Message,
    *,
    skip_preflight: bool = False,
    strict_preflight: bool = False,
    text_override: str | None = None,
) -> None:
    """Handle a plain-text prompt.

    Args:
      message:         aiogram Message — used for chat_id and answer().
      skip_preflight:  True when invoked via /yolo — bypass risk check.
      strict_preflight: True when invoked via /safe — flag any write verb.
      text_override:   When non-None, this is used as the prompt instead of
                       ``message.text``. /yolo and /safe pass the part
                       AFTER the slash-command here. Needed because
                       aiogram's Message is a frozen pydantic model — we
                       can't mutate ``message.text`` in place.
    """
    settings = get_settings()
    chat_id = message.chat.id
    raw = text_override if text_override is not None else (message.text or "")
    text = raw.strip()
    if not text:
        return

    # Slash commands route to their own handlers — defensive guard
    if text.startswith("/"):
        return

    sess = await get_active_session(settings.db_path, chat_id)
    if not sess:
        await message.answer(NO_ACTIVE_SESSION)
        return

    # Pre-flight risk check
    if not skip_preflight:
        verdict = preflight.check(text, strict=strict_preflight)
        if verdict.risky:
            await audit(
                settings.db_path,
                chat_id=chat_id,
                event="preflight_blocked",
                reasons=list(verdict.reasons),
            )
            await message.answer(
                "⚠️ Pre-flight: нашёл рискованные паттерны: "
                f"`{verdict.reason_text}`.\n\n"
                "Если уверен — отправь снова с префиксом `/yolo`."
            )
            return

    session_id: str = sess["session_id"]
    # `claude --resume <id>` needs to be launched from the same working
    # directory the session was created in — otherwise it reports
    # "No conversation found with session ID". /switch stores the real cwd
    # we extracted from the session jsonl (see daemon/session_discovery.py).
    cwd: str | None = sess.get("project_path") or None

    # First placeholder message — we'll edit this in place as text streams.
    # Inline 🛑 Прервать lives under the active streaming chunk and is
    # removed in the final flush.
    cancel_kb = _cancel_keyboard(session_id)
    placeholder = await message.answer("…", reply_markup=cancel_kb)

    chat_msgs: list[Message] = [placeholder]
    last_sent: list[str] = [""]
    renderer = StreamRenderer()
    last_edit_at = 0.0

    await audit(
        settings.db_path,
        chat_id=chat_id,
        event="prompt_sent",
        session_id=session_id,
        prompt_len=len(text),
    )

    try:
        async for event in stream_send(
            settings.daemon_url,
            session_id=session_id,
            prompt=text,
            cwd=cwd,
            # /yolo also bypasses the CLI-vs-bridge mutex on the daemon
            # side. The user already opted into "I know what I'm doing".
            force=skip_preflight,
        ):
            renderer.feed(event)
            now = time.monotonic()
            if now - last_edit_at >= EDIT_THROTTLE_SEC:
                await _flush(
                    chat_msgs, renderer.chunks_for_send(), last_sent,
                    message, cancel_kb=cancel_kb,
                )
                last_edit_at = now
    except DaemonOffline as e:
        log.warning("daemon offline mid-stream: %s", e)
        await _safe_edit(chat_msgs[0], DAEMON_OFFLINE_MSG)
        await audit(
            settings.db_path,
            chat_id=chat_id,
            event="prompt_failed",
            reason="daemon_offline",
        )
        return
    except Exception as e:
        log.exception("prompt handler error: %s", e)
        await _safe_edit(chat_msgs[0], f"❌ Внутренняя ошибка: {e}")
        await audit(
            settings.db_path,
            chat_id=chat_id,
            event="prompt_failed",
            reason="exception",
            error=str(e)[:200],
        )
        return

    # Final flush — render whatever's left, including the StreamEnd footer.
    # No cancel_kb this time: the run is over, button is no longer useful.
    chunks = renderer.chunks_for_send()
    if not chunks:
        chunks = ["(пусто — Claude ничего не ответил)"]
    await _flush(chat_msgs, chunks, last_sent, message, cancel_kb=None)

    await audit(
        settings.db_path,
        chat_id=chat_id,
        event="prompt_done",
        session_id=session_id,
        had_error=renderer.has_error(),
    )


async def cb_cancel(query: CallbackQuery) -> None:
    """Handle the inline 🛑 Прервать button.

    callback_data = ``cancel:<session_id>``. We:
      1. Acknowledge with a small toast (Telegram requires answering
         every callback to clear the spinner).
      2. Tell the daemon to kill the live ``claude --resume`` proc.
      3. Audit it.
    The streaming loop in ``handle_prompt`` will see the subprocess
    exit and emit a final flush with the partial text + footer.
    """
    settings = get_settings()
    data = query.data or ""
    if not data.startswith(CANCEL_CB_PREFIX):
        await query.answer()
        return
    session_id = data[len(CANCEL_CB_PREFIX):]
    if not session_id:
        await query.answer("Нет session_id", show_alert=False)
        return

    chat_id = query.message.chat.id if query.message else 0
    try:
        result = await cancel_session(settings.daemon_url, session_id)
    except DaemonOffline:
        await query.answer("Daemon недоступен", show_alert=True)
        return

    killed = result.get("killed", False)
    await query.answer("🛑 Прервано" if killed else "ℹ️ Уже завершилось", show_alert=False)
    await audit(
        settings.db_path,
        chat_id=chat_id,
        event="cancel_clicked",
        session_id=session_id,
        killed=killed,
    )


def register_handlers(dp: Dispatcher) -> None:
    # Match anything that's NOT a command (commands start with /)
    dp.message.register(handle_prompt, F.text & ~F.text.startswith("/"))
    # Inline 🛑 Прервать button under streaming replies
    dp.callback_query.register(cb_cancel, F.data.startswith(CANCEL_CB_PREFIX))
