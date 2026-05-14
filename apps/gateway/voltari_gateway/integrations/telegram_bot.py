"""@VoltariBot — Telegram bot integration (Sprint 4 Поток M).

Architecture: webhook-based, NOT polling. Telegram POSTs every update to
``POST /v1/telegram/webhook?secret=<TG_WEBHOOK_SECRET>``; we resolve the
chat_id → user, dispatch the command, send a reply via the Bot API.

Why webhook instead of aiogram polling:

* Solo founder doesn't need a long-running poller-task hung off
  ``main.lifespan`` — fewer moving parts, no race against shutdown,
  free horizontal-scaling (any web worker can answer).
* No extra dependency: we already have ``httpx``, that's all the Bot API
  needs (sendMessage, setWebhook, getMe).
* Webhook secret in URL is signed by us → reject forged updates without
  cryptography.

Commands implemented:

* ``/start``       — welcome + linking instructions.
* ``/link <token>``— consume a one-time-token issued by ``POST /v1/account/telegram-link``;
  store ``users.telegram_chat_id``.
* ``/balance``     — current balance for linked account.
* ``/usage``       — last 7 days spend / tokens.
* ``/keys``        — list active API keys (prefixes only).
* ``/topup <rub>`` — return a ЮKassa payment URL.

Push alerts (called by backend, not by user):

* ``send_alert(chat_id, text)`` — bare wrapper over Bot API sendMessage.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.db.models import Account, ApiKey, ApiKeyStatus, UsageEvent, User
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

# ---------- constants --------------------------------------------------------

TELEGRAM_API_BASE = "https://api.telegram.org"
LINK_TOKEN_TTL_SECONDS = 300  # 5 minutes — short window between web-issue and bot-consume
LINK_TOKEN_KEY_PREFIX = "tg:link:"


# ---------- DTOs -------------------------------------------------------------


@dataclass(frozen=True)
class LinkToken:
    """One-time token tying a logged-in user to an inbound /link command."""

    token: str
    user_id: str  # str-form UUID for Redis-friendly storage


@dataclass(frozen=True)
class TelegramReply:
    """What we instruct the webhook handler to send back."""

    text: str
    parse_mode: str = "HTML"  # default — most replies use bold/code formatting


# ---------- link-token issuance ---------------------------------------------


def _link_key(token: str) -> str:
    return f"{LINK_TOKEN_KEY_PREFIX}{token}"


async def issue_link_token(redis: Redis | None, user_id: str) -> LinkToken | None:
    """Mint a fresh one-time token. Returns None if Redis is unavailable.

    Tokens are 24 chars urlsafe-base64 (~144 bits) → collision odds zero
    in any practical horizon. Stored as ``tg:link:<token> → user_id`` with
    a 5-minute TTL; consumption deletes the row (atomic via DEL).
    """
    if redis is None:
        return None
    token = secrets.token_urlsafe(24)
    try:
        await redis.set(_link_key(token), user_id, ex=LINK_TOKEN_TTL_SECONDS)
    except Exception as exc:
        log.warning("tg_link_token_issue_failed", error=str(exc))
        return None
    return LinkToken(token=token, user_id=user_id)


async def consume_link_token(redis: Redis | None, token: str) -> str | None:
    """Atomically claim and delete a link-token. Returns the user_id or None.

    Uses GETDEL (Redis 6.2+) for the atomic-claim — if the same token were
    used twice (race), only one caller gets the user_id. Falls back to
    GET+DEL on older Redis (still safe; idempotent).
    """
    if redis is None or not token:
        return None
    try:
        # redis-py's typings on execute_command are loose (returns Any);
        # we coerce explicitly below.
        result = await redis.execute_command("GETDEL", _link_key(token))  # type: ignore[no-untyped-call]
    except Exception:
        # Older Redis without GETDEL — fall back. Tiny race window is OK
        # for a 5-min one-shot token; collision yields one valid link.
        try:
            result = await redis.get(_link_key(token))
            if result is not None:
                await redis.delete(_link_key(token))
        except Exception as exc:
            log.warning("tg_link_token_consume_failed", error=str(exc))
            return None
    if result is None:
        return None
    if isinstance(result, bytes):
        return result.decode("utf-8")
    return str(result)


# ---------- Bot API client ---------------------------------------------------


class TelegramBotClient:
    """Thin async wrapper over Telegram Bot API.

    Single httpx.AsyncClient instance, kept for the lifetime of the gateway
    (created in ``main.lifespan`` when ``TELEGRAM_BOT_TOKEN`` is set, closed
    on shutdown).
    """

    def __init__(
        self,
        bot_token: str,
        *,
        base_url: str = TELEGRAM_API_BASE,
        timeout_s: float = 10.0,
        proxy: str | None = None,
    ) -> None:
        if not bot_token:
            raise ValueError("bot_token must be a non-empty string")
        self._base = f"{base_url.rstrip('/')}/bot{bot_token}"
        # api.telegram.org is blocked from Russian IPs since 2025-07. Outgoing
        # sendMessage requests must go through the WG tunnel to Aeza-FI's
        # tinyproxy. Set TELEGRAM_OUTBOUND_PROXY=$OUTBOUND_HTTP_PROXY in .env.
        # If proxy=None, httpx still falls back to HTTPS_PROXY env var, but we
        # pass it explicitly for clarity.
        self._http = httpx.AsyncClient(timeout=timeout_s, proxy=proxy)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        disable_web_page_preview: bool = True,
    ) -> bool:
        """POST sendMessage. Returns True on 200 with ``"ok": true``.

        Quietly logs failures — push-alerts must NEVER raise into the
        caller (a TG outage shouldn't break /v1/billing/topup).
        """
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            r = await self._http.post(f"{self._base}/sendMessage", json=payload)
        except Exception as exc:
            log.warning("tg_send_message_http_failed", chat_id=chat_id, error=str(exc))
            return False
        if r.status_code != 200:
            log.warning(
                "tg_send_message_non_200",
                chat_id=chat_id,
                status=r.status_code,
                body=r.text[:200],
            )
            return False
        try:
            data = r.json()
        except ValueError:
            return False
        return bool(data.get("ok"))


# ---------- command dispatcher ----------------------------------------------


async def handle_update(
    *,
    update: dict[str, Any],
    db: AsyncSession,
    redis: Redis | None,
    bot: TelegramBotClient | None,
) -> TelegramReply | None:
    """Process one ``Update`` object from Telegram.

    Returns the text reply to send back to the chat (the caller — webhook
    HTTP handler — is responsible for actually calling ``bot.send_message``).
    Returns ``None`` for updates we don't care about (e.g. ``edited_message``).

    Format reference: https://core.telegram.org/bots/api#update
    """
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return None
    text = message.get("text")
    if not isinstance(text, str):
        return None

    text_stripped = text.strip()
    if not text_stripped.startswith("/"):
        # Free-form chatter — keep it cheap, just ack.
        return TelegramReply(text="Я понимаю только команды. Попробуй /balance или /help.")

    parts = text_stripped.split(maxsplit=1)
    cmd = parts[0].split("@", 1)[0].lower()  # strip "@VoltariBot" suffix
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/start" or cmd == "/help":
        # Telegram deep-link: ``https://t.me/<bot>?start=<payload>`` arrives
        # as ``/start <payload>``. If we got a non-empty payload on /start,
        # treat it exactly like ``/link <token>`` — one-tap pairing from the
        # dashboard's "Открыть в Telegram" button.
        if cmd == "/start" and arg.strip():
            return await _cmd_link(db=db, redis=redis, chat_id=chat_id, arg=arg)
        return _reply_start()
    if cmd == "/link":
        return await _cmd_link(db=db, redis=redis, chat_id=chat_id, arg=arg)
    if cmd == "/balance":
        return await _cmd_balance(db=db, chat_id=chat_id)
    if cmd == "/usage":
        return await _cmd_usage(db=db, chat_id=chat_id)
    if cmd == "/keys":
        return await _cmd_keys(db=db, chat_id=chat_id)
    if cmd == "/topup":
        return await _cmd_topup_hint(arg=arg)

    return TelegramReply(text="Неизвестная команда. Список: /balance /usage /keys /topup /link")


# ---------- individual commands ---------------------------------------------


def _reply_start() -> TelegramReply:
    return TelegramReply(
        text=(
            "Привет, я <b>@BrikkoAI_bot</b> — официальный бот Brikko.\n\n"
            "Привязать аккаунт в один тап:\n"
            '1. Открой <a href="https://brikko.ru/app/settings">app/settings</a>.\n'
            "2. Нажми «Привязать Telegram» → «Открыть в Telegram».\n"
            "Я сам вытащу токен из ссылки и подвяжу чат.\n\n"
            "(Если ссылка не сработала — скопируй токен и пришли как "
            "<code>/link &lt;token&gt;</code>.)\n\n"
            "Команды после привязки:\n"
            "<code>/balance</code> — баланс\n"
            "<code>/usage</code> — расход за 7 дней\n"
            "<code>/keys</code> — список API-ключей\n"
            "<code>/topup 1000</code> — пополнить через ЮKassa"
        )
    )


async def _cmd_link(
    *,
    db: AsyncSession,
    redis: Redis | None,
    chat_id: int,
    arg: str,
) -> TelegramReply:
    token = arg.strip()
    if not token:
        return TelegramReply(
            text=(
                "Использование: <code>/link &lt;token&gt;</code>\n"
                "Получи токен в кабинете: app/settings → «Привязать Telegram»."
            )
        )
    user_id = await consume_link_token(redis, token)
    if user_id is None:
        return TelegramReply(
            text=("Токен невалидный или истёк (срок жизни 5 минут). Сгенерируй новый в кабинете.")
        )
    try:
        import uuid as _uuid  # local — avoid module-top circulars

        user_uuid = _uuid.UUID(user_id)
    except (ValueError, TypeError):
        return TelegramReply(text="Внутренняя ошибка: токен повреждён.")

    user = await db.get(User, user_uuid)
    if user is None:
        return TelegramReply(text="Пользователь не найден. Зарегистрируйся на brikko.ru.")
    user.telegram_chat_id = chat_id
    db.add(user)
    await db.commit()
    return TelegramReply(
        text=f"✅ Привязал к <b>{_html_escape(user.email)}</b>. /balance чтобы проверить."
    )


async def _resolve_user(db: AsyncSession, chat_id: int) -> User | None:
    stmt = select(User).where(User.telegram_chat_id == chat_id).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _cmd_balance(*, db: AsyncSession, chat_id: int) -> TelegramReply:
    user = await _resolve_user(db, chat_id)
    if user is None:
        return _reply_not_linked()
    # Take the first owned account — same convention as the dashboard.
    stmt = select(Account).where(Account.owner_id == user.id).limit(1)
    account = (await db.execute(stmt)).scalar_one_or_none()
    if account is None:
        return TelegramReply(text="У пользователя нет активного аккаунта.")
    rub = account.balance_kopecks / 100.0
    return TelegramReply(
        text=(
            f"💰 Баланс: <b>{rub:,.2f} ₽</b>\n📦 Тариф: <code>{account.tariff.value}</code>"
        ).replace(",", " ")
    )


async def _cmd_usage(*, db: AsyncSession, chat_id: int) -> TelegramReply:
    user = await _resolve_user(db, chat_id)
    if user is None:
        return _reply_not_linked()
    stmt = select(Account).where(Account.owner_id == user.id).limit(1)
    account = (await db.execute(stmt)).scalar_one_or_none()
    if account is None:
        return TelegramReply(text="Нет активного аккаунта.")

    since = datetime.now(UTC) - timedelta(days=7)
    rows = (
        (
            await db.execute(
                select(UsageEvent).where(
                    UsageEvent.account_id == account.id,
                    UsageEvent.created_at >= since,
                )
            )
        )
        .scalars()
        .all()
    )
    total_kop = sum(r.cost_kopecks for r in rows)
    total_in = sum(r.input_tokens for r in rows)
    total_out = sum(r.output_tokens for r in rows)
    rub = total_kop / 100.0
    return TelegramReply(
        text=(
            f"📊 За 7 дней:\n"
            f"  расход: <b>{rub:,.2f} ₽</b>\n"
            f"  токены: <code>{total_in:,}</code> in / <code>{total_out:,}</code> out\n"
            f"  запросов: <b>{len(rows)}</b>"
        ).replace(",", " ")
    )


async def _cmd_keys(*, db: AsyncSession, chat_id: int) -> TelegramReply:
    user = await _resolve_user(db, chat_id)
    if user is None:
        return _reply_not_linked()
    stmt = select(Account).where(Account.owner_id == user.id).limit(1)
    account = (await db.execute(stmt)).scalar_one_or_none()
    if account is None:
        return TelegramReply(text="Нет активного аккаунта.")
    keys = (
        (
            await db.execute(
                select(ApiKey)
                .where(
                    ApiKey.account_id == account.id,
                    ApiKey.status == ApiKeyStatus.ACTIVE,
                )
                .order_by(ApiKey.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    if not keys:
        return TelegramReply(text="Активных ключей нет. Создай в кабинете → API.")
    lines = [f"  • <code>{k.key_prefix}...</code> ({_html_escape(k.name)})" for k in keys]
    return TelegramReply(text="🔑 Активные ключи:\n" + "\n".join(lines))


async def _cmd_topup_hint(*, arg: str) -> TelegramReply:
    arg = arg.strip()
    if not arg or not arg.isdigit():
        return TelegramReply(
            text=(
                "Использование: <code>/topup 1000</code>\n"
                "Создание платежа делается в кабинете brikko.ru/app/billing — "
                "TG-flow требует редиректа в браузер для подтверждения 3DS, "
                "это безопаснее делать с компьютера."
            )
        )
    return TelegramReply(
        text=(
            f"Пополнить <b>{int(arg):,} ₽</b> можно тут:\n"
            f"https://brikko.ru/app/billing?amount={arg}"
        ).replace(",", " ")
    )


def _reply_not_linked() -> TelegramReply:
    return TelegramReply(
        text=(
            "Аккаунт не привязан. Получи токен в кабинете "
            '<a href="https://brikko.ru/app/settings">app/settings</a> '
            "и отправь <code>/link &lt;token&gt;</code>."
        )
    )


def _html_escape(s: str) -> str:
    """Minimal HTML escape for Telegram parse_mode=HTML.

    Telegram's HTML parser breaks on naked `<`, `>`, `&`. We only escape
    those three — that's the documented surface.
    """
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------- push alerts -------------------------------------------------------


async def send_alert(
    *,
    bot: TelegramBotClient | None,
    chat_id: int | None,
    text: str,
) -> bool:
    """Best-effort push from gateway → user. False on no-op / failure.

    Used by:
      * billing low-balance warning ("у вас 10% баланса")
      * router failover notice ("OpenAI → Anthropic")
      * key created notice ("новый API-ключ создан")
    """
    if bot is None or chat_id is None:
        return False
    return await bot.send_message(chat_id, text)


__all__ = [
    "LINK_TOKEN_TTL_SECONDS",
    "LinkToken",
    "TelegramBotClient",
    "TelegramReply",
    "consume_link_token",
    "handle_update",
    "issue_link_token",
    "send_alert",
]
