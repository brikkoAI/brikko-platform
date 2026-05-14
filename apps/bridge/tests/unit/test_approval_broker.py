"""Tests for daemon.approval_broker — the can_use_tool Redis bridge.

All tests use fakeredis.aioredis instead of a real Redis. We exercise:

  * NEVER_ASK short-circuits (Read/Glob/Grep/TodoWrite) → Allow without Redis
  * Session always-allow / always-deny short-circuits
  * Happy path: publish → bot LPUSH → daemon resolves to the right decision
  * Timeout when no LPUSH arrives → fail-closed Deny w/ approval_timeout message
  * Redis failure with yolo_on_redis_down=True → Allow + one YOLO banner
  * Redis failure with yolo_on_redis_down=False → Deny
  * Always-allow decision adds to per-session cache; subsequent calls skip Redis
  * Always-deny adds to cache too
  * Bash always_allow attempt is downgraded to one-shot allow (defensive)
  * Unpaired session (no chat_id) → Deny without touching Redis
  * Malformed bot response → Deny with surfaced parse error
  * summarize_tool_input() truncation + per-tool formatting
"""

from __future__ import annotations

import asyncio

import pytest
from fakeredis.aioredis import FakeRedis

from daemon.approval_broker import (
    ALWAYS_ALLOW_OFFERED,
    NEVER_ASK,
    ApprovalBroker,
    _ToolPermissionContextShim,
    summarize_tool_input,
)
from daemon.approval_state import SessionPermissionStore
from shared.approval_protocol import (
    APPROVAL_REQUEST_CHANNEL,
    YOLO_BANNER_CHANNEL,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    approval_response_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SESSION = "session-abc-123"
_CHAT_ID = 5550001


def _make_broker(
    *,
    redis,
    chat_id: int | None = _CHAT_ID,
    yolo_on_redis_down: bool = True,
    timeout_seconds: int = 5,
    permission_store: SessionPermissionStore | None = None,
    request_id: str = "01XX0000000000000000000000",
) -> ApprovalBroker:
    return ApprovalBroker(
        redis=redis,
        chat_id_resolver=lambda sid: chat_id,
        permission_store=permission_store or SessionPermissionStore(),
        timeout_seconds=timeout_seconds,
        yolo_on_redis_down=yolo_on_redis_down,
        clock=lambda: 1_700_000_000.0,
        ulid_factory=lambda: request_id,
    )


def _ctx(
    *,
    display_name: str | None = None,
    description: str | None = None,
    tool_use_id: str | None = None,
) -> _ToolPermissionContextShim:
    return _ToolPermissionContextShim(
        display_name=display_name,
        description=description,
        tool_use_id=tool_use_id,
    )


async def _respond_after_request(
    redis,
    expected_request_id: str,
    decision: ApprovalDecision,
    *,
    delay: float = 0.05,
    message: str = "",
) -> ApprovalRequest:
    """Subscribe to the request channel, capture the request, push response.

    Returns the parsed request so the test can assert on its fields.
    Mimics what the bot does in production.
    """
    pubsub = redis.pubsub()
    await pubsub.subscribe(APPROVAL_REQUEST_CHANNEL)

    # Drain the subscribe-confirmation message
    captured: ApprovalRequest | None = None
    try:
        # listen() blocks; use get_message with a small loop
        deadline = asyncio.get_event_loop().time() + 5
        while asyncio.get_event_loop().time() < deadline:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if msg is None:
                continue
            if msg.get("type") != "message":
                continue
            data = msg["data"]
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            captured = ApprovalRequest.model_validate_json(data)
            break
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()

    assert captured is not None, "did not receive approval request from broker"
    assert captured.request_id == expected_request_id

    if delay:
        await asyncio.sleep(delay)

    resp = ApprovalResponse(
        request_id=captured.request_id,
        decision=decision,
        actor_chat_id=_CHAT_ID,
        decided_at_unix=1_700_000_001,
        message=message,
    )
    await redis.lpush(
        approval_response_key(captured.request_id), resp.model_dump_json()
    )
    return captured


# ---------------------------------------------------------------------------
# NEVER_ASK shortcuts (no Redis touched)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", sorted(NEVER_ASK))
@pytest.mark.asyncio
async def test_never_ask_tools_allow_without_redis(tool_name):
    redis = FakeRedis()
    broker = _make_broker(redis=redis)

    result = await broker.request_approval(
        session_id=_SESSION,
        tool_name=tool_name,
        tool_input={"any": "thing"},
        ctx=_ctx(display_name=tool_name),
    )
    # Allow
    assert result.__class__.__name__ == "PermissionResultAllow"


# ---------------------------------------------------------------------------
# Always-allow / always-deny session cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_always_allowed_short_circuits_redis():
    redis = FakeRedis()
    perms = SessionPermissionStore()
    perms.add_always_allow(_SESSION, "Edit")
    broker = _make_broker(redis=redis, permission_store=perms)

    result = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Edit",
        tool_input={"file_path": "/tmp/x"},
        ctx=_ctx(),
    )
    assert result.__class__.__name__ == "PermissionResultAllow"


@pytest.mark.asyncio
async def test_session_always_denied_short_circuits_redis():
    redis = FakeRedis()
    perms = SessionPermissionStore()
    perms.add_always_deny(_SESSION, "Edit")
    broker = _make_broker(redis=redis, permission_store=perms)

    result = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Edit",
        tool_input={"file_path": "/tmp/x"},
        ctx=_ctx(),
    )
    assert result.__class__.__name__ == "PermissionResultDeny"
    assert "always-deny" in result.message


# ---------------------------------------------------------------------------
# Happy path — allow / deny / always_allow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_allow():
    redis = FakeRedis()
    broker = _make_broker(redis=redis, request_id="01ALW00000000000000000000")

    # Pre-subscribe THEN call broker — guaranteed delivery (FakeRedis pubsub
    # is synchronous in-memory but we still need the subscription first).
    async def bot_side():
        # Give broker a tick to publish; we already started in parallel.
        return await _respond_after_request(
            redis, "01ALW00000000000000000000", ApprovalDecision.ALLOW
        )

    async def daemon_side():
        return await broker.request_approval(
            session_id=_SESSION,
            tool_name="Bash",
            tool_input={"command": "rm temp.txt", "description": "delete temp"},
            ctx=_ctx(description="delete temp"),
        )

    # Run them concurrently; bot listens then responds.
    bot_task = asyncio.create_task(bot_side())
    # Small delay so the bot has subscribed before broker publishes
    await asyncio.sleep(0.05)
    result = await daemon_side()
    captured = await bot_task

    assert result.__class__.__name__ == "PermissionResultAllow"
    assert captured.tool_name == "Bash"
    assert captured.tg_chat_id == _CHAT_ID
    assert captured.session_id == _SESSION
    assert captured.tool_input_summary == "rm temp.txt"
    assert captured.always_allow_offered is False  # Bash never gets always


@pytest.mark.asyncio
async def test_happy_path_deny_with_message():
    redis = FakeRedis()
    broker = _make_broker(redis=redis, request_id="01DENY0000000000000000000")

    bot_task = asyncio.create_task(
        _respond_after_request(
            redis,
            "01DENY0000000000000000000",
            ApprovalDecision.DENY,
            message="nope",
        )
    )
    await asyncio.sleep(0.05)
    result = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Bash",
        tool_input={"command": "rm -rf /"},
        ctx=_ctx(),
    )
    await bot_task

    assert result.__class__.__name__ == "PermissionResultDeny"
    assert result.message == "nope"


@pytest.mark.asyncio
async def test_always_allow_caches_for_session():
    redis = FakeRedis()
    perms = SessionPermissionStore()
    broker = _make_broker(
        redis=redis,
        permission_store=perms,
        request_id="01ALWAYS00000000000000000",
    )

    bot_task = asyncio.create_task(
        _respond_after_request(
            redis, "01ALWAYS00000000000000000", ApprovalDecision.ALWAYS_ALLOW
        )
    )
    await asyncio.sleep(0.05)
    result = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Edit",
        tool_input={"file_path": "/tmp/a.txt"},
        ctx=_ctx(),
    )
    await bot_task

    assert result.__class__.__name__ == "PermissionResultAllow"
    assert perms.is_always_allowed(_SESSION, "Edit")

    # Second call: cache hits, no Redis interaction needed
    result2 = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Edit",
        tool_input={"file_path": "/tmp/b.txt"},
        ctx=_ctx(),
    )
    assert result2.__class__.__name__ == "PermissionResultAllow"


@pytest.mark.asyncio
async def test_always_deny_caches_for_session():
    redis = FakeRedis()
    perms = SessionPermissionStore()
    broker = _make_broker(
        redis=redis,
        permission_store=perms,
        request_id="01ALWAYSDENY0000000000000",
    )

    bot_task = asyncio.create_task(
        _respond_after_request(
            redis,
            "01ALWAYSDENY0000000000000",
            ApprovalDecision.ALWAYS_DENY,
            message="no edits ever",
        )
    )
    await asyncio.sleep(0.05)
    result = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Edit",
        tool_input={"file_path": "/tmp/a.txt"},
        ctx=_ctx(),
    )
    await bot_task

    assert result.__class__.__name__ == "PermissionResultDeny"
    assert perms.is_always_denied(_SESSION, "Edit")


@pytest.mark.asyncio
async def test_always_allow_for_bash_is_downgraded_to_one_shot():
    """Defensive: bot UI should never send always_allow for Bash, but if
    something did, we don't poison the cache."""
    redis = FakeRedis()
    perms = SessionPermissionStore()
    broker = _make_broker(
        redis=redis,
        permission_store=perms,
        request_id="01BASHALWAYS00000000000000",
    )

    bot_task = asyncio.create_task(
        _respond_after_request(
            redis, "01BASHALWAYS00000000000000", ApprovalDecision.ALWAYS_ALLOW
        )
    )
    await asyncio.sleep(0.05)
    result = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Bash",
        tool_input={"command": "mkdir tmp"},
        ctx=_ctx(),
    )
    await bot_task

    # Allowed once, but Bash NOT added to the always-allow set
    assert result.__class__.__name__ == "PermissionResultAllow"
    assert not perms.is_always_allowed(_SESSION, "Bash")


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_returns_deny_with_clear_message():
    redis = FakeRedis()
    broker = _make_broker(redis=redis, timeout_seconds=1)

    # No bot subscriber — BLPOP will time out
    result = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Bash",
        tool_input={"command": "rm foo"},
        ctx=_ctx(),
    )

    assert result.__class__.__name__ == "PermissionResultDeny"
    assert "approval_timeout" in result.message
    assert "1s" in result.message  # surface the configured timeout


# ---------------------------------------------------------------------------
# Redis failure paths
# ---------------------------------------------------------------------------


class _BrokenRedis:
    """Stub that raises RedisError on publish/blpop. Tracks side-effects."""

    def __init__(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        self._exc_cls = RedisConnectionError
        self.publish_calls: list[tuple[str, str]] = []
        self.banner_publishes: list[str] = []

    async def publish(self, channel: str, payload: str) -> int:
        self.publish_calls.append((channel, payload))
        if channel == YOLO_BANNER_CHANNEL:
            self.banner_publishes.append(payload)
            return 1
        raise self._exc_cls("simulated redis-down")

    async def blpop(self, key, timeout):  # pragma: no cover — never reached
        raise self._exc_cls("simulated redis-down")


@pytest.mark.asyncio
async def test_redis_down_yolo_mode_allows_and_emits_banner_once():
    redis = _BrokenRedis()
    broker = _make_broker(
        redis=redis,
        yolo_on_redis_down=True,
        request_id="01YOLO0000000000000000000",
    )

    # First failing call → Allow + banner
    result1 = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Bash",
        tool_input={"command": "rm foo"},
        ctx=_ctx(),
    )
    assert result1.__class__.__name__ == "PermissionResultAllow"
    assert len(redis.banner_publishes) == 1

    # Second failing call → still Allow, NO new banner (one-shot latch)
    result2 = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Bash",
        tool_input={"command": "rm bar"},
        ctx=_ctx(),
    )
    assert result2.__class__.__name__ == "PermissionResultAllow"
    assert len(redis.banner_publishes) == 1  # still one


@pytest.mark.asyncio
async def test_redis_down_strict_mode_denies():
    redis = _BrokenRedis()
    broker = _make_broker(redis=redis, yolo_on_redis_down=False)

    result = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Bash",
        tool_input={"command": "rm foo"},
        ctx=_ctx(),
    )
    assert result.__class__.__name__ == "PermissionResultDeny"
    assert "Redis is unreachable" in result.message
    assert len(redis.banner_publishes) == 0  # strict mode does NOT emit banner


# ---------------------------------------------------------------------------
# Unpaired session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unpaired_session_denies_without_touching_redis():
    """If chat_id_resolver returns None, we can't ask anyone → fail-closed."""
    redis = FakeRedis()
    broker = _make_broker(redis=redis, chat_id=None)

    result = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Bash",
        tool_input={"command": "rm foo"},
        ctx=_ctx(),
    )
    assert result.__class__.__name__ == "PermissionResultDeny"
    assert "not paired" in result.message


# ---------------------------------------------------------------------------
# Malformed bot response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_response_denies_with_parse_error():
    redis = FakeRedis()
    broker = _make_broker(redis=redis, request_id="01MALFORMED000000000000000")

    # Subscribe and push garbage instead of a valid ApprovalResponse
    async def garbage_responder():
        pubsub = redis.pubsub()
        await pubsub.subscribe(APPROVAL_REQUEST_CHANNEL)
        try:
            deadline = asyncio.get_event_loop().time() + 3
            while asyncio.get_event_loop().time() < deadline:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.5
                )
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                # Push raw garbage
                req = ApprovalRequest.model_validate_json(msg["data"])
                await redis.lpush(
                    approval_response_key(req.request_id), b"not-json-at-all"
                )
                return
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    task = asyncio.create_task(garbage_responder())
    await asyncio.sleep(0.05)
    result = await broker.request_approval(
        session_id=_SESSION,
        tool_name="Bash",
        tool_input={"command": "rm foo"},
        ctx=_ctx(),
    )
    await task

    assert result.__class__.__name__ == "PermissionResultDeny"
    assert "malformed" in result.message.lower()


# ---------------------------------------------------------------------------
# Bash never offers always-allow on the wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bash_request_has_always_allow_offered_false():
    redis = FakeRedis()
    broker = _make_broker(redis=redis, request_id="01ALWAYS01ALWAYS01ALWAYS01")

    bot_task = asyncio.create_task(
        _respond_after_request(
            redis, "01ALWAYS01ALWAYS01ALWAYS01", ApprovalDecision.ALLOW
        )
    )
    await asyncio.sleep(0.05)
    await broker.request_approval(
        session_id=_SESSION,
        tool_name="Bash",
        tool_input={"command": "ls"},
        ctx=_ctx(),
    )
    captured = await bot_task
    assert captured.always_allow_offered is False


@pytest.mark.asyncio
async def test_edit_request_has_always_allow_offered_true():
    redis = FakeRedis()
    broker = _make_broker(redis=redis, request_id="01EDITREQ00000000000000000")

    bot_task = asyncio.create_task(
        _respond_after_request(
            redis, "01EDITREQ00000000000000000", ApprovalDecision.ALLOW
        )
    )
    await asyncio.sleep(0.05)
    await broker.request_approval(
        session_id=_SESSION,
        tool_name="Edit",
        tool_input={"file_path": "/tmp/x.py"},
        ctx=_ctx(),
    )
    captured = await bot_task
    assert captured.always_allow_offered is True


# ---------------------------------------------------------------------------
# clear_session
# ---------------------------------------------------------------------------


def test_clear_session_drops_per_session_cache():
    perms = SessionPermissionStore()
    perms.add_always_allow(_SESSION, "Edit")
    perms.add_always_allow(_SESSION, "Write")

    redis = FakeRedis()
    broker = _make_broker(redis=redis, permission_store=perms)
    broker.clear_session(_SESSION)

    assert not perms.is_always_allowed(_SESSION, "Edit")
    assert not perms.is_always_allowed(_SESSION, "Write")


# ---------------------------------------------------------------------------
# summarize_tool_input
# ---------------------------------------------------------------------------


def test_summarize_bash_returns_command():
    assert summarize_tool_input("Bash", {"command": "rm -rf x"}) == "rm -rf x"


def test_summarize_bash_truncates_long_commands():
    long = "x" * 500
    out = summarize_tool_input("Bash", {"command": long})
    assert len(out) <= 200
    assert out.endswith("…")


def test_summarize_edit_shows_path_and_tag():
    assert summarize_tool_input("Edit", {"file_path": "/a/b.py"}) == "/a/b.py (Edit)"


def test_summarize_write_shows_path_and_tag():
    assert summarize_tool_input("Write", {"file_path": "/a/b.py"}) == "/a/b.py (Write)"


def test_summarize_multiedit_shows_path_and_tag():
    assert (
        summarize_tool_input("MultiEdit", {"file_path": "/a/b.py"})
        == "/a/b.py (MultiEdit)"
    )


def test_summarize_webfetch_shows_url():
    out = summarize_tool_input("WebFetch", {"url": "https://example.com"})
    assert out.startswith("GET https://example.com")


def test_summarize_websearch_shows_query():
    out = summarize_tool_input("WebSearch", {"query": "claude approval flow"})
    assert "claude approval flow" in out


def test_summarize_unknown_tool_falls_back_to_json():
    out = summarize_tool_input("Mystery", {"foo": "bar", "n": 42})
    assert "foo" in out
    assert "bar" in out


def test_summarize_handles_non_dict_input():
    out = summarize_tool_input("Bash", "not-a-dict")  # type: ignore[arg-type]
    assert "not-a-dict" in out


# ---------------------------------------------------------------------------
# ALWAYS_ALLOW_OFFERED contract
# ---------------------------------------------------------------------------


def test_bash_is_not_in_always_allow_offered():
    """CEO decision 2026-05-11 #2: Bash MUST NOT get always-allow."""
    assert "Bash" not in ALWAYS_ALLOW_OFFERED


def test_edit_write_multiedit_get_always_allow_offered():
    assert "Edit" in ALWAYS_ALLOW_OFFERED
    assert "Write" in ALWAYS_ALLOW_OFFERED
    assert "MultiEdit" in ALWAYS_ALLOW_OFFERED
