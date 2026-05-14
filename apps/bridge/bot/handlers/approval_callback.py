"""Inline-keyboard click handler for ``appr:<id>:<decision>`` callbacks.

The button is rendered by ``bot.services.approval_handler.ApprovalRouter`` and
posts its decision back to the daemon via Redis. The handler here:

  1. Parses the callback_data string
  2. Calls ``router.post_decision()`` which LPUSHes the wire response
  3. Edits the original message in place to show the decision (so the user
     sees their button was registered, even if Telegram already cleared the
     keyboard latency-wise).
"""

from __future__ import annotations

import logging

from aiogram import Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from bot.services.approval_handler import (
    APPROVAL_CB_PREFIX,
    get_shared_router,
)

log = logging.getLogger(__name__)


_DECISION_LABELS: dict[str, str] = {
    "allow": "✅ Allowed",
    "deny": "❌ Denied",
    "always_allow": "🔓 Always-allowed (this session)",
    "always_deny": "🔒 Always-denied (this session)",
}


async def on_approval_click(call: CallbackQuery) -> None:
    """Handle a button tap on an approval inline keyboard."""
    data = call.data or ""
    if not data.startswith(APPROVAL_CB_PREFIX):
        await call.answer()
        return

    # Format: ``appr:<request_id>:<decision>``. request_id is ULID (no colons),
    # so split into exactly 3 parts works reliably.
    payload = data[len(APPROVAL_CB_PREFIX) :]
    if ":" not in payload:
        await call.answer("invalid format", show_alert=False)
        return
    request_id, decision = payload.split(":", 1)

    if decision not in _DECISION_LABELS:
        await call.answer("unknown decision", show_alert=False)
        return

    router = get_shared_router()
    if router is None:
        log.error("approval click but no shared router — bot misconfigured")
        await call.answer("Bot config error", show_alert=True)
        return

    actor_chat_id = call.from_user.id if call.from_user else 0
    live = await router.post_decision(
        request_id=request_id,
        decision_str=decision,
        actor_chat_id=actor_chat_id,
    )

    # Always answer the callback (Telegram requires this to clear the spinner)
    label = _DECISION_LABELS[decision]
    await call.answer(label, show_alert=False)

    # If we still know the original message, edit it to reflect the decision.
    if live is None or call.message is None:
        return

    # Show who decided and what — clears the keyboard
    actor_name = call.from_user.full_name if call.from_user else "?"
    suffix = f"\n\n*Decision:* {label} by {actor_name}"
    original = call.message.text or ""
    new_text = original + suffix
    try:
        await call.message.edit_text(
            text=new_text,
            reply_markup=None,
            parse_mode="Markdown",
        )
    except TelegramBadRequest as exc:
        # "message not modified" / "message to edit not found" — non-fatal
        if "not modified" not in str(exc).lower():
            log.debug("approval-click edit_text failed: %s", exc)


def register_handlers(dp: Dispatcher) -> None:
    """Register on the dispatcher. Must run AFTER cancel-callback registration
    so a stray ``cancel:`` callback doesn't get routed here.

    The filter is narrow — only ``appr:`` prefix — so no conflict with the
    existing ``cancel:`` callbacks.
    """
    dp.callback_query.register(
        on_approval_click,
        F.data.startswith(APPROVAL_CB_PREFIX),
    )
