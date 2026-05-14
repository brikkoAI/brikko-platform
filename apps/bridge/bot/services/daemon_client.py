"""HTTP client for the daemon API.

The bot lives on Aeza; the daemon lives on the CEO's PC. They talk over an
SSH reverse tunnel (Aeza:8090 → PC:9090). From the bot's perspective the
daemon is at ``http://127.0.0.1:8090`` (or whatever ``BRIDGE_BOT_DAEMON_URL``
says).

This module provides:
  * ``health()``       — synchronous GET /health, used by /status
  * ``list_sessions()`` — GET /sessions, returns list[dict]
  * ``stream_send()``  — POST /sessions/{id}/send, async-iterates BridgeEvents
                          parsed from SSE ``data:`` lines

If the daemon is offline (SSH tunnel down, PC asleep), httpx raises
``httpx.ConnectError`` / ``httpx.ReadTimeout`` etc. — callers should catch
``DaemonOffline`` which we re-raise here for clarity.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx
from pydantic import ValidationError

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

log = logging.getLogger(__name__)


# Long enough to ride out a slow Claude run, but not so long that a dead
# tunnel hangs the bot forever. Streaming responses set their own per-read
# timeout via httpx.Timeout.
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)


class DaemonOffline(Exception):
    """Raised when daemon is unreachable (tunnel down, PC asleep, …)."""


# Map BridgeEvent.type → pydantic model class. Keep in sync with shared.events.
_EVENT_TYPES: dict[str, type[BridgeEvent]] = {
    "text": TextDelta,
    "tool_use": ToolUse,
    "tool_result": ToolResult,
    "todos": TodosUpdate,
    "end": StreamEnd,
    "error": ErrorEvent,
    "system_init": SystemInit,
    "user_prompt": UserPrompt,
}


def _parse_event(payload: dict[str, Any]) -> BridgeEvent | None:
    """Convert a parsed JSON payload into the right BridgeEvent subclass."""
    type_ = payload.get("type")
    cls = _EVENT_TYPES.get(type_) if isinstance(type_, str) else None
    if cls is None:
        log.debug("unknown event type from daemon: %r", type_)
        return None
    try:
        return cls.model_validate(payload)
    except ValidationError:
        log.warning("invalid event payload: %r", payload)
        return None


async def health(daemon_url: str) -> dict[str, Any]:
    """GET /health. Raises DaemonOffline if unreachable."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.get(f"{daemon_url}/health")
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, OSError) as e:
        raise DaemonOffline(str(e)) from e


async def list_sessions(daemon_url: str) -> list[dict[str, Any]]:
    """GET /sessions. Returns raw session dicts."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            r = await cli.get(f"{daemon_url}/sessions")
        r.raise_for_status()
        return r.json().get("sessions", [])
    except (httpx.HTTPError, OSError) as e:
        raise DaemonOffline(str(e)) from e


async def cancel_session(daemon_url: str, session_id: str) -> dict[str, Any]:
    """POST /sessions/{id}/cancel. Returns the daemon's response payload."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.post(f"{daemon_url}/sessions/{session_id}/cancel")
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, OSError) as e:
        raise DaemonOffline(str(e)) from e


async def active_sessions(daemon_url: str) -> list[str]:
    """GET /sessions/active — list of session_ids with a live claude proc."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.get(f"{daemon_url}/sessions/active")
        r.raise_for_status()
        return r.json().get("active", [])
    except (httpx.HTTPError, OSError) as e:
        raise DaemonOffline(str(e)) from e


async def list_processes(daemon_url: str) -> list[dict[str, Any]]:
    """GET /processes — all claude.exe procs on the PC, daemon and CLI."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.get(f"{daemon_url}/processes")
        r.raise_for_status()
        return r.json().get("processes", [])
    except (httpx.HTTPError, OSError) as e:
        raise DaemonOffline(str(e)) from e


async def get_permissions(daemon_url: str, session_id: str) -> dict[str, Any]:
    """GET /sessions/{id}/permissions — current always-allow/deny cache.

    Returns ``{"session_id": ..., "always_allow": [...], "always_deny": [...]}``.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.get(f"{daemon_url}/sessions/{session_id}/permissions")
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, OSError) as e:
        raise DaemonOffline(str(e)) from e


async def reset_permissions(daemon_url: str, session_id: str) -> dict[str, Any]:
    """POST /sessions/{id}/permissions/reset — clear the cache."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.post(
                f"{daemon_url}/sessions/{session_id}/permissions/reset"
            )
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, OSError) as e:
        raise DaemonOffline(str(e)) from e


async def list_follows(
    daemon_url: str, chat_id: int
) -> list[dict[str, Any]]:
    """GET /follows?chat_id=N — active CLI-mirror follows for this chat.

    Returns the daemon's TailerStatus rows as dicts. Empty list if the
    chat isn't following anything (not an error).
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.get(
                f"{daemon_url}/follows", params={"chat_id": chat_id}
            )
        r.raise_for_status()
        return r.json().get("follows", [])
    except (httpx.HTTPError, OSError) as e:
        raise DaemonOffline(str(e)) from e


async def stream_send(
    daemon_url: str,
    session_id: str,
    prompt: str,
    cwd: str | None = None,
    force: bool = False,
) -> AsyncIterator[BridgeEvent]:
    """POST /sessions/{id}/send and yield BridgeEvent objects.

    The daemon returns ``text/event-stream`` with one ``data: <json>\\n\\n``
    block per event. We parse each block back into the typed event.

    Stops yielding when the stream ends or the connection drops.
    """
    url = f"{daemon_url}/sessions/{session_id}/send"
    body: dict[str, Any] = {"prompt": prompt}
    if cwd is not None:
        body["cwd"] = cwd
    if force:
        body["force"] = True

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as cli:
            async with cli.stream("POST", url, json=body) as resp:
                resp.raise_for_status()
                async for raw in resp.aiter_lines():
                    if not raw:
                        continue
                    if not raw.startswith("data:"):
                        # Comments (": keep-alive\n\n") and other SSE fields
                        continue
                    payload_str = raw[len("data:"):].lstrip()
                    try:
                        payload = json.loads(payload_str)
                    except json.JSONDecodeError:
                        log.warning("bad JSON in SSE line: %r", payload_str[:200])
                        continue
                    event = _parse_event(payload)
                    if event is not None:
                        yield event
    except (httpx.HTTPError, OSError) as e:
        raise DaemonOffline(str(e)) from e
