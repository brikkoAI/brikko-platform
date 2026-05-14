"""Bot-side approval subscriber + inline-keyboard renderer (Phase 8).

Subscribes to:
  * ``bridge:approval-request`` — daemon's ``can_use_tool`` requests.
  * ``bridge:yolo-banner``      — daemon's one-shot Redis-down warning.

For each request: renders an inline keyboard, sends to the paired Telegram
chat, and lets ``handlers.approval_callback`` post the user's choice back.

Live state:

  ``ApprovalRouter`` keeps a dict[request_id → state] for de-duplication and
  for re-rendering the original message on click. State expires after the
  deadline + a grace period; cleanup runs lazily.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pydantic import ValidationError

from shared.approval_protocol import (
    APPROVAL_REQUEST_CHANNEL,
    YOLO_BANNER_CHANNEL,
    ApprovalRequest,
    YoloBannerEvent,
)

log = logging.getLogger(__name__)


# Callback-data prefix for the approval buttons. Format:
#   ``appr:<request_id>:<decision>``
# We also reserve ``appr-show:<request_id>`` for an optional "show details"
# button in a future revision; current UI is compact (Allow/Deny/Always).
APPROVAL_CB_PREFIX = "appr:"

# How long after the deadline we keep state around in case the user taps a
# late button (Telegram delivers callbacks even after the inline keyboard
# has expired logically). 60s = enough for tap-then-cellular-roundtrip.
_LATE_GRACE_S = 60


@dataclass
class _LiveRequest:
    """Per-request bot state — populated on render, drained on click/expiry."""

    request_id: str
    chat_id: int
    message_id: int
    deadline_unix: int
    tool_name: str
    summary: str
    description: str


class ApprovalRouter:
    """Coordinates approval subscriber + callback handler over shared state.

    Single instance per bot process. Inject into both:
      * The subscriber task (run_subscriber)
      * The callback_query handler (handle_button_click)
    """

    def __init__(self, *, bot: Bot, redis, response_ttl_seconds: int = 600):
        self._bot = bot
        self._redis = redis
        self._ttl = response_ttl_seconds
        self._live: dict[str, _LiveRequest] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Subscriber loop (run as a background task by bot.main)
    # ------------------------------------------------------------------

    async def run_subscriber(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Subscribe and dispatch forever (or until stop_event is set)."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(APPROVAL_REQUEST_CHANNEL, YOLO_BANNER_CHANNEL)
        log.info(
            "approval subscriber started (channels=%s, %s)",
            APPROVAL_REQUEST_CHANNEL,
            YOLO_BANNER_CHANNEL,
        )
        try:
            while stop_event is None or not stop_event.is_set():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                channel = msg.get("channel")
                if isinstance(channel, bytes):
                    channel = channel.decode("utf-8", errors="replace")
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")

                if channel == APPROVAL_REQUEST_CHANNEL:
                    asyncio.create_task(self._handle_request_msg(data))
                elif channel == YOLO_BANNER_CHANNEL:
                    asyncio.create_task(self._handle_yolo_msg(data))
        finally:
            await pubsub.unsubscribe()
            try:
                await pubsub.aclose()
            except Exception:  # pragma: no cover
                pass
            log.info("approval subscriber stopped")

    # ------------------------------------------------------------------
    # Per-message handlers (used by subscriber + tests)
    # ------------------------------------------------------------------

    async def handle_request(self, req: ApprovalRequest) -> None:
        """Render an inline-keyboard message for one ApprovalRequest.

        Idempotent for duplicates of the same request_id (rare but possible
        if the daemon retries publish on its end).
        """
        async with self._lock:
            if req.request_id in self._live:
                log.debug("duplicate approval request %s — skipping", req.request_id)
                return
            self._live[req.request_id] = _LiveRequest(
                request_id=req.request_id,
                chat_id=req.tg_chat_id,
                message_id=0,  # filled in after send
                deadline_unix=req.deadline_unix,
                tool_name=req.tool_name,
                summary=req.tool_input_summary,
                description=req.description,
            )

        body = self.render_body(req)
        kb = self.render_keyboard(req)
        try:
            sent = await self._bot.send_message(
                chat_id=req.tg_chat_id,
                text=body,
                reply_markup=kb,
                parse_mode="Markdown",
            )
            async with self._lock:
                if req.request_id in self._live:
                    self._live[req.request_id].message_id = sent.message_id
        except Exception as exc:
            log.error(
                "failed to send approval prompt request_id=%s: %s",
                req.request_id,
                exc,
            )
            # Best effort: tell daemon to deny so it doesn't hang for 5 min.
            await self._post_emergency_deny(req, reason=f"render_failed: {exc}")
            async with self._lock:
                self._live.pop(req.request_id, None)

        self._cleanup_expired()

    async def handle_yolo_banner(self, evt: YoloBannerEvent) -> None:
        """Render the one-shot 'Redis is down, approvals bypassed' warning."""
        text = (
            "🚨 *ВНИМАНИЕ — защита отключена*\n\n"
            "Redis недоступен, approval flow выключен.\n"
            f"Опасные операции выполняются БЕЗ подтверждения.\n\n"
            f"Причина: `{evt.reason}`\n\n"
            "Перезапусти Redis на Aeza, потом /status."
        )
        try:
            await self._bot.send_message(
                chat_id=evt.tg_chat_id,
                text=text,
                parse_mode="Markdown",
            )
        except Exception as exc:  # pragma: no cover — best-effort
            log.error("yolo banner send failed: %s", exc)

    # ------------------------------------------------------------------
    # Callback (button click)
    # ------------------------------------------------------------------

    async def post_decision(
        self,
        *,
        request_id: str,
        decision_str: str,
        actor_chat_id: int,
    ) -> _LiveRequest | None:
        """Bot's callback_query handler calls this when a button is tapped.

        Returns the LiveRequest if found (so the caller can edit the original
        message in place), or None if the request expired / unknown.
        """
        from shared.approval_protocol import (
            ApprovalDecision,
            ApprovalResponse,
            approval_response_key,
        )

        async with self._lock:
            live = self._live.get(request_id)

        try:
            decision = ApprovalDecision(decision_str)
        except ValueError:
            log.warning("invalid decision string from callback: %s", decision_str)
            return None

        resp = ApprovalResponse(
            request_id=request_id,
            decision=decision,
            actor_chat_id=actor_chat_id,
            decided_at_unix=int(time.time()),
        )
        try:
            await self._redis.lpush(
                approval_response_key(request_id), resp.model_dump_json()
            )
            await self._redis.expire(approval_response_key(request_id), self._ttl)
        except Exception as exc:  # pragma: no cover — best-effort
            log.error("failed to LPUSH approval response: %s", exc)
            return live

        async with self._lock:
            self._live.pop(request_id, None)

        return live

    # ------------------------------------------------------------------
    # Renderers (pure — easy to test)
    # ------------------------------------------------------------------

    def render_body(self, req: ApprovalRequest) -> str:
        remaining = max(0, req.deadline_unix - int(time.time()))
        # Escape backticks in summary so it stays inside the Markdown ``` block.
        safe_summary = req.tool_input_summary.replace("```", "ʼʼʼ")
        lines = [
            f"🔒 Claude wants to use *{req.tool_name}*",
        ]
        if req.description:
            lines.append(f"_{req.description}_")
        lines.extend(
            [
                "",
                f"```\n{safe_summary}\n```",
                "",
                f"⏳ Ответь в течение {remaining}s — иначе авто-deny.",
            ]
        )
        return "\n".join(lines)

    def render_keyboard(self, req: ApprovalRequest) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton(
                    text="✅ Allow",
                    callback_data=f"{APPROVAL_CB_PREFIX}{req.request_id}:allow",
                ),
                InlineKeyboardButton(
                    text="❌ Deny",
                    callback_data=f"{APPROVAL_CB_PREFIX}{req.request_id}:deny",
                ),
            ],
        ]
        if req.always_allow_offered:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"🔓 Always allow {req.tool_name}",
                        callback_data=(
                            f"{APPROVAL_CB_PREFIX}{req.request_id}:always_allow"
                        ),
                    ),
                    InlineKeyboardButton(
                        text=f"🔒 Always deny {req.tool_name}",
                        callback_data=(
                            f"{APPROVAL_CB_PREFIX}{req.request_id}:always_deny"
                        ),
                    ),
                ]
            )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _handle_request_msg(self, raw: str) -> None:
        try:
            req = ApprovalRequest.model_validate_json(raw)
        except ValidationError as exc:
            log.warning("malformed approval request: %s", exc)
            return
        await self.handle_request(req)

    async def _handle_yolo_msg(self, raw: str) -> None:
        try:
            evt = YoloBannerEvent.model_validate_json(raw)
        except ValidationError as exc:
            log.warning("malformed yolo banner: %s", exc)
            return
        await self.handle_yolo_banner(evt)

    async def _post_emergency_deny(self, req: ApprovalRequest, *, reason: str) -> None:
        from shared.approval_protocol import (
            ApprovalDecision,
            ApprovalResponse,
            approval_response_key,
        )

        resp = ApprovalResponse(
            request_id=req.request_id,
            decision=ApprovalDecision.DENY,
            actor_chat_id=req.tg_chat_id,
            decided_at_unix=int(time.time()),
            message=f"Bridge bot: {reason}",
        )
        try:
            await self._redis.lpush(
                approval_response_key(req.request_id), resp.model_dump_json()
            )
            await self._redis.expire(approval_response_key(req.request_id), self._ttl)
        except Exception:  # pragma: no cover
            pass

    def _cleanup_expired(self) -> None:
        """Drop _live entries past deadline + grace. Called opportunistically."""
        now = int(time.time())
        cutoff = now - _LATE_GRACE_S
        # Snapshot keys to avoid mutating while iterating
        stale = [rid for rid, lr in self._live.items() if lr.deadline_unix < cutoff]
        for rid in stale:
            self._live.pop(rid, None)


# ----------------------------------------------------------------------
# Module-level singleton wiring — bot.main constructs once at startup.
# ----------------------------------------------------------------------


_SHARED_ROUTER: ApprovalRouter | None = None


def set_shared_router(router: ApprovalRouter | None) -> None:
    global _SHARED_ROUTER
    _SHARED_ROUTER = router


def get_shared_router() -> ApprovalRouter | None:
    return _SHARED_ROUTER
