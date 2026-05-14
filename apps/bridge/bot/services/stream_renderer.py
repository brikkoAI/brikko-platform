"""Convert BridgeEvent stream → Telegram-friendly text.

Builds up a single message that the bot will edit in place as new events
arrive. When the message exceeds Telegram's 4096-char limit, the renderer
"closes" it and starts a new one.

Text formatting deliberately avoids MarkdownV2 because escape rules are
brutal — we use plain Unicode + simple emoji prefixes instead.
"""
from __future__ import annotations


from shared.events import (
    BridgeEvent,
    ErrorEvent,
    StreamEnd,
    SystemInit,
    TextDelta,
    TodosUpdate,
    ToolResult,
    ToolUse,
)


TELEGRAM_LIMIT = 4096
SAFETY_MARGIN = 100  # leave room for the "..." continuation hint


class StreamRenderer:
    """Stateful renderer. Feed events one-by-one, ask for current text."""

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._cost_seen: float = 0.0
        self._tokens_in: int = 0
        self._tokens_out: int = 0
        self._has_error: bool = False
        self._ended: bool = False

    def feed(self, event: BridgeEvent) -> None:
        if isinstance(event, SystemInit):
            # Don't render system init — too verbose for chat. Just log it.
            return

        if isinstance(event, TextDelta):
            self._buffer.append(event.delta)
            return

        if isinstance(event, ToolUse):
            preview = _format_tool_input(event.tool, event.input)
            self._buffer.append(f"\n\n📖 `{event.tool}` {preview}")
            return

        if isinstance(event, ToolResult):
            icon = "✓" if event.ok else "❌"
            summary = (event.summary or "").strip()
            if summary:
                # Truncate long tool results
                if len(summary) > 250:
                    summary = summary[:250] + "…"
                self._buffer.append(f"\n  {icon} {summary}")
            else:
                self._buffer.append(f"\n  {icon}")
            return

        if isinstance(event, TodosUpdate):
            lines = ["\n\n📋 Todos:"]
            for item in event.items:
                emoji = {
                    "completed": "✅",
                    "in_progress": "🔧",
                    "pending": "○",
                }.get(item.get("status", "pending"), "○")
                content = str(item.get("content") or item.get("text") or "")
                lines.append(f"  {emoji} {content}")
            self._buffer.append("\n".join(lines))
            return

        if isinstance(event, ErrorEvent):
            self._has_error = True
            self._buffer.append(f"\n\n❌ {event.message}")
            return

        if isinstance(event, StreamEnd):
            self._ended = True
            self._cost_seen = event.cost_usd
            self._tokens_in = event.tokens_in
            self._tokens_out = event.tokens_out
            self._has_error = self._has_error or event.is_error
            cost_str = f"${event.cost_usd:.4f}" if event.cost_usd else "$0"
            ic = "❌" if event.is_error else "✓"
            self._buffer.append(
                f"\n\n— {ic} done · in:{event.tokens_in} out:{event.tokens_out} · {cost_str}"
            )
            return

    def has_ended(self) -> bool:
        return self._ended

    def has_error(self) -> bool:
        return self._has_error

    def current_text(self) -> str:
        return "".join(self._buffer)

    def chunks_for_send(self) -> list[str]:
        """Split into Telegram-sized pieces. Splits at newlines when possible."""
        text = self.current_text()
        if not text:
            return []
        if len(text) <= TELEGRAM_LIMIT:
            return [text]
        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= TELEGRAM_LIMIT:
                chunks.append(remaining)
                break
            cut = remaining.rfind("\n", 0, TELEGRAM_LIMIT - SAFETY_MARGIN)
            if cut < 100:
                cut = TELEGRAM_LIMIT - SAFETY_MARGIN
            chunks.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
        return chunks


def _format_tool_input(tool: str, input_data: dict) -> str:
    """One-line preview of a tool call, fits in a single Telegram line."""
    if tool == "Read":
        return f"`{input_data.get('file_path', '?')}`"
    if tool == "Write":
        return f"`{input_data.get('file_path', '?')}`"
    if tool == "Edit":
        return f"`{input_data.get('file_path', '?')}`"
    if tool == "Bash":
        cmd = str(input_data.get("command", ""))
        if len(cmd) > 100:
            cmd = cmd[:100] + "…"
        return f"`{cmd}`"
    if tool in ("Glob", "Grep"):
        pat = str(input_data.get("pattern", ""))
        return f"`{pat[:80]}`"
    if tool == "WebFetch":
        return f"`{input_data.get('url', '?')[:100]}`"
    # Generic fallback
    keys = list(input_data.keys())[:3]
    return f"({', '.join(keys)})"
