"""Telegram bot HTTP endpoints (Sprint 4 Поток M).

Two routes:

* ``POST /v1/account/telegram-link`` — session-authenticated; mints a
  one-time token (5 min TTL). Returned in two forms: as ``token`` (for
  manual ``/link <token>`` paste) and as ``deep_link`` —
  ``https://t.me/<bot>?start=<token>`` — which Telegram delivers to the
  bot as ``/start <token>``, auto-linking the chat in one tap.

* ``POST /v1/telegram/webhook`` — Telegram → us. Authenticated by a
  shared secret in the URL (``?secret=...``); never user-facing. Parses
  the update, dispatches via ``integrations.telegram_bot.handle_update``,
  echoes the reply via the bot client. Returns 200 even on internal
  errors to keep Telegram from spamming retries.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.middleware import get_redis
from voltari_gateway.auth.session_middleware import SessionPrincipal, require_session
from voltari_gateway.config import get_settings
from voltari_gateway.db.session import get_db
from voltari_gateway.integrations.telegram_bot import (
    LINK_TOKEN_TTL_SECONDS,
    TelegramBotClient,
    TelegramReply,
    handle_update,
    issue_link_token,
)
from voltari_gateway.utils.errors import GatewayError
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


# ---------- response models -------------------------------------------------


class TelegramLinkResponse(BaseModel):
    token: str
    ttl_seconds: int
    bot_username: str
    deep_link: str  # https://t.me/<bot>?start=<token>


# ---------- routers ---------------------------------------------------------

# Sits next to /v1/account routes (mounted with prefix=/v1/account).
account_link_router = APIRouter(tags=["account"])

# Webhook is unauthenticated by session — uses URL secret instead.
webhook_router = APIRouter(tags=["telegram"])


# ---------- session-authed: mint one-time link token ----------------------


@account_link_router.post(
    "/telegram-link",
    response_model=TelegramLinkResponse,
    summary="Issue a one-time TG-link token",
    description=(
        "Mints a 5-minute token. The frontend renders the returned "
        "`deep_link` as a button — Telegram delivers it as `/start <token>` "
        "and the bot pairs the chat automatically. Manual fallback: paste "
        "`/link <token>`. Tokens are single-use, Redis-backed with TTL — "
        "no DB row, no cleanup needed."
    ),
)
async def issue_telegram_link(
    principal: Annotated[SessionPrincipal, Depends(require_session)],
) -> TelegramLinkResponse:
    settings = get_settings()
    redis = get_redis()
    issued = await issue_link_token(redis, str(principal.user.id))
    if issued is None:
        raise GatewayError(
            status_code=503,
            message="Telegram linking is unavailable (Redis required).",
            type="api_error",
            code="telegram_unavailable",
        )
    bot_username = settings.telegram_bot_username or "BrikkoAI_bot"
    return TelegramLinkResponse(
        token=issued.token,
        ttl_seconds=LINK_TOKEN_TTL_SECONDS,
        bot_username=bot_username,
        deep_link=f"https://t.me/{bot_username}?start={issued.token}",
    )


# ---------- session-authed: revoke link (unpair the chat) ------------------


@account_link_router.delete(
    "/telegram-link",
    status_code=204,
    summary="Revoke the Telegram pairing for the calling user",
    description=(
        "Clears ``users.telegram_chat_id``. The bot no longer recognises the "
        "chat on its next message; the user can re-pair by issuing a new "
        "token via POST. Idempotent — returns 204 even when not currently "
        "linked."
    ),
)
async def revoke_telegram_link(
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    user = principal.user
    if user.telegram_chat_id is not None:
        log.info("telegram_unlinked", user_id=str(user.id), chat_id=user.telegram_chat_id)
        user.telegram_chat_id = None
        db.add(user)
        await db.commit()
    return Response(status_code=204)


# ---------- webhook receiver -----------------------------------------------


@webhook_router.post(
    "/v1/telegram/webhook",
    summary="Telegram Bot API webhook receiver",
    description=(
        "Telegram POSTs each `Update` here. URL-secret auth (the path "
        "includes `?secret=<TG_WEBHOOK_SECRET>` when registered via "
        "`setWebhook`). Always returns 200 to suppress Telegram retries; "
        "internal errors are logged but never propagated."
    ),
    include_in_schema=False,
)
async def telegram_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    secret: Annotated[str | None, Query(alias="secret")] = None,
) -> JSONResponse:
    settings = get_settings()
    expected = settings.telegram_webhook_secret.get_secret_value()
    if not expected:
        # Bot not configured — quietly drop. Telegram will keep retrying;
        # operator just hasn't set the env yet, no point lighting up logs.
        return JSONResponse({"ok": True}, status_code=200)
    if secret != expected:
        log.warning("tg_webhook_bad_secret")
        # 200 (not 401) to avoid handing an attacker a probe oracle.
        return JSONResponse({"ok": True}, status_code=200)

    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": True}, status_code=200)
    if not isinstance(update, dict):
        return JSONResponse({"ok": True}, status_code=200)

    bot: TelegramBotClient | None = getattr(request.app.state, "telegram_bot", None)
    redis = get_redis()

    try:
        reply: TelegramReply | None = await handle_update(
            update=update,
            db=db,
            redis=redis,
            bot=bot,
        )
    except Exception as exc:
        log.exception("tg_webhook_handler_failed", error=str(exc))
        return JSONResponse({"ok": True}, status_code=200)

    if reply is None:
        return JSONResponse({"ok": True}, status_code=200)

    # Send the reply back. We need chat_id from the update itself.
    chat_id = _extract_chat_id(update)
    if chat_id is not None and bot is not None:
        await bot.send_message(chat_id, reply.text, parse_mode=reply.parse_mode)
    return JSONResponse({"ok": True}, status_code=200)


def _extract_chat_id(update: dict[str, Any]) -> int | None:
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat")
    if not isinstance(chat, dict):
        return None
    cid = chat.get("id")
    return cid if isinstance(cid, int) else None


__all__ = ["account_link_router", "webhook_router"]
