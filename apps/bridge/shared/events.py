"""Bridge event models — daemon → bot SSE stream.

These are *normalized* events. The daemon parses Claude Code's raw
stream-json output and converts to these typed objects. The bot then
renders them into Telegram messages.

Decoupling here means: when Claude Code's stream-json shape changes
(it has changed twice already), only the parser needs an update —
event consumers (renderer, audit log, approval logic) keep working.
"""
from typing import Any, Literal

from pydantic import BaseModel


class TextDelta(BaseModel):
    """A chunk of assistant-generated text."""
    type: Literal["text"] = "text"
    delta: str


class UserPrompt(BaseModel):
    """A user message typed in CLI (or sent via bot).

    Only surfaced by the CLI-mirror tailer — the daemon-side prompt path
    never needs to echo the prompt back because the bot has it already.
    Auto-injected ``This session is being continued`` blurbs are filtered
    upstream in stream_parser; this carries genuine user-typed text only.
    """

    type: Literal["user_prompt"] = "user_prompt"
    text: str


class ToolUse(BaseModel):
    """Claude is invoking a tool (Read/Edit/Bash/Glob/Grep/...)."""
    type: Literal["tool_use"] = "tool_use"
    tool: str
    input: dict[str, Any]


class ToolResult(BaseModel):
    """Result of a tool call. Bot may render as collapsed block."""
    type: Literal["tool_result"] = "tool_result"
    tool: str
    ok: bool
    summary: str  # short text or first chunk of payload


class TodosUpdate(BaseModel):
    """Claude updated its task list — bot can render as a checklist."""
    type: Literal["todos"] = "todos"
    items: list[dict[str, Any]]


class StreamEnd(BaseModel):
    """Final event — Claude finished. Tokens billed, cost shown to user."""
    type: Literal["end"] = "end"
    reason: str = "stop"
    is_error: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    text: str = ""  # final result text (claude's summary)


class ErrorEvent(BaseModel):
    """Parser/runtime error event. Bot shows as ❌ message."""
    type: Literal["error"] = "error"
    message: str
    code: str | None = None


class SystemInit(BaseModel):
    """First event — Claude starts the session. Bot logs this for audit."""
    type: Literal["system_init"] = "system_init"
    session_id: str
    cwd: str
    model: str | None = None
    tools_available: list[str] = []


# Union of all bridge events the daemon can emit.
# `discriminator` not used — code uses isinstance() checks.
BridgeEvent = (
    TextDelta
    | ToolUse
    | ToolResult
    | TodosUpdate
    | StreamEnd
    | ErrorEvent
    | SystemInit
    | UserPrompt
)
