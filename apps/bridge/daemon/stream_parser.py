"""Parse Claude Code's stream-json output into typed BridgeEvents.

Format reference: ``claude --print --output-format=stream-json --verbose``.
Each line of stdout is a JSON object. We've seen these shapes (Day 1):

  - {"type":"system","subtype":"init","session_id":"...","cwd":"...","tools":[...]}
  - {"type":"assistant","message":{"content":[{"type":"text","text":"..."}, ...]}}
  - {"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{...}}]}}
  - {"type":"user","message":{"content":[{"type":"tool_result","is_error":false,"content":"..."}]}}
  - {"type":"rate_limit_event", ...}
  - {"type":"result","subtype":"success","is_error":false,"duration_ms":...,
     "total_cost_usd":...,"result":"...","usage":{"input_tokens":...,"output_tokens":...}}
"""
from __future__ import annotations

import json
from typing import Iterable, Iterator

from shared.events import (
    BridgeEvent,
    ErrorEvent,
    StreamEnd,
    SystemInit,
    TextDelta,
    ToolResult,
    ToolUse,
    UserPrompt,
)


def parse_stream_line(line: str) -> BridgeEvent | None:
    """Parse a single stream-json line into a single BridgeEvent or None.

    Returns None for blank lines or events we don't surface (rate_limit_event).
    For assistant messages with multiple content blocks (text + tool_use),
    callers should use parse_stream_lines() which yields multiple events.
    """
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        return ErrorEvent(message=f"invalid stream-json: {e}", code="parse_error")

    t = obj.get("type")
    sub = obj.get("subtype")

    if t == "system" and sub == "init":
        return SystemInit(
            session_id=obj.get("session_id", ""),
            cwd=obj.get("cwd", ""),
            model=obj.get("model"),
            tools_available=obj.get("tools", []),
        )

    if t == "result":
        usage = obj.get("usage") or {}
        return StreamEnd(
            reason=sub or "stop",
            is_error=bool(obj.get("is_error", False)),
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
            cost_usd=float(obj.get("total_cost_usd", 0.0)),
            text=obj.get("result", "") or "",
        )

    if t == "rate_limit_event":
        return None  # informational, not surfaced

    if t == "assistant":
        # Has multiple content blocks — caller should use parse_stream_lines.
        # If called via single-line API, return first non-empty event.
        events = list(_explode_assistant(obj))
        return events[0] if events else None

    if t == "user":
        # Same — multiple tool_result blocks possible
        events = list(_explode_user(obj))
        return events[0] if events else None

    return ErrorEvent(message=f"unknown event type: {t!r}", code="unknown_type")


def parse_stream_lines(
    lines: Iterable[str],
    *,
    include_user_prompts: bool = False,
) -> Iterator[BridgeEvent]:
    """Yield BridgeEvents from a stream of jsonl lines.

    For multi-block events (assistant with text+tool_use, user with multiple
    tool_results), this yields each block as a separate BridgeEvent so the
    bot can render them in order.

    ``include_user_prompts`` (default ``False``) controls whether non-tool
    ``user`` messages are surfaced as :class:`UserPrompt` events. The
    daemon's prompt path leaves it ``False`` (the bot already has the
    prompt). The CLI-mirror tailer flips it to ``True`` so the CEO sees
    what he typed in CLI on his phone.
    """
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            yield ErrorEvent(message=f"invalid stream-json: {e}", code="parse_error")
            continue

        t = obj.get("type")
        sub = obj.get("subtype")

        if t == "rate_limit_event":
            continue  # silent skip

        if t == "system" and sub == "init":
            yield SystemInit(
                session_id=obj.get("session_id", ""),
                cwd=obj.get("cwd", ""),
                model=obj.get("model"),
                tools_available=obj.get("tools", []),
            )
            continue

        if t == "result":
            usage = obj.get("usage") or {}
            yield StreamEnd(
                reason=sub or "stop",
                is_error=bool(obj.get("is_error", False)),
                tokens_in=int(usage.get("input_tokens", 0)),
                tokens_out=int(usage.get("output_tokens", 0)),
                cost_usd=float(obj.get("total_cost_usd", 0.0)),
                text=obj.get("result", "") or "",
            )
            continue

        if t == "assistant":
            yield from _explode_assistant(obj)
            continue

        if t == "user":
            yield from _explode_user(obj, include_user_prompts=include_user_prompts)
            continue

        # Summary entries are written by the CLI when a session is
        # named/auto-summarised; they look like {"type":"summary", "summary":"..."}.
        # Not interesting for live mirroring.
        if t == "summary":
            continue

        # Unknown — log as error, but don't crash the stream
        yield ErrorEvent(message=f"unknown event type: {t!r}", code="unknown_type")


def _explode_assistant(obj: dict) -> Iterator[BridgeEvent]:
    """Yield events from each content block of an assistant message."""
    content = (obj.get("message") or {}).get("content") or []
    if not isinstance(content, list):
        return
    for c in content:
        if not isinstance(c, dict):
            continue
        ctype = c.get("type")
        if ctype == "text":
            text = c.get("text", "")
            if text:
                yield TextDelta(delta=text)
        elif ctype == "tool_use":
            yield ToolUse(
                tool=c.get("name", "?"),
                input=c.get("input") or {},
            )
        # other content types (thinking, image) — silently skipped


def _explode_user(
    obj: dict, *, include_user_prompts: bool = False
) -> Iterator[BridgeEvent]:
    """Yield events from a ``type=user`` jsonl row.

    Two cases:

    * ``message.content`` is a list of blocks. We may see tool_result
      blocks (always emitted as :class:`ToolResult`) and text blocks (the
      CLI writes user-typed prompts here). The text blocks become
      :class:`UserPrompt` only when ``include_user_prompts=True`` — the
      daemon's own send_prompt path leaves it False to avoid echoing
      what the bot already knows.

    * ``message.content`` is a bare string (legacy / some CLI versions).
      That's a plain user prompt — emitted as :class:`UserPrompt` only
      when ``include_user_prompts=True``.
    """
    msg = obj.get("message") or {}
    content = msg.get("content")

    if isinstance(content, str):
        if include_user_prompts:
            text = _clean_user_text(content)
            if text:
                yield UserPrompt(text=text)
        return

    if not isinstance(content, list):
        return

    for c in content:
        if not isinstance(c, dict):
            continue
        ctype = c.get("type")

        if ctype == "tool_result":
            raw = c.get("content", "")
            # content can be a string OR a list of {type:"text",text:"..."} blocks
            if isinstance(raw, list):
                parts = []
                for item in raw:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                summary = "\n".join(parts)
            else:
                summary = str(raw)
            yield ToolResult(
                tool="?",  # tool name not always echoed back here
                ok=not bool(c.get("is_error", False)),
                summary=summary[:1000],
            )
            continue

        if ctype == "text" and include_user_prompts:
            text = _clean_user_text(c.get("text", ""))
            if text:
                yield UserPrompt(text=text)
            continue


def _clean_user_text(text: str) -> str:
    """Trim and filter user-prompt strings.

    The CLI auto-injects continuation blurbs after ``/compact``; those
    should not be mirrored to Telegram because the CEO didn't type them.
    """
    if not isinstance(text, str):
        return ""
    s = text.strip()
    if not s:
        return ""
    if s.startswith("This session is being continued"):
        return ""
    return s
