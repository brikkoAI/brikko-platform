"""End-to-end approval flow smoke test.

Exercises the full daemon ↔ Redis ↔ bot path WITHOUT real Claude or Telegram:

  daemon-side                          bot-side
  -----------                          --------
  ApprovalBroker.request_approval()    ApprovalRouter.run_subscriber()
       │                                    │
       │  publish ApprovalRequest           │
       │ ─────────────────────────────────► │  handle_request() —
       │                                    │  send_message (captured by FakeBot)
       │                                    │
       │                                    │  user taps button (simulated)
       │                                    │  → router.post_decision()
       │                                    │
       │  BLPOP response                    │  LPUSH response
       │ ◄───────────────────────────────── │
       │                                    │
  resolve to Allow/Deny                     │

Verifies:
  * Bash request → CEO Allows → daemon resolves to PermissionResultAllow
  * Edit request → CEO Always-Allows → cached in session store
  * Bash request → CEO Denies → daemon resolves to PermissionResultDeny with message
  * NEVER_ASK tools skip the round trip entirely (no message to bot)
  * Timeout when bot is offline — daemon Denies with approval_timeout
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from fakeredis.aioredis import FakeRedis

from bot.services.approval_handler import (
    APPROVAL_CB_PREFIX,
    ApprovalRouter,
)
from daemon.approval_broker import ApprovalBroker
from daemon.approval_state import SessionPermissionStore
from shared.approval_protocol import APPROVAL_REQUEST_CHANNEL


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


@dataclass
class _CapturedMessage:
    chat_id: int
    text: str
    reply_markup: Any
    message_id: int = 1001


@dataclass
class _FakeBot:
    """Captures send_message + auto-replies via callback when called."""

    sent: list[_CapturedMessage] = field(default_factory=list)
    next_message_id: int = 1001

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Any = None,
        parse_mode: str | None = None,
    ) -> _CapturedMessage:
        msg = _CapturedMessage(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            message_id=self.next_message_id,
        )
        self.next_message_id += 1
        self.sent.append(msg)
        return msg


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


@dataclass
class _E2EHarness:
    redis: Any
    perms: SessionPermissionStore
    broker: ApprovalBroker
    bot: _FakeBot
    router: ApprovalRouter
    subscriber_task: asyncio.Task
    stop_event: asyncio.Event

    async def stop(self) -> None:
        self.stop_event.set()
        try:
            await asyncio.wait_for(self.subscriber_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self.subscriber_task.cancel()


@pytest.fixture
async def harness() -> _E2EHarness:
    redis = FakeRedis()
    perms = SessionPermissionStore()
    broker = ApprovalBroker(
        redis=redis,
        chat_id_resolver=lambda sid: 12345,
        permission_store=perms,
        timeout_seconds=2,  # short for tests
    )
    bot = _FakeBot()
    router = ApprovalRouter(bot=bot, redis=redis)
    stop = asyncio.Event()
    task = asyncio.create_task(router.run_subscriber(stop_event=stop))

    # Wait for the subscriber to actually be listening on the channel.
    # We poll redis.pubsub_numsub() until we see at least one subscriber.
    for _ in range(50):
        try:
            n = await redis.pubsub_numsub(APPROVAL_REQUEST_CHANNEL)
            # pubsub_numsub returns list of [channel, count] or dict
            count = 0
            if isinstance(n, list):
                for ch, c in zip(n[::2], n[1::2]):
                    if ch in (
                        APPROVAL_REQUEST_CHANNEL,
                        APPROVAL_REQUEST_CHANNEL.encode(),
                    ):
                        count = c
                        break
            elif isinstance(n, dict):
                count = n.get(APPROVAL_REQUEST_CHANNEL, 0) or n.get(
                    APPROVAL_REQUEST_CHANNEL.encode(), 0
                )
            if count >= 1:
                break
        except Exception:
            pass
        await asyncio.sleep(0.02)

    h = _E2EHarness(
        redis=redis,
        perms=perms,
        broker=broker,
        bot=bot,
        router=router,
        subscriber_task=task,
        stop_event=stop,
    )
    try:
        yield h
    finally:
        await h.stop()


async def _wait_for_message(bot: _FakeBot, *, count: int = 1, timeout: float = 5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while len(bot.sent) < count and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.02)
    assert len(bot.sent) >= count, f"expected {count} messages, got {len(bot.sent)}"


async def _simulate_button_tap(
    router: ApprovalRouter,
    request_id: str,
    decision: str,
) -> None:
    """Bot dispatcher would call this when user taps an inline button."""
    await router.post_decision(
        request_id=request_id,
        decision_str=decision,
        actor_chat_id=12345,
    )


def _extract_request_id_from_keyboard(msg: _CapturedMessage) -> str:
    """The buttons' callback_data is appr:<request_id>:<decision>; extract id."""
    kb = msg.reply_markup
    assert kb is not None
    btn = kb.inline_keyboard[0][0]
    payload = btn.callback_data[len(APPROVAL_CB_PREFIX) :]
    request_id, _ = payload.split(":", 1)
    return request_id


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_bash_allow(harness: _E2EHarness):
    """CEO sends rm-prompt → daemon callback fires → bot renders → CEO taps Allow → daemon resolves Allow."""

    async def ceo_taps_allow():
        await _wait_for_message(harness.bot)
        request_id = _extract_request_id_from_keyboard(harness.bot.sent[0])
        await _simulate_button_tap(harness.router, request_id, "allow")

    tap_task = asyncio.create_task(ceo_taps_allow())
    result = await harness.broker.request_approval(
        session_id="sid",
        tool_name="Bash",
        tool_input={"command": "rm -rf temp"},
        ctx=type(
            "Ctx", (), {"display_name": "Bash", "description": "", "tool_use_id": "t1"}
        )(),
    )
    await tap_task

    assert result.__class__.__name__ == "PermissionResultAllow"


@pytest.mark.asyncio
async def test_e2e_bash_deny(harness: _E2EHarness):
    """CEO denies → daemon resolves to Deny with the bot's message."""

    async def ceo_taps_deny():
        await _wait_for_message(harness.bot)
        request_id = _extract_request_id_from_keyboard(harness.bot.sent[0])
        await _simulate_button_tap(harness.router, request_id, "deny")

    tap_task = asyncio.create_task(ceo_taps_deny())
    result = await harness.broker.request_approval(
        session_id="sid",
        tool_name="Bash",
        tool_input={"command": "rm -rf /etc/passwd"},
        ctx=type(
            "Ctx", (), {"display_name": "Bash", "description": "", "tool_use_id": "t1"}
        )(),
    )
    await tap_task

    assert result.__class__.__name__ == "PermissionResultDeny"


@pytest.mark.asyncio
async def test_e2e_edit_always_allow_caches_for_session(harness: _E2EHarness):
    """First Edit prompts; CEO taps Always-Allow; second Edit short-circuits."""

    async def ceo_taps_always_allow():
        await _wait_for_message(harness.bot)
        request_id = _extract_request_id_from_keyboard(harness.bot.sent[0])
        await _simulate_button_tap(harness.router, request_id, "always_allow")

    tap_task = asyncio.create_task(ceo_taps_always_allow())
    result1 = await harness.broker.request_approval(
        session_id="sid",
        tool_name="Edit",
        tool_input={"file_path": "/tmp/a.py"},
        ctx=type(
            "Ctx", (), {"display_name": "Edit", "description": "", "tool_use_id": "t1"}
        )(),
    )
    await tap_task
    assert result1.__class__.__name__ == "PermissionResultAllow"
    assert harness.perms.is_always_allowed("sid", "Edit")

    # Second call — no bot prompt needed
    bot_msgs_before = len(harness.bot.sent)
    result2 = await harness.broker.request_approval(
        session_id="sid",
        tool_name="Edit",
        tool_input={"file_path": "/tmp/b.py"},
        ctx=type(
            "Ctx", (), {"display_name": "Edit", "description": "", "tool_use_id": "t2"}
        )(),
    )
    assert result2.__class__.__name__ == "PermissionResultAllow"
    assert len(harness.bot.sent) == bot_msgs_before  # NO new message


@pytest.mark.asyncio
async def test_e2e_never_ask_tool_does_not_round_trip(harness: _E2EHarness):
    """Read/Glob/Grep/TodoWrite — no message to bot, instant allow."""
    result = await harness.broker.request_approval(
        session_id="sid",
        tool_name="Read",
        tool_input={"file_path": "/tmp/x"},
        ctx=type(
            "Ctx", (), {"display_name": "Read", "description": "", "tool_use_id": "t1"}
        )(),
    )
    assert result.__class__.__name__ == "PermissionResultAllow"
    # No message went to bot
    assert harness.bot.sent == []


@pytest.mark.asyncio
async def test_e2e_timeout_when_no_response(harness: _E2EHarness):
    """Bot never replies → daemon hits 2s timeout → Deny with approval_timeout."""
    result = await harness.broker.request_approval(
        session_id="sid",
        tool_name="Bash",
        tool_input={"command": "rm something"},
        ctx=type(
            "Ctx", (), {"display_name": "Bash", "description": "", "tool_use_id": "t1"}
        )(),
    )
    assert result.__class__.__name__ == "PermissionResultDeny"
    assert "approval_timeout" in result.message
    # Bot DID render the message (we just didn't tap)
    assert len(harness.bot.sent) == 1
