"""Bot-side subscriber for the CLI-mirror Redis channel.

Subscribes to ``bridge:cli-mirror:<chat_id>`` (one channel per paired
chat — see :func:`shared.follow_protocol.cli_mirror_channel`). Each
received frame is a :class:`CliMirrorEvent` carrying:

  * A normal :class:`BridgeEvent` payload (TextDelta, ToolUse, …).
  * Or a synthetic ``follow_notice`` payload (registered/unregistered/
    session_ended/status/error) — used by :mod:`daemon.follow_service` to
    surface lifecycle messages.

We render BridgeEvents through a dedicated :class:`CliMirrorRenderer`
that batches TextDeltas with a short coalescing window so a fast-typing
Claude doesn't spam Telegram (one message per Δ ≈ 50/sec is unusable).

Throttling strategy
-------------------

* **Coalesce-window:** within ``_flush_after_ms`` of the last text-delta,
  we keep buffering. When the window expires OR a non-text event arrives
  OR ``TELEGRAM_LIMIT - SAFETY_MARGIN`` chars accumulate, we send.
* **Non-text events flush immediately.** ToolUse, ToolResult, StreamEnd
  etc. are bounded in count and want low latency.
* **Per-chat lock** prevents interleaving when two channels race (shouldn't
  happen with our channel-per-chat layout, but defensive).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from aiogram import Bot
from pydantic import ValidationError

from bot.services.stream_renderer import (
    SAFETY_MARGIN,
    TELEGRAM_LIMIT,
    _format_tool_input,
)
from shared.events import (
    BridgeEvent,
    ErrorEvent,
    StreamEnd,
    SystemInit,
    TextDelta,
    TodosUpdate,
    ToolResult,
    ToolUse,
    UserPrompt,
)
from shared.follow_protocol import (
    CliMirrorEvent,
    cli_mirror_channel,
)

log = logging.getLogger(__name__)


# Footer added once when the daemon flagged this stream as PII-redacted.
MASKED_FOOTER = "\n\n_секреты скрыты PII-фильтром_"

# Coalesce window — how long to wait for more text deltas before flushing
# the current buffer to Telegram. 700 ms strikes the balance: low enough
# that the user sees text quickly, high enough that one Claude sentence
# (often 20-40 deltas) renders as a single message.
DEFAULT_COALESCE_MS = 700


def _parse_event(payload: dict[str, Any]) -> BridgeEvent | None:
    """Reconstruct a BridgeEvent from its serialised dict, by ``type`` field."""
    t = payload.get("type")
    try:
        if t == "text":
            return TextDelta.model_validate(payload)
        if t == "tool_use":
            return ToolUse.model_validate(payload)
        if t == "tool_result":
            return ToolResult.model_validate(payload)
        if t == "todos":
            return TodosUpdate.model_validate(payload)
        if t == "end":
            return StreamEnd.model_validate(payload)
        if t == "error":
            return ErrorEvent.model_validate(payload)
        if t == "system_init":
            return SystemInit.model_validate(payload)
        if t == "user_prompt":
            return UserPrompt.model_validate(payload)
    except ValidationError as exc:
        log.warning("invalid mirror event payload type=%s: %s", t, exc)
        return None
    return None


# ---------------------------------------------------------------------------
# Renderer — single text buffer per chat, coalesce-window flushed
# ---------------------------------------------------------------------------


@dataclass
class CliMirrorRenderer:
    """Per-chat buffer for outgoing Telegram text.

    Owned by :class:`CliMirrorSubscriber` (one renderer per chat_id).
    Accumulates events and flushes via the injected ``send_text`` callable.
    """

    chat_id: int
    send_text: Any  # async (text: str, parse_mode: str | None) -> None
    coalesce_ms: int = DEFAULT_COALESCE_MS
    _buffer: list[str] = field(default_factory=list)
    _masked: bool = False
    _last_text_ts: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def feed(self, event: BridgeEvent, *, masked: bool = False) -> None:
        """Render one event, flushing the buffer if needed."""
        async with self._lock:
            if masked:
                self._masked = True

            if isinstance(event, TextDelta):
                self._buffer.append(event.delta)
                self._last_text_ts = time.time()
                if self._current_size() >= TELEGRAM_LIMIT - SAFETY_MARGIN:
                    await self._flush_locked()
                return

            if isinstance(event, UserPrompt):
                # User prompts get their own line — flush any pending
                # assistant text first so order is preserved.
                await self._flush_locked()
                await self._send_chunk(f"📱→🖥 *вы:* {event.text}")
                return

            if isinstance(event, ToolUse):
                preview = _format_tool_input(event.tool, event.input)
                await self._flush_locked()
                await self._send_chunk(f"📖 `{event.tool}` {preview}")
                return

            if isinstance(event, ToolResult):
                icon = "✓" if event.ok else "❌"
                summary = (event.summary or "").strip()
                if len(summary) > 250:
                    summary = summary[:250] + "…"
                tail = f" {summary}" if summary else ""
                await self._flush_locked()
                await self._send_chunk(f"  {icon}{tail}")
                return

            if isinstance(event, TodosUpdate):
                await self._flush_locked()
                lines = ["📋 Todos:"]
                for item in event.items:
                    emoji = {
                        "completed": "✅",
                        "in_progress": "🔧",
                        "pending": "○",
                    }.get(item.get("status", "pending"), "○")
                    content = str(item.get("content") or item.get("text") or "")
                    lines.append(f"  {emoji} {content}")
                await self._send_chunk("\n".join(lines))
                return

            if isinstance(event, StreamEnd):
                await self._flush_locked()
                cost_str = f"${event.cost_usd:.4f}" if event.cost_usd else "$0"
                ic = "❌" if event.is_error else "✓"
                await self._send_chunk(
                    f"— {ic} done · in:{event.tokens_in} out:{event.tokens_out} · {cost_str}"
                )
                return

            if isinstance(event, ErrorEvent):
                await self._flush_locked()
                await self._send_chunk(f"❌ {event.message}")
                return

            if isinstance(event, SystemInit):
                # Don't render — too verbose. Bot logs were already silent
                # on this in the prompt-path renderer for the same reason.
                return

    async def maybe_flush_idle(self) -> None:
        """Called by the subscriber's periodic timer.

        Flushes the text buffer when it's been ``coalesce_ms`` since the
        last delta — i.e. Claude is "done" with this sentence.
        """
        async with self._lock:
            if not self._buffer:
                return
            elapsed_ms = (time.time() - self._last_text_ts) * 1000
            if elapsed_ms >= self.coalesce_ms:
                await self._flush_locked()

    async def force_flush(self) -> None:
        """Drain pending text immediately. Called at shutdown / unregister."""
        async with self._lock:
            await self._flush_locked()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _current_size(self) -> int:
        return sum(len(s) for s in self._buffer)

    async def _flush_locked(self) -> None:
        if not self._buffer:
            return
        text = "🖥 " + "".join(self._buffer).rstrip()
        self._buffer = []
        # Chunk if oversized
        if len(text) > TELEGRAM_LIMIT:
            for chunk in _chunk_text(text):
                await self._send_chunk(chunk)
        else:
            await self._send_chunk(text)

    async def _send_chunk(self, body: str) -> None:
        if self._masked:
            body = body + MASKED_FOOTER
            self._masked = False
        try:
            await self.send_text(body, "Markdown")
        except Exception as exc:  # noqa: BLE001
            # Fall back to plain text if Markdown parse fails
            log.warning("cli-mirror send (md) failed chat=%s: %s", self.chat_id, exc)
            try:
                await self.send_text(body, None)
            except Exception as inner:  # noqa: BLE001  pragma: no cover
                log.error(
                    "cli-mirror send (plain) also failed chat=%s: %s",
                    self.chat_id,
                    inner,
                )


def _chunk_text(text: str) -> Iterable[str]:
    """Split a long string into Telegram-sized pieces preferring newline cuts."""
    remaining = text
    while remaining:
        if len(remaining) <= TELEGRAM_LIMIT:
            yield remaining
            return
        cut = remaining.rfind("\n", 0, TELEGRAM_LIMIT - SAFETY_MARGIN)
        if cut < 100:
            cut = TELEGRAM_LIMIT - SAFETY_MARGIN
        yield remaining[:cut].rstrip()
        remaining = remaining[cut:].lstrip()


# ---------------------------------------------------------------------------
# Subscriber
# ---------------------------------------------------------------------------


# Telegram-friendly text for follow_notice payloads.
_NOTICE_TEMPLATES: dict[str, str] = {
    "registered": "🖥→📱 *follow on* · сессия `{sid}` — будем зеркалить.",
    "unregistered": "🛑 *follow off* · сессия `{sid}`.",
    "session_ended": "🏁 CLI-сессия `{sid}` завершилась — follow снят.",
    "status": "ℹ️ {msg}",
    "error": "⚠️ {msg}",
}


def render_notice(kind: str, session_id: str, message: str) -> str:
    """Pure helper for tests: render a follow_notice payload as Telegram text."""
    sid = session_id[:8] if session_id else "?"
    tpl = _NOTICE_TEMPLATES.get(kind)
    if tpl is None:
        return f"ℹ️ {kind}: {message or session_id}"
    return tpl.format(sid=sid, msg=message or kind)


class CliMirrorSubscriber:
    """Long-lived task: psubscribes to ``bridge:cli-mirror:*``, fans events
    to per-chat renderers.

    Construct once per bot process. The subscriber holds one renderer per
    chat_id seen so far; renderers are lazily created.
    """

    def __init__(
        self,
        *,
        bot: Bot,
        redis: Any,
        coalesce_ms: int = DEFAULT_COALESCE_MS,
        idle_flush_interval_s: float = 0.3,
    ) -> None:
        self._bot = bot
        self._redis = redis
        self._coalesce_ms = coalesce_ms
        self._idle_interval = idle_flush_interval_s
        self._renderers: dict[int, CliMirrorRenderer] = {}
        self._renderer_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Subscribe and dispatch until ``stop_event`` is set."""
        pubsub = self._redis.pubsub()
        # psubscribe — one pattern covers all chat_ids without enumerating
        # them ahead of time.
        await pubsub.psubscribe(cli_mirror_channel(0).replace("0", "*"))
        log.info("cli-mirror subscriber started (pattern=bridge:cli-mirror:*)")

        idle_task = asyncio.create_task(
            self._idle_loop(stop_event), name="cli-mirror-idle"
        )

        try:
            while stop_event is None or not stop_event.is_set():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg is None:
                    continue
                mtype = msg.get("type")
                if mtype not in ("message", "pmessage"):
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                asyncio.create_task(self._handle_frame(data))
        finally:
            idle_task.cancel()
            try:
                await pubsub.punsubscribe()
                await pubsub.aclose()
            except Exception:  # pragma: no cover
                pass
            # Drain any pending text in every renderer before exit
            for renderer in list(self._renderers.values()):
                try:
                    await renderer.force_flush()
                except Exception:  # pragma: no cover
                    pass
            log.info("cli-mirror subscriber stopped")

    async def _idle_loop(self, stop_event: asyncio.Event | None) -> None:
        try:
            while stop_event is None or not stop_event.is_set():
                await asyncio.sleep(self._idle_interval)
                for renderer in list(self._renderers.values()):
                    try:
                        await renderer.maybe_flush_idle()
                    except Exception as exc:  # noqa: BLE001  pragma: no cover
                        log.debug("idle flush failed: %s", exc)
        except asyncio.CancelledError:
            return

    # ------------------------------------------------------------------
    # Per-frame handling
    # ------------------------------------------------------------------

    async def _handle_frame(self, raw: str) -> None:
        try:
            wire = CliMirrorEvent.model_validate_json(raw)
        except ValidationError as exc:
            log.warning("malformed cli mirror frame: %s", exc)
            return
        await self.dispatch(wire)

    async def dispatch(self, wire: CliMirrorEvent) -> None:
        """Public entry-point for tests; production goes via Redis pubsub."""
        payload = wire.event or {}

        if payload.get("type") == "follow_notice":
            text = render_notice(
                kind=str(payload.get("kind", "")),
                session_id=str(payload.get("session_id", "")),
                message=str(payload.get("message", "")),
            )
            await self._send(wire.chat_id, text)
            return

        event = _parse_event(payload)
        if event is None:
            return

        renderer = await self._get_renderer(wire.chat_id)
        await renderer.feed(event, masked=wire.masked)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _get_renderer(self, chat_id: int) -> CliMirrorRenderer:
        async with self._renderer_lock:
            renderer = self._renderers.get(chat_id)
            if renderer is None:
                renderer = CliMirrorRenderer(
                    chat_id=chat_id,
                    send_text=lambda body, mode, cid=chat_id: self._send(
                        cid, body, mode
                    ),
                    coalesce_ms=self._coalesce_ms,
                )
                self._renderers[chat_id] = renderer
            return renderer

    async def _send(
        self, chat_id: int, text: str, parse_mode: str | None = "Markdown"
    ) -> None:
        try:
            await self._bot.send_message(
                chat_id=chat_id, text=text, parse_mode=parse_mode
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "cli-mirror send_message failed chat=%s: %s", chat_id, exc
            )


# ---------------------------------------------------------------------------
# Shared instance wiring (mirrors approval_handler's pattern)
# ---------------------------------------------------------------------------


_SHARED_SUBSCRIBER: CliMirrorSubscriber | None = None


def set_shared_subscriber(sub: CliMirrorSubscriber | None) -> None:
    global _SHARED_SUBSCRIBER
    _SHARED_SUBSCRIBER = sub


def get_shared_subscriber() -> CliMirrorSubscriber | None:
    return _SHARED_SUBSCRIBER
