"""Light-touch secret scrubber for events streamed to Telegram.

We **don't** try to be a full DLP — the daemon trusts CEO not to type
secrets into prompts. But the jsonl on disk can accidentally capture an
API key embedded in a tool result, a stack trace, or a copy-paste fragment.

This module scans assistant text / tool input / tool result payloads for
a small set of high-signal patterns (OpenAI / Anthropic / Brikko / generic
``Bearer`` tokens) and replaces them with ``[REDACTED:<kind>]``. Anything
not matched passes through unchanged.

Design notes
------------
* **No false positives.** The patterns are deliberately conservative — we
  match on prefix + length so generic strings like "sk-something" don't
  trigger unless they're recognisably an API key.
* **Stateless.** ``scrub_event`` returns ``(new_event, masked)`` so the
  caller can flag the rendered message with a "(secrets redacted)" footer.
* **No reverse.** Once redacted, the original isn't recoverable from the
  event. The full jsonl on disk still has the secret — this is purely a
  transport-layer scrub for the bot.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from shared.events import (
    BridgeEvent,
    TextDelta,
    ToolResult,
    ToolUse,
    UserPrompt,
)


# Pattern → label. Order matters — more specific patterns run first so
# generic patterns (``sk-...``) don't catch them. Each pattern is anchored
# with a prefix + minimum length to keep false positives near zero.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Anthropic: sk-ant-api03-..., sk-ant-... — MUST come before openai
    # because the latter would otherwise gobble sk-ant-* as a generic sk-*.
    (re.compile(r"sk-ant-(?:api\d{2}-)?[A-Za-z0-9_-]{32,}"), "anthropic_key"),
    # OpenAI: sk-proj-..., sk-... (negative lookahead avoids re-matching anthropic).
    (re.compile(r"sk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{32,}"), "openai_key"),
    # Brikko user-scoped: brk_live_..., brk_test_...
    (re.compile(r"brk_(?:live|test)_[A-Za-z0-9]{16,}"), "brikko_key"),
    # Brikko MCP token (matches voltari_gateway/api/mcp_tokens.py)
    (re.compile(r"mcp-brk-[A-Za-z0-9_-]{16,}"), "brikko_mcp_token"),
    # JWT (header.payload.sig — three base64url segments)
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "jwt"),
    # Generic Authorization: Bearer ...
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9_\-.=]{20,}"), "bearer_token"),
    # AWS access key id
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws_access_key"),
    # Google API key
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "google_api_key"),
    # YooKassa secret (live_ / test_ prefix + base64url) — anchored to
    # word-start so generic "live_account_id" doesn't fire.
    (re.compile(r"(?<![A-Za-z0-9_])(?:live|test)_[A-Za-z0-9_-]{30,}"), "yookassa_secret"),
]


def scrub_text(text: str) -> tuple[str, bool]:
    """Return ``(scrubbed_text, was_modified)``."""
    if not text:
        return text, False
    modified = False
    out = text
    for pat, label in _PATTERNS:
        new = pat.sub(f"[REDACTED:{label}]", out)
        if new != out:
            modified = True
            out = new
    return out, modified


def _scrub_value(value: Any) -> tuple[Any, bool]:
    """Recursive scrubbing for nested dict/list/str payloads."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        modified = False
        out: dict[str, Any] = {}
        for k, v in value.items():
            scrubbed_v, was_mod = _scrub_value(v)
            modified = modified or was_mod
            out[k] = scrubbed_v
        return out, modified
    if isinstance(value, list):
        modified = False
        out_list: list[Any] = []
        for item in value:
            scrubbed_item, was_mod = _scrub_value(item)
            modified = modified or was_mod
            out_list.append(scrubbed_item)
        return out_list, modified
    return value, False


def scrub_event(event: BridgeEvent) -> tuple[BridgeEvent, bool]:
    """Return ``(scrubbed_event, was_masked)``.

    Currently we scrub:
      * ``TextDelta.delta``       — assistant-generated prose
      * ``UserPrompt.text``       — user typed in CLI
      * ``ToolUse.input``         — tool arguments (recursive)
      * ``ToolResult.summary``    — tool stdout / result string

    Other event types pass through unchanged (StreamEnd, SystemInit,
    ErrorEvent, TodosUpdate — none of these typically carry secrets in
    our flows; cheaper to scope narrowly).
    """
    if isinstance(event, TextDelta):
        new_text, masked = scrub_text(event.delta)
        if masked:
            return TextDelta(delta=new_text), True
        return event, False

    if isinstance(event, UserPrompt):
        new_text, masked = scrub_text(event.text)
        if masked:
            return UserPrompt(text=new_text), True
        return event, False

    if isinstance(event, ToolUse):
        new_input, masked = _scrub_value(event.input)
        if masked:
            return ToolUse(tool=event.tool, input=new_input), True
        return event, False

    if isinstance(event, ToolResult):
        new_summary, masked = scrub_text(event.summary)
        if masked:
            return (
                ToolResult(tool=event.tool, ok=event.ok, summary=new_summary),
                True,
            )
        return event, False

    return event, False


def scrub_events(events: Iterable[BridgeEvent]) -> Iterable[tuple[BridgeEvent, bool]]:
    """Convenience for bulk scrubbing — yields ``(event, was_masked)``."""
    for e in events:
        yield scrub_event(e)
