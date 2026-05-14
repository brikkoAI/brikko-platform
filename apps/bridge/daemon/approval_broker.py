"""Mediates ``can_use_tool`` callbacks via Redis ↔ Telegram bot.

Flow per tool call:

  1. ``can_use_tool`` callback fires in the SDK with (tool_name, tool_input, ctx)
  2. Read-only / safe tools (``NEVER_ASK``) return Allow immediately.
  3. Session-cached always-allow / always-deny short-circuits.
  4. Otherwise: publish an ``ApprovalRequest`` to Redis, BLPOP the response
     list key with ``approval_timeout_seconds`` timeout. Resolve to
     Allow/Deny based on the bot's reply.
  5. On Redis-down: see ``yolo_on_redis_down`` setting. With CEO's default
     (true) we ALLOW and emit a one-shot YoloBannerEvent to Telegram so the
     user sees the safety net dropped. With false we DENY (fail-closed).

The broker is daemon-scoped; one instance shared by all SessionRunners.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

try:
    import redis.asyncio as redis_async
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover — redis is a hard dep, import-time error
    redis_async = None  # type: ignore[assignment]
    RedisError = Exception  # type: ignore[assignment,misc]

try:
    import ulid
except ImportError:  # pragma: no cover
    ulid = None  # type: ignore[assignment]

# Lazy-imported so this module can be unit-tested without the SDK installed
# (we mock the return shapes). The runtime require is in claude_runner.py.
try:
    from claude_agent_sdk import (
        PermissionResultAllow,
        PermissionResultDeny,
    )
except ImportError:  # pragma: no cover
    # Test/lint shim when SDK isn't installed in the env. Real code only
    # uses these inside the callback, which is only reachable from
    # claude_runner.py which DOES require the SDK.
    @dataclass
    class PermissionResultAllow:  # type: ignore[no-redef]
        updated_input: dict[str, Any] | None = None

    @dataclass
    class PermissionResultDeny:  # type: ignore[no-redef]
        message: str = ""
        interrupt: bool = False


from daemon.approval_state import SessionPermissionStore
from shared.approval_protocol import (
    APPROVAL_REQUEST_CHANNEL,
    YOLO_BANNER_CHANNEL,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    YoloBannerEvent,
    approval_response_key,
)

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------


# Read-only / always-safe tools that NEVER trigger an approval prompt.
# These mirror the CLI's built-in safe classification:
#   * Read/Glob/Grep — pure reads of files / paths / contents
#   * TodoWrite — internal task-list bookkeeping, no side effects outside Claude
#
# Bash with safe subcommands (echo, pwd, ls, cat) is auto-allowed by the CLI
# itself and the callback is never invoked. We do NOT need to include those
# here — they don't reach this code.
NEVER_ASK: frozenset[str] = frozenset({"Read", "Glob", "Grep", "TodoWrite"})


# Tools where "always allow" is OFFERED to the user. CEO decision 2026-05-11
# #2: Bash NOT offered "always" — each Bash gets re-prompted. Edit / Write /
# MultiEdit can be "always allowed" for the session.
ALWAYS_ALLOW_OFFERED: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit"})


# Tools where "always deny" makes sense to offer. Same set as ALWAYS_ALLOW_OFFERED
# — Bash deny-always is rarely useful (you'd just say no to each).
ALWAYS_DENY_OFFERED: frozenset[str] = ALWAYS_ALLOW_OFFERED


# How long to keep the response key alive on Redis after we BLPOP-or-timeout.
# We never re-read the key after this returns, but a stuck-bot scenario could
# LPUSH later and leak garbage forever — TTL protects us.
_RESPONSE_KEY_TTL_S = 600


# ----------------------------------------------------------------------
# Public broker
# ----------------------------------------------------------------------


# Type alias for the chat-id resolver. The daemon knows the session→chat
# mapping via the auth_tokens module; the broker stays decoupled by taking
# a callable. ``None`` return means "this session isn't paired" and the
# broker will refuse the tool.
ChatIdResolver = Callable[[str], int | None]


@dataclass
class _ToolPermissionContextShim:
    """Minimal shape we read from the SDK's ToolPermissionContext.

    Defined here so tests can pass a plain object without needing the SDK.
    """

    display_name: str | None = None
    description: str | None = None
    tool_use_id: str | None = None


class ApprovalBroker:
    """Daemon-side coordinator for the can_use_tool flow.

    Construct once per daemon process. Inject into each ``SessionRunner``.

    The Redis client must be ``redis.asyncio.Redis``-compatible — production
    uses ``redis.asyncio.Redis.from_url(...)``, tests use ``fakeredis.aioredis``.
    """

    def __init__(
        self,
        *,
        redis: Any,  # redis.asyncio.Redis | fakeredis.aioredis.FakeRedis
        chat_id_resolver: ChatIdResolver,
        permission_store: SessionPermissionStore,
        timeout_seconds: int = 300,
        yolo_on_redis_down: bool = True,
        clock: Callable[[], float] = time.time,
        ulid_factory: Callable[[], str] | None = None,
    ) -> None:
        self._redis = redis
        self._resolve_chat_id = chat_id_resolver
        self._perms = permission_store
        self._timeout = timeout_seconds
        self._yolo_fallback = yolo_on_redis_down
        self._clock = clock
        # The default ulid_factory uses python-ulid; tests can inject a
        # deterministic counter for assertions.
        if ulid_factory is None:
            if ulid is None:
                raise RuntimeError(
                    "python-ulid is required when no ulid_factory is provided"
                )
            ulid_factory = lambda: str(ulid.ULID())  # noqa: E731
        self._new_request_id = ulid_factory

        # Used to ensure we only emit ONE yolo banner per Redis-outage event.
        # Reset to False on a successful Redis op so a recovery → re-failure
        # gets a new banner.
        self._yolo_banner_emitted = False
        self._yolo_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Hot path — called for every gated tool
    # ------------------------------------------------------------------

    async def request_approval(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        ctx: Any,  # ToolPermissionContext or our shim
    ) -> Any:  # PermissionResultAllow | PermissionResultDeny
        """The function bound to ``ClaudeAgentOptions.can_use_tool``.

        Bridge tests call this directly. Real SDK calls it via the bound
        partial in ``SessionRunner._make_callback``.
        """
        # 1. NEVER_ASK shortcut
        if tool_name in NEVER_ASK:
            return PermissionResultAllow()

        # 2. Session always-deny — never even ask the user
        if self._perms.is_always_denied(session_id, tool_name):
            return PermissionResultDeny(
                message=f"Bridge: {tool_name} is in always-deny for this session.",
            )

        # 3. Session always-allow shortcut
        if self._perms.is_always_allowed(session_id, tool_name):
            return PermissionResultAllow()

        # 4. Resolve target chat id
        chat_id = self._resolve_chat_id(session_id)
        if chat_id is None:
            # Session isn't paired with Telegram — we can't ask anyone.
            # Fail-closed in this case regardless of yolo_on_redis_down,
            # because "no one to ask" is a different bug than "Redis broken".
            log.warning(
                "approval blocked: session=%s has no paired chat_id",
                session_id,
            )
            return PermissionResultDeny(
                message=(
                    "Bridge: this Claude session is not paired with Telegram. "
                    "Pair via /start <token> first."
                ),
            )

        # 5. Build the request
        request_id = self._new_request_id()
        now = int(self._clock())
        deadline = now + self._timeout
        summary = summarize_tool_input(tool_name, tool_input)
        display_name = getattr(ctx, "display_name", None) or tool_name
        description = getattr(ctx, "description", None) or ""
        tool_use_id = getattr(ctx, "tool_use_id", None)

        req = ApprovalRequest(
            request_id=request_id,
            session_id=session_id,
            tg_chat_id=chat_id,
            tool_name=tool_name,
            display_name=display_name,
            description=description,
            tool_input_summary=summary,
            tool_input_full=tool_input,
            tool_use_id=tool_use_id,
            deadline_unix=deadline,
            always_allow_offered=tool_name in ALWAYS_ALLOW_OFFERED,
        )

        # 6. Publish + BLPOP the response. ALL Redis errors here trigger the
        # yolo-fallback / fail-closed branch.
        try:
            response = await self._publish_and_wait(req)
        except RedisError as exc:
            return await self._handle_redis_failure(
                exc=exc, chat_id=chat_id, tool_name=tool_name, request_id=request_id
            )
        except asyncio.TimeoutError:
            log.warning(
                "approval timed out request_id=%s session=%s tool=%s timeout=%ds",
                request_id,
                session_id,
                tool_name,
                self._timeout,
            )
            return PermissionResultDeny(
                message=(
                    f"Bridge: approval_timeout after {self._timeout}s — "
                    "no Telegram response."
                ),
            )

        # 7. Apply decision + side-effects
        decision = response.decision
        if decision == ApprovalDecision.ALLOW:
            return PermissionResultAllow()

        if decision == ApprovalDecision.ALWAYS_ALLOW:
            if tool_name in ALWAYS_ALLOW_OFFERED:
                self._perms.add_always_allow(session_id, tool_name)
            else:
                # Bot sent always_allow for Bash (shouldn't happen with our UI,
                # but be defensive). Treat as one-shot allow.
                log.warning(
                    "always_allow received for non-eligible tool=%s; treating as one-shot",
                    tool_name,
                )
            return PermissionResultAllow()

        if decision == ApprovalDecision.ALWAYS_DENY:
            if tool_name in ALWAYS_DENY_OFFERED:
                self._perms.add_always_deny(session_id, tool_name)
            return PermissionResultDeny(
                message=response.message
                or f"Bridge: rejected (always-deny {tool_name}).",
            )

        if decision == ApprovalDecision.DENY:
            return PermissionResultDeny(
                message=response.message or "Bridge: rejected by user.",
            )

        # Defensive: unknown decision string from the wire
        return PermissionResultDeny(
            message=f"Bridge: unknown decision {decision!r}",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _publish_and_wait(self, req: ApprovalRequest) -> ApprovalResponse:
        """Publish on the request channel, then BLPOP the response list key.

        Any Redis errors bubble. The caller maps them to fail-closed / yolo.
        """
        resp_key = approval_response_key(req.request_id)
        payload = req.model_dump_json()

        # Publish first — fan-out wakes up the subscriber. If it lost the
        # subscription somehow, it can also discover the request by
        # observing the list key TTL on a separate poll — but we don't
        # implement that fallback (single bot, simple flow).
        await self._redis.publish(APPROVAL_REQUEST_CHANNEL, payload)

        # The response key has a TTL after we walk away; here we just wait
        # for the bot to LPUSH.
        timeout = max(1, req.deadline_unix - int(self._clock()))
        result = await self._redis.blpop(resp_key, timeout=timeout)
        if result is None:
            raise asyncio.TimeoutError()

        # redis-py returns (key, value). With decode_responses=False (the
        # default) both are bytes; with True both are str. Handle both.
        _, raw = result
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        try:
            response = ApprovalResponse.model_validate_json(raw)
        except Exception as exc:
            log.warning(
                "malformed approval response for request_id=%s: %s — raw=%r",
                req.request_id,
                exc,
                raw[:200],
            )
            return ApprovalResponse(
                request_id=req.request_id,
                decision=ApprovalDecision.DENY,
                actor_chat_id=0,
                decided_at_unix=int(self._clock()),
                message=f"Bridge: malformed bot response ({exc})",
            )

        # Mark Redis healthy → reset the yolo-banner one-shot latch so a
        # later failure gets a fresh banner.
        if self._yolo_banner_emitted:
            self._yolo_banner_emitted = False

        return response

    async def _handle_redis_failure(
        self,
        *,
        exc: BaseException,
        chat_id: int,
        tool_name: str,
        request_id: str,
    ) -> Any:
        """Either fail-closed or yolo-allow, depending on CEO setting."""
        log.error(
            "redis error during approval request_id=%s tool=%s: %s",
            request_id,
            tool_name,
            exc,
        )

        if not self._yolo_fallback:
            return PermissionResultDeny(
                message=(
                    f"Bridge: Redis is unreachable and yolo_on_redis_down=false. "
                    f"Refusing {tool_name}. ({exc})"
                ),
            )

        # YOLO: emit a one-shot banner so the user sees the dropped safety net.
        # Best-effort — if even the banner publish fails, we just log.
        await self._emit_yolo_banner_once(chat_id, reason=str(exc))
        return PermissionResultAllow()

    async def _emit_yolo_banner_once(self, chat_id: int, reason: str) -> None:
        """Publish the YOLO banner exactly once per Redis-outage event."""
        async with self._yolo_lock:
            if self._yolo_banner_emitted:
                return
            self._yolo_banner_emitted = True

        evt = YoloBannerEvent(
            tg_chat_id=chat_id,
            reason=reason[:200],
            emitted_at_unix=int(self._clock()),
        )
        try:
            await self._redis.publish(YOLO_BANNER_CHANNEL, evt.model_dump_json())
        except Exception as inner:  # pragma: no cover — best-effort
            log.error("yolo-banner publish also failed: %s", inner)

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def clear_session(self, session_id: str) -> None:
        """Drop session's always-allow / always-deny on session close."""
        self._perms.clear_session(session_id)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


_SUMMARY_MAX = 200


def summarize_tool_input(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Produce a short, human-friendly summary for the Telegram message.

    Capped at ``_SUMMARY_MAX`` chars to fit comfortably inside a Telegram
    message body. Sensitive payloads (full file diffs, large prompts) are
    truncated — the bot can offer a "show full" button later via the
    ``tool_input_full`` field on the request.
    """
    if not isinstance(tool_input, dict):
        return _truncate(str(tool_input))

    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        return _truncate(cmd)

    if tool_name in ("Edit", "Write", "MultiEdit"):
        path = str(tool_input.get("file_path", "?"))
        return _truncate(f"{path} ({tool_name})")

    if tool_name == "WebFetch":
        url = str(tool_input.get("url", "?"))
        return _truncate(f"GET {url}")

    if tool_name == "WebSearch":
        query = str(tool_input.get("query", "?"))
        return _truncate(f"search: {query}")

    # Fallback: stringified JSON
    try:
        return _truncate(json.dumps(tool_input, ensure_ascii=False, default=str))
    except Exception:
        return _truncate(repr(tool_input))


def _truncate(s: str, limit: int = _SUMMARY_MAX) -> str:
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"
