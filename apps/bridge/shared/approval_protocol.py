"""Wire-format schemas for the daemon ↔ bot approval round-trip (Phase 8).

The daemon's ``can_use_tool`` callback publishes an ``ApprovalRequest`` to a
Redis channel, the bot renders an inline keyboard, the CEO taps a button, and
the bot LPUSHes an ``ApprovalResponse`` to a per-request list key the daemon is
BLPOP'ing on. See ``docs/superpowers/specs/2026-05-11-telegram-bridge-proper-approval-design.md``
for the full diagram.

These models live in ``shared/`` so the daemon and bot can both import them and
serialise/parse the same on-wire JSON without drift.

Channel/key conventions:
  * ``bridge:approval-request``                  — pub/sub (daemon → bot)
  * ``bridge:approval-response:<request_id>``    — list (bot LPUSH, daemon BLPOP)
  * ``bridge:yolo-banner``                       — pub/sub (daemon → bot,
                                                   one-shot Redis-down warning)
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Redis channel/key naming — centralised so renames don't drift across modules.
# ---------------------------------------------------------------------------

APPROVAL_REQUEST_CHANNEL = "bridge:approval-request"
YOLO_BANNER_CHANNEL = "bridge:yolo-banner"


def approval_response_key(request_id: str) -> str:
    """Per-request list key the daemon BLPOPs on and the bot LPUSHes to."""
    return f"bridge:approval-response:{request_id}"


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


class ApprovalDecision(str, Enum):
    """All the button-decisions the bot can post back to the daemon.

    ``always_allow`` is offered ONLY for Edit/Write/MultiEdit — CEO decision
    2026-05-11: Bash never gets "always" because it's the most destructive
    surface and the friction is intentional. ``always_deny`` is symmetric.
    """

    ALLOW = "allow"
    DENY = "deny"
    ALWAYS_ALLOW = "always_allow"
    ALWAYS_DENY = "always_deny"


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ApprovalRequest(BaseModel):
    """Sent by daemon when ``can_use_tool`` fires for a non-NEVER_ASK tool.

    Field rationale:
      * ``request_id`` — ULID; sortable so we can see latest requests first
        when debugging Redis directly.
      * ``tool_input_summary`` — short, escaped, ≤ 200 chars, designed to fit
        inside a Telegram message without overflow.
      * ``tool_input_full`` — kept around so the bot CAN render a "Show
        details" expansion later if needed. Currently the inline keyboard
        message stays compact for fast taps.
      * ``deadline_unix`` — daemon writes the absolute epoch second. Bot
        renders "X seconds remaining" relative to its own clock — the two
        clocks should be ≤ a few seconds apart given NTP, and we accept a
        small drift in displayed countdown.
      * ``always_allow_offered`` — false for Bash (CEO decision 2026-05-11).
    """

    request_id: str
    session_id: str
    tg_chat_id: int

    tool_name: str
    display_name: str
    description: str = ""

    tool_input_summary: str = Field(..., max_length=400)
    tool_input_full: dict[str, Any] = Field(default_factory=dict)
    tool_use_id: str | None = None

    deadline_unix: int

    always_allow_offered: bool = True


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class ApprovalResponse(BaseModel):
    """Posted by bot once the CEO taps a button (or timeout-deny fallback).

    ``message`` surfaces back to Claude as the deny-reason when applicable —
    Claude sees this in the ``tool_result.content`` of the rejected call and
    typically reports it gracefully to the user.
    """

    request_id: str
    decision: ApprovalDecision
    actor_chat_id: int
    decided_at_unix: int
    message: str = ""


# ---------------------------------------------------------------------------
# YOLO banner (daemon → bot, fire-and-forget)
# ---------------------------------------------------------------------------


class YoloBannerEvent(BaseModel):
    """One-shot warning the daemon emits when Redis is unreachable.

    CEO decision 2026-05-11 #3: Redis-down → YOLO fallback (NOT fail-closed).
    The daemon continues to allow tools but the bot displays a large red
    banner so the user is unambiguously aware that approvals are bypassed.

    This is emitted on a SEPARATE channel because, by definition, if we're
    sending it, the primary approval channel may be unhealthy — the bot may
    be on a different code path that doesn't subscribe to the approval
    channel. We keep this simple: best-effort fire-and-forget. If Redis is
    so broken even this fails, the daemon falls back to logging only and
    the CEO will notice the missing approvals on the next destructive op.
    """

    tg_chat_id: int
    reason: str  # short human reason, e.g. "redis_connection_refused"
    emitted_at_unix: int
