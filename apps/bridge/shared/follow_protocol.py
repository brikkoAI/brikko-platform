"""Wire-format schemas for the CLI → Telegram mirroring flow (Part A).

The user types in a local ``claude`` CLI window. The daemon tails the
session jsonl, parses each new line into a ``BridgeEvent``, and publishes
a ``CliMirrorEvent`` on a Redis pub/sub channel. The bot subscriber
renders the event into Telegram messages.

A separate "control plane" channel carries on/off toggles from the bot:

  * ``bridge:follow-control``                  — pub/sub (bot → daemon)
    Carries ``FollowControl`` messages with action=register|unregister|status.

  * ``bridge:cli-mirror:<chat_id>``            — pub/sub (daemon → bot)
    Per-chat fan-out of ``CliMirrorEvent`` carrying BridgeEvent payloads
    parsed from the jsonl tailer. Per-chat channel makes the bot
    subscriber's routing trivial (one channel per paired chat).

These live in ``shared/`` so daemon and bot import the same models and
agree on the wire JSON.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Channel naming
# ---------------------------------------------------------------------------

FOLLOW_CONTROL_CHANNEL = "bridge:follow-control"


def cli_mirror_channel(chat_id: int) -> str:
    """Per-chat mirror channel — daemon publishes here, bot subscribes once.

    A single subscriber pattern (``psubscribe bridge:cli-mirror:*``) would
    also work, but per-chat lets the bot scope each Redis frame to a
    specific Telegram destination without re-parsing the payload.
    """
    return f"bridge:cli-mirror:{chat_id}"


# ---------------------------------------------------------------------------
# Control plane (bot → daemon)
# ---------------------------------------------------------------------------


class FollowAction(str, Enum):
    """All the actions the bot can request from the daemon.

    ``status`` is read-only — daemon replies (in-process) by surfacing the
    current registry to the requesting chat via a one-shot mirror event.
    """

    REGISTER = "register"
    UNREGISTER = "unregister"
    UNREGISTER_ALL = "unregister_all"


class FollowControl(BaseModel):
    """Bot publishes this when CEO taps ``/follow on``/``off``/``status``.

    ``session_id`` is required for register/unregister of a specific session.
    For ``unregister_all`` it's ignored (and may be the empty string).
    """

    action: FollowAction
    tg_chat_id: int
    session_id: str = ""


# ---------------------------------------------------------------------------
# Data plane (daemon → bot)
# ---------------------------------------------------------------------------


class CliMirrorEvent(BaseModel):
    """One parsed line from the CLI session jsonl, fanned out to a chat.

    The ``event`` payload is a serialised ``BridgeEvent`` (pydantic dict —
    we keep it untyped here because the union doesn't validate as a single
    field cleanly). The receiver re-parses by ``type`` discriminator.

    ``source`` is always ``cli`` for Part A; reserved for ``tg`` in Part B
    so the bot can dedupe its own echoed events when full bidirectional
    mirroring lands.

    ``masked`` is true when the daemon's PII filter scrubbed the payload —
    the bot may then attach a small footer like "(secrets redacted)" so
    the user notices.
    """

    chat_id: int
    session_id: str
    source: str = Field(default="cli", pattern="^(cli|tg)$")
    seq: int = 0  # monotonic per-tailer sequence, helps debug missing frames
    event: dict[str, Any]
    masked: bool = False


# ---------------------------------------------------------------------------
# Lifecycle notifications (daemon → bot, on cli_mirror_channel)
# ---------------------------------------------------------------------------


class FollowNotice(BaseModel):
    """A control reply mirrored back to the bot on the per-chat channel.

    Used for register-confirmed / unregister-confirmed / session-ended /
    error messages. Bot renders these as plain text in Telegram.
    """

    chat_id: int
    session_id: str = ""
    kind: str  # registered | unregistered | session_ended | error | status
    message: str = ""
