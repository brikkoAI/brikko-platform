"""Bot entry-point. Run::

    python -m bot.main

Bot lives on Aeza, polls Telegram, calls daemon over SSH reverse tunnel.

Phase 8: also subscribes to ``bridge:approval-request`` on Redis to mediate
the daemon's can_use_tool callbacks. The subscriber runs as a background
task in the same asyncio loop as the aiogram dispatcher.
"""
from __future__ import annotations

import asyncio
import logging

import redis.asyncio as redis_async
from aiogram import Bot, Dispatcher

from bot.config import get_settings
from bot.db import audit, init_db
from bot.handlers import register_all
from bot.handlers.follow import set_redis as set_follow_redis
from bot.middleware import WhitelistMiddleware
from bot.services.approval_handler import ApprovalRouter, set_shared_router
from bot.services.cli_mirror_subscriber import (
    CliMirrorSubscriber,
    set_shared_subscriber,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot.main")


async def main() -> None:
    settings = get_settings()
    await init_db(settings.db_path)

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    # Whitelist gate runs on every update (Message + CallbackQuery).
    dp.message.middleware(WhitelistMiddleware())
    dp.callback_query.middleware(WhitelistMiddleware())

    # Phase 8 — set up Redis client + approval router BEFORE registering
    # handlers, so the callback handler can resolve the shared router.
    redis_client = redis_async.from_url(settings.redis_url)
    router = ApprovalRouter(
        bot=bot,
        redis=redis_client,
        response_ttl_seconds=settings.approval_response_ttl_seconds,
    )
    set_shared_router(router)
    stop_event = asyncio.Event()
    subscriber_task = asyncio.create_task(
        router.run_subscriber(stop_event=stop_event),
        name="bridge-approval-subscriber",
    )

    # Part A — CLI mirror subscriber + handler Redis wiring
    cli_subscriber = CliMirrorSubscriber(
        bot=bot,
        redis=redis_client,
        coalesce_ms=settings.cli_mirror_coalesce_ms,
    )
    set_shared_subscriber(cli_subscriber)
    set_follow_redis(redis_client)
    cli_mirror_task = asyncio.create_task(
        cli_subscriber.run(stop_event=stop_event),
        name="bridge-cli-mirror-subscriber",
    )

    register_all(dp)

    await audit(settings.db_path, chat_id=None, event="bot_started")
    log.info(
        "bot starting (username=%s, daemon_url=%s, redis_url=%s)",
        settings.bot_username, settings.daemon_url, settings.redis_url,
    )
    try:
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        stop_event.set()
        for task in (subscriber_task, cli_mirror_task):
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
        try:
            await redis_client.aclose()
        except Exception:  # pragma: no cover
            pass


if __name__ == "__main__":
    asyncio.run(main())
