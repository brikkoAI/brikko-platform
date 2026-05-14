"""``/follow on | off | status`` slash commands.

Part A of the CLI-mirror feature: the CEO toggles whether the daemon
tails his **current active session** jsonl and mirrors events to this
Telegram chat.

``/follow on``       — registers the chat's active session as a follow.
``/follow off``      — unregisters the chat's active session.
``/follow off all``  — unregister every session for this chat.
``/follow status``   — list active follows.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import get_settings
from bot.db import audit, get_active_session
from shared.follow_protocol import (
    FOLLOW_CONTROL_CHANNEL,
    FollowAction,
    FollowControl,
)

log = logging.getLogger(__name__)


NO_ACTIVE_SESSION = (
    "У тебя ещё не выбрана сессия. Сначала /sessions, потом /switch <n>."
)

HELP_HINT = (
    "Используй: `/follow on` (зеркалить активную сессию) "
    "/ `/follow off` (выключить) / `/follow status` (что зеркалится)."
)


# Module-level Redis handle. Tests patch this with FakeRedis.
_REDIS: Any | None = None


def set_redis(redis: Any) -> None:
    """Wire the Redis client used to publish FollowControl messages."""
    global _REDIS
    _REDIS = redis


def get_redis() -> Any | None:
    return _REDIS


async def _publish(ctrl: FollowControl) -> bool:
    """Publish a FollowControl on the bot→daemon channel. Returns success."""
    if _REDIS is None:
        log.error("follow handler: redis not wired (set_redis() not called)")
        return False
    try:
        await _REDIS.publish(FOLLOW_CONTROL_CHANNEL, ctrl.model_dump_json())
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("follow control publish failed: %s", exc)
        return False


# ============================================================================
# /follow on
# ============================================================================

async def cmd_follow(message: Message, command: CommandObject) -> None:
    """Top-level ``/follow`` dispatcher — args decide the action.

    We deliberately keep the surface small: on/off/status. Anything else
    surfaces the help hint. This makes the command discoverable without
    overloading /help with sub-flags.
    """
    arg = ((command.args or "") if command else "").strip().lower()
    parts = arg.split()
    sub = parts[0] if parts else ""

    if sub == "on":
        await _do_follow_on(message)
        return

    if sub == "off":
        if len(parts) > 1 and parts[1] == "all":
            await _do_follow_off_all(message)
        else:
            await _do_follow_off(message)
        return

    if sub == "status":
        await _do_follow_status(message)
        return

    await message.answer(HELP_HINT)


# ----------------------------------------------------------------------------
# Sub-handlers
# ----------------------------------------------------------------------------

async def _do_follow_on(message: Message) -> None:
    settings = get_settings()
    sess = await get_active_session(settings.db_path, message.chat.id)
    if not sess:
        await message.answer(NO_ACTIVE_SESSION)
        return

    ctrl = FollowControl(
        action=FollowAction.REGISTER,
        tg_chat_id=message.chat.id,
        session_id=sess["session_id"],
    )
    ok = await _publish(ctrl)
    if not ok:
        await message.answer(
            "⚠️ Не смог отправить запрос daemon-у (Redis недоступен). "
            "Проверь `/status`."
        )
        return

    # The daemon will publish a FollowNotice on cli-mirror:<chat_id>
    # which the subscriber renders. We add a local breadcrumb so the
    # CEO sees the request was sent even before the daemon's reply.
    await audit(
        settings.db_path,
        chat_id=message.chat.id,
        event="follow_on_requested",
        session_id=sess["session_id"],
    )


async def _do_follow_off(message: Message) -> None:
    settings = get_settings()
    sess = await get_active_session(settings.db_path, message.chat.id)
    if not sess:
        await message.answer(NO_ACTIVE_SESSION)
        return

    ctrl = FollowControl(
        action=FollowAction.UNREGISTER,
        tg_chat_id=message.chat.id,
        session_id=sess["session_id"],
    )
    ok = await _publish(ctrl)
    if not ok:
        await message.answer(
            "⚠️ Не смог отправить запрос daemon-у (Redis недоступен)."
        )
        return

    await audit(
        settings.db_path,
        chat_id=message.chat.id,
        event="follow_off_requested",
        session_id=sess["session_id"],
    )


async def _do_follow_off_all(message: Message) -> None:
    settings = get_settings()
    ctrl = FollowControl(
        action=FollowAction.UNREGISTER_ALL,
        tg_chat_id=message.chat.id,
        session_id="",
    )
    ok = await _publish(ctrl)
    if not ok:
        await message.answer(
            "⚠️ Не смог отправить запрос daemon-у (Redis недоступен)."
        )
        return
    await audit(
        settings.db_path,
        chat_id=message.chat.id,
        event="follow_off_all_requested",
    )


async def _do_follow_status(message: Message) -> None:
    """Show what we're currently following.

    Daemon responds asynchronously with a status FollowNotice the
    subscriber renders. We just trigger the query here.
    """
    settings = get_settings()

    # Status uses unregister_all? No — we need a dedicated read-only path.
    # For simplicity we just query the daemon over HTTP through the
    # existing daemon_client; but to keep Part A tight we lean on the
    # daemon-side notice mechanism by publishing a synthetic control with
    # action=status. Since the protocol doesn't list status (only
    # register/unregister/unregister_all), we surface a bot-local message
    # instead and add the HTTP endpoint in Part A.2 if needed.
    from bot.services.daemon_client import (
        DaemonOffline,
        list_follows,
    )

    try:
        follows = await list_follows(settings.daemon_url, message.chat.id)
    except DaemonOffline as exc:
        await message.answer(f"❌ Daemon offline ({exc}).")
        return

    if not follows:
        await message.answer("📭 Сейчас ничего не зеркалится. `/follow on` чтобы начать.")
        return

    lines = ["📡 *Активные follow*:\n"]
    for f in follows[:20]:
        lines.append(
            f"  • `{f['session_id'][:8]}` · published={f['events_published']} · "
            f"path=`{_basename(f['path'])}`"
        )
    if len(follows) > 20:
        lines.append(f"  …и ещё {len(follows) - 20}")
    await message.answer("\n".join(lines))


def _basename(path: str) -> str:
    """Last path segment — short, fits inside Telegram code-span."""
    p = path.replace("\\", "/").rstrip("/")
    return p.rsplit("/", 1)[-1] or p


# ============================================================================
# Registration
# ============================================================================

def register_handlers(dp: Dispatcher) -> None:
    dp.message.register(cmd_follow, Command("follow"))
