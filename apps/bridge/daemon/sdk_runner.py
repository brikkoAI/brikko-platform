"""Phase 8 runner — drives prompts through ``ClaudeSDKClient`` with
``can_use_tool`` callback gating instead of ``claude --print
--dangerously-skip-permissions``.

This module coexists with the legacy ``claude_runner.run_claude`` for
backwards compatibility with Day 1-7 tests + the YOLO/legacy fallback path.
The HTTP layer (``daemon.api``) picks between them based on settings.

Design references:
  * ``docs/superpowers/specs/2026-05-11-telegram-bridge-proper-approval-design.md``
  * ``docs/superpowers/specs/DAY8_CALLBACK_PROTOTYPE_RESULTS.md``
    (two non-obvious constraints — iterator lifecycle + ``allowed_tools``
    pre-approval bypass — both avoided here by using ``ClaudeSDKClient`` and
    NOT passing ``allowed_tools``)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from daemon import process_inspector
from daemon.approval_broker import ApprovalBroker
from shared.events import (
    BridgeEvent,
    ErrorEvent,
    StreamEnd,
    SystemInit,
    TextDelta,
    ToolResult,
    ToolUse,
)

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Session-scoped runner
# ----------------------------------------------------------------------


class SessionRunner:
    """One ``ClaudeSDKClient`` per Claude session_id, kept alive between prompts.

    Use ``send(prompt)`` for each user message. The runner serialises prompts
    per session via ``_lock`` (only one in flight at a time) so two callbacks
    can't race over the same in-memory state.

    The connection is lazy — we connect on first ``send()`` and keep the
    socket alive until ``close()`` or daemon shutdown. This avoids the
    cold-start latency of spawning the CLI for every prompt.
    """

    def __init__(
        self,
        *,
        session_id: str,
        cwd: str | None,
        broker: ApprovalBroker,
        cli_path: str | None = None,
    ) -> None:
        self._session_id = session_id
        self._cwd = cwd
        self._broker = broker
        self._cli_path = cli_path
        self._client: ClaudeSDKClient | None = None
        self._lock = asyncio.Lock()
        self._connected = False

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def _ensure_connected(self) -> None:
        if self._connected and self._client is not None:
            return

        # NOTE — both constraints from DAY8_CALLBACK_PROTOTYPE_RESULTS.md:
        #   * Do NOT pass allowed_tools=[...] — it silently pre-approves
        #     EVERY listed tool at the CLI level, bypassing the callback.
        #   * Use ClaudeSDKClient (bidirectional) not query(...) — no fragile
        #     prompt-iterator lifecycle.
        options = ClaudeAgentOptions(
            cwd=self._cwd,
            resume=self._session_id,
            can_use_tool=self._make_callback(),
            permission_mode="default",
            setting_sources=["user", "project", "local"],
            cli_path=self._cli_path,
        )
        self._client = ClaudeSDKClient(options=options)
        await self._client.connect()
        self._connected = True
        log.info(
            "SessionRunner connected: session_id=%s cwd=%s",
            self._session_id,
            self._cwd,
        )

    def _make_callback(self):
        """Bind the broker.callback to this session's id.

        We construct the closure once per SessionRunner so the SDK can hold
        a stable callable reference.
        """
        broker = self._broker
        sid = self._session_id

        async def cb(tool_name: str, tool_input: dict[str, Any], ctx: Any):
            return await broker.request_approval(
                session_id=sid,
                tool_name=tool_name,
                tool_input=tool_input,
                ctx=ctx,
            )

        return cb

    # ------------------------------------------------------------------
    # Hot path
    # ------------------------------------------------------------------

    async def send(
        self, prompt: str, *, force: bool = False
    ) -> AsyncIterator[BridgeEvent]:
        """Send one prompt; yield BridgeEvents as Claude produces them.

        Args:
          prompt: User text.
          force:  Bypass the CLI-vs-bridge mutex (set by /yolo bot command).
        """
        # CLI-vs-bridge mutex check (same semantics as legacy run_claude)
        if self._cwd and not force:
            cli_procs = process_inspector.find_cli_processes_for_cwd(self._cwd)
            if cli_procs:
                pids = ", ".join(str(p.pid) for p in cli_procs[:3])
                yield ErrorEvent(
                    message=(
                        f"🔒 Сессия занята: в проекте `{self._cwd}` уже работает "
                        f"CLI-Claude (PID {pids}). Закрой её и попробуй снова."
                    ),
                    code="session_busy_cli",
                )
                return

        async with self._lock:
            try:
                await self._ensure_connected()
            except Exception as exc:
                log.exception("SessionRunner connect failed: %s", exc)
                yield ErrorEvent(
                    message=f"failed to connect to claude SDK: {exc}",
                    code="sdk_connect_failed",
                )
                return

            assert self._client is not None
            try:
                await self._client.query(prompt)
            except Exception as exc:
                log.exception("client.query failed: %s", exc)
                yield ErrorEvent(
                    message=f"claude query failed: {exc}", code="sdk_query_failed"
                )
                return

            try:
                async for msg in self._client.receive_response():
                    for event in sdk_message_to_events(
                        msg, session_id=self._session_id
                    ):
                        yield event
                    if isinstance(msg, ResultMessage):
                        # ResultMessage marks end-of-turn; receive_response()
                        # closes after this naturally, but we break to be
                        # explicit and avoid spinning on an empty queue.
                        break
            except Exception as exc:
                log.exception("client.receive_response failed: %s", exc)
                yield ErrorEvent(
                    message=f"claude stream failed: {exc}", code="sdk_stream_failed"
                )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def cancel(self) -> bool:
        """Interrupt the in-flight turn, if any. Returns True if something to interrupt."""
        if self._client is None or not self._connected:
            return False
        try:
            await self._client.interrupt()
            return True
        except Exception as exc:  # pragma: no cover — best-effort
            log.warning("client.interrupt failed: %s", exc)
            return False

    async def close(self) -> None:
        """Dispose the SDK client and drop session-permission cache."""
        if self._client is not None and self._connected:
            try:
                await self._client.disconnect()
            except Exception as exc:  # pragma: no cover
                log.warning("client.disconnect failed: %s", exc)
            self._client = None
            self._connected = False
        self._broker.clear_session(self._session_id)


# ----------------------------------------------------------------------
# Message → BridgeEvent mapper (pure function for easy testing)
# ----------------------------------------------------------------------


def sdk_message_to_events(msg: Any, *, session_id: str) -> list[BridgeEvent]:
    """Convert one SDK message into a list of BridgeEvents.

    The mapping mirrors what the legacy stream_parser.parse_stream_lines()
    produces so that ``shared.events.BridgeEvent`` consumers (bot's
    StreamRenderer, audit log) stay unchanged.

    Why return a list, not yield: SDK messages each map to 0..N events,
    and a flat list is easier to assert on in unit tests.
    """
    out: list[BridgeEvent] = []

    if isinstance(msg, SystemMessage):
        # SystemMessage carries the session-init payload. Some SDK releases
        # split this into multiple kinds; we surface only ``init``-shaped ones.
        sub = getattr(msg, "subtype", None) or getattr(msg, "type", None)
        data = getattr(msg, "data", None) or {}
        if sub == "init":
            out.append(
                SystemInit(
                    session_id=data.get("session_id") or session_id,
                    cwd=data.get("cwd", ""),
                    model=data.get("model"),
                    tools_available=data.get("tools", []),
                )
            )
        return out

    if isinstance(msg, AssistantMessage):
        for block in getattr(msg, "content", []) or []:
            if isinstance(block, TextBlock):
                text = getattr(block, "text", "") or ""
                if text:
                    out.append(TextDelta(delta=text))
            elif isinstance(block, ToolUseBlock):
                out.append(
                    ToolUse(
                        tool=getattr(block, "name", "?") or "?",
                        input=getattr(block, "input", None) or {},
                    )
                )
            # ThinkingBlock + others — silently skipped
        return out

    if isinstance(msg, UserMessage):
        # UserMessage echoes tool_result blocks back to us. Surface them so
        # the bot can render "[tool succeeded]" / "[tool failed]" lines.
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for block in content:
                if isinstance(block, ToolResultBlock):
                    raw = getattr(block, "content", "")
                    if isinstance(raw, list):
                        parts = []
                        for item in raw:
                            text = None
                            if hasattr(item, "text"):
                                text = item.text
                            elif isinstance(item, dict) and item.get("type") == "text":
                                text = item.get("text")
                            if text:
                                parts.append(str(text))
                        summary = "\n".join(parts)
                    else:
                        summary = str(raw)
                    out.append(
                        ToolResult(
                            tool="?",
                            ok=not bool(getattr(block, "is_error", False)),
                            summary=summary[:1000],
                        )
                    )
        return out

    if isinstance(msg, ResultMessage):
        usage = getattr(msg, "usage", None) or {}
        tokens_in = int(usage.get("input_tokens", 0)) if isinstance(usage, dict) else 0
        tokens_out = (
            int(usage.get("output_tokens", 0)) if isinstance(usage, dict) else 0
        )
        cost = getattr(msg, "total_cost_usd", 0.0) or 0.0
        sub = getattr(msg, "subtype", None) or "stop"
        is_error = bool(getattr(msg, "is_error", False))
        text = getattr(msg, "result", "") or ""
        out.append(
            StreamEnd(
                reason=sub,
                is_error=is_error,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=float(cost),
                text=text,
            )
        )
        return out

    # RateLimitEvent / StreamEvent / unknown — silently skipped
    return out


# ----------------------------------------------------------------------
# Singleton-per-process registry (replaces runner_registry for SDK path)
# ----------------------------------------------------------------------


class SessionRunnerRegistry:
    """Maps session_id → SessionRunner. Keeps the SDK client alive across prompts."""

    def __init__(self, broker: ApprovalBroker) -> None:
        self._broker = broker
        self._runners: dict[str, SessionRunner] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self, *, session_id: str, cwd: str | None, cli_path: str | None = None
    ) -> SessionRunner:
        async with self._lock:
            existing = self._runners.get(session_id)
            if existing is not None:
                # Keep the cwd consistent — if the caller passes a different
                # cwd than what we connected with, log it but keep the existing
                # runner (cwd changes mid-session would surprise the user).
                if cwd is not None and existing._cwd != cwd:
                    log.info(
                        "session=%s cwd ignored (existing=%s, new=%s)",
                        session_id,
                        existing._cwd,
                        cwd,
                    )
                return existing

            runner = SessionRunner(
                session_id=session_id, cwd=cwd, broker=self._broker, cli_path=cli_path
            )
            self._runners[session_id] = runner
            return runner

    def get(self, session_id: str) -> SessionRunner | None:
        return self._runners.get(session_id)

    async def cancel(self, session_id: str) -> bool:
        runner = self._runners.get(session_id)
        if runner is None:
            return False
        return await runner.cancel()

    async def close(self, session_id: str) -> bool:
        runner = self._runners.pop(session_id, None)
        if runner is None:
            return False
        await runner.close()
        return True

    async def close_all(self) -> None:
        runners = list(self._runners.values())
        self._runners.clear()
        await asyncio.gather(*(r.close() for r in runners), return_exceptions=True)

    def active_session_ids(self) -> list[str]:
        return list(self._runners.keys())


# ----------------------------------------------------------------------
# Daemon-wide singleton wiring (constructed once in main.py / api.py startup)
# ----------------------------------------------------------------------


_SHARED_REGISTRY: SessionRunnerRegistry | None = None


def set_shared_registry(reg: SessionRunnerRegistry | None) -> None:
    """Inject the daemon-wide registry; the HTTP layer reads it via get_shared_registry().

    Tests call this with a registry built around a FakeRedis-backed broker.
    """
    global _SHARED_REGISTRY
    _SHARED_REGISTRY = reg


def get_shared_registry() -> SessionRunnerRegistry | None:
    return _SHARED_REGISTRY
