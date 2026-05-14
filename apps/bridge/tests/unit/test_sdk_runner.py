"""Tests for daemon.sdk_runner — ClaudeSDKClient-based runner.

We don't spawn real claude here; we monkeypatch ClaudeSDKClient with a fake
that records the wire calls (connect / query / receive_response / interrupt /
disconnect) and yields scripted messages. This lets us assert on the SDK
contract WITHOUT a CLI process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import pytest
from fakeredis.aioredis import FakeRedis

from daemon.approval_broker import ApprovalBroker
from daemon.approval_state import SessionPermissionStore
from daemon.sdk_runner import (
    SessionRunner,
    SessionRunnerRegistry,
    sdk_message_to_events,
)
from shared.events import (
    ErrorEvent,
    StreamEnd,
    SystemInit,
    TextDelta,
    ToolResult,
    ToolUse,
)


# ---------------------------------------------------------------------------
# Fake SDK message types — minimal stand-ins so isinstance checks in
# sdk_message_to_events() work. We use the REAL types where possible.
# ---------------------------------------------------------------------------


from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


# ---------------------------------------------------------------------------
# sdk_message_to_events — pure function
# ---------------------------------------------------------------------------


def test_system_init_message_maps_to_systeminit_event():
    msg = SystemMessage(
        subtype="init",
        data={
            "session_id": "sid-1",
            "cwd": "/tmp",
            "model": "claude-opus-4",
            "tools": ["Read", "Bash"],
        },
    )
    out = sdk_message_to_events(msg, session_id="sid-1")
    assert len(out) == 1
    assert isinstance(out[0], SystemInit)
    assert out[0].session_id == "sid-1"
    assert out[0].cwd == "/tmp"
    assert out[0].model == "claude-opus-4"
    assert out[0].tools_available == ["Read", "Bash"]


def test_assistant_text_block_maps_to_textdelta():
    msg = AssistantMessage(content=[TextBlock(text="hello world")], model="m")
    out = sdk_message_to_events(msg, session_id="s")
    assert len(out) == 1
    assert isinstance(out[0], TextDelta)
    assert out[0].delta == "hello world"


def test_assistant_text_block_empty_text_is_skipped():
    msg = AssistantMessage(content=[TextBlock(text="")], model="m")
    out = sdk_message_to_events(msg, session_id="s")
    assert out == []


def test_assistant_tool_use_block_maps_to_tooluse():
    msg = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Bash", input={"command": "ls"})],
        model="m",
    )
    out = sdk_message_to_events(msg, session_id="s")
    assert len(out) == 1
    assert isinstance(out[0], ToolUse)
    assert out[0].tool == "Bash"
    assert out[0].input == {"command": "ls"}


def test_assistant_message_with_text_and_tool_use_yields_both():
    msg = AssistantMessage(
        content=[
            TextBlock(text="Running command"),
            ToolUseBlock(id="t2", name="Bash", input={"command": "ls"}),
        ],
        model="m",
    )
    out = sdk_message_to_events(msg, session_id="s")
    assert len(out) == 2
    assert isinstance(out[0], TextDelta)
    assert isinstance(out[1], ToolUse)


def test_user_tool_result_block_string_content_maps_to_toolresult():
    msg = UserMessage(
        content=[
            ToolResultBlock(tool_use_id="t1", content="output text", is_error=False)
        ],
    )
    out = sdk_message_to_events(msg, session_id="s")
    assert len(out) == 1
    assert isinstance(out[0], ToolResult)
    assert out[0].ok is True
    assert out[0].summary == "output text"


def test_user_tool_result_block_with_error_flag_sets_ok_false():
    msg = UserMessage(
        content=[ToolResultBlock(tool_use_id="t1", content="boom", is_error=True)],
    )
    out = sdk_message_to_events(msg, session_id="s")
    assert len(out) == 1
    assert out[0].ok is False


def test_user_tool_result_block_with_text_list_content():
    """Some tools return content as list of {type:'text',text:...} blocks."""
    msg = UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id="t1",
                content=[
                    {"type": "text", "text": "line1"},
                    {"type": "text", "text": "line2"},
                ],
                is_error=False,
            )
        ],
    )
    out = sdk_message_to_events(msg, session_id="s")
    assert len(out) == 1
    assert "line1" in out[0].summary
    assert "line2" in out[0].summary


def test_result_message_maps_to_streamend_with_costs():
    msg = ResultMessage(
        subtype="success",
        duration_ms=1234,
        duration_api_ms=1000,
        is_error=False,
        num_turns=1,
        total_cost_usd=0.0042,
        usage={"input_tokens": 100, "output_tokens": 50},
        session_id="sid",
        result="all done",
    )
    out = sdk_message_to_events(msg, session_id="s")
    assert len(out) == 1
    assert isinstance(out[0], StreamEnd)
    assert out[0].cost_usd == pytest.approx(0.0042)
    assert out[0].tokens_in == 100
    assert out[0].tokens_out == 50
    assert out[0].text == "all done"
    assert out[0].is_error is False


def test_result_message_with_error_flag():
    msg = ResultMessage(
        subtype="error_during_execution",
        duration_ms=10,
        duration_api_ms=5,
        is_error=True,
        num_turns=1,
        total_cost_usd=0,
        usage={"input_tokens": 0, "output_tokens": 0},
        session_id="sid",
    )
    out = sdk_message_to_events(msg, session_id="s")
    assert isinstance(out[0], StreamEnd)
    assert out[0].is_error is True


def test_unknown_message_yields_no_events():
    """RateLimitEvent / StreamEvent / random objects are silently skipped."""
    out = sdk_message_to_events("not a real message", session_id="s")
    assert out == []


# ---------------------------------------------------------------------------
# SessionRunner with a fake ClaudeSDKClient
# ---------------------------------------------------------------------------


@dataclass
class _FakeClient:
    """Minimal ClaudeSDKClient stand-in.

    The runner only uses connect/disconnect/query/receive_response/interrupt.
    We record the calls and yield scripted messages.
    """

    scripted: list[Any] = field(default_factory=list)
    connected: bool = False
    disconnected: bool = False
    queries: list[str] = field(default_factory=list)
    interrupts: int = 0

    async def connect(self, prompt=None) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.queries.append(prompt)

    async def receive_response(self) -> AsyncIterator[Any]:
        for msg in self.scripted:
            yield msg

    async def interrupt(self) -> None:
        self.interrupts += 1


def _make_runner(monkeypatch, *, scripted, cwd: str | None = None):
    fake = _FakeClient(scripted=scripted)

    def fake_ctor(options=None, transport=None):
        # Stash options on the fake so tests can assert on them
        fake.options = options
        return fake

    monkeypatch.setattr("daemon.sdk_runner.ClaudeSDKClient", fake_ctor)

    # Make sure mutex check doesn't block (no CLI claudes in this cwd)
    monkeypatch.setattr(
        "daemon.process_inspector.find_cli_processes_for_cwd",
        lambda *_args, **_kw: [],
    )

    broker = ApprovalBroker(
        redis=FakeRedis(),
        chat_id_resolver=lambda sid: 12345,
        permission_store=SessionPermissionStore(),
        timeout_seconds=5,
    )
    runner = SessionRunner(session_id="sid-test", cwd=cwd, broker=broker)
    return runner, fake


@pytest.mark.asyncio
async def test_session_runner_connects_lazily(monkeypatch):
    """First send() triggers connect; subsequent ones reuse."""
    runner, fake = _make_runner(
        monkeypatch,
        scripted=[
            ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                total_cost_usd=0,
                usage={"input_tokens": 0, "output_tokens": 0},
                session_id="sid",
            ),
        ],
    )

    events = [ev async for ev in runner.send("hi")]
    assert fake.connected is True
    assert fake.queries == ["hi"]
    assert isinstance(events[-1], StreamEnd)


@pytest.mark.asyncio
async def test_session_runner_does_not_pass_allowed_tools(monkeypatch):
    """CRITICAL: allowed_tools=[...] silently pre-approves and bypasses
    the callback. We must NOT pass it."""
    runner, fake = _make_runner(
        monkeypatch,
        scripted=[
            ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                total_cost_usd=0,
                usage={"input_tokens": 0, "output_tokens": 0},
                session_id="sid",
            ),
        ],
    )

    _ = [ev async for ev in runner.send("hi")]
    # ClaudeAgentOptions defaults allowed_tools to an empty list. We must
    # leave it untouched — anything we add bypasses callback for that tool.
    assert fake.options.allowed_tools == [], (
        "allowed_tools must be empty so callback gates every tool"
    )


@pytest.mark.asyncio
async def test_session_runner_yields_events_from_scripted_messages(monkeypatch):
    """End-to-end: assistant text + tool use → tool result → result message."""
    scripted = [
        SystemMessage(
            subtype="init",
            data={"session_id": "sid", "cwd": "/x", "model": "m", "tools": []},
        ),
        AssistantMessage(content=[TextBlock(text="Hi")], model="m"),
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="Bash", input={"command": "ls"})],
            model="m",
        ),
        UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content="file1\nfile2",
                    is_error=False,
                )
            ]
        ),
        ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=5,
            is_error=False,
            num_turns=1,
            total_cost_usd=0.001,
            usage={"input_tokens": 10, "output_tokens": 5},
            session_id="sid",
        ),
    ]
    runner, fake = _make_runner(monkeypatch, scripted=scripted)

    events = [ev async for ev in runner.send("do stuff")]
    types = [type(e).__name__ for e in events]
    assert types[0] == "SystemInit"
    assert "TextDelta" in types
    assert "ToolUse" in types
    assert "ToolResult" in types
    assert types[-1] == "StreamEnd"


@pytest.mark.asyncio
async def test_session_runner_cancel_calls_interrupt(monkeypatch):
    runner, fake = _make_runner(
        monkeypatch,
        scripted=[
            ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                total_cost_usd=0,
                usage={"input_tokens": 0, "output_tokens": 0},
                session_id="sid",
            ),
        ],
    )
    # Cancel before connect — does nothing
    assert await runner.cancel() is False
    # Connect via send()
    _ = [ev async for ev in runner.send("hi")]
    # Now cancel works
    assert await runner.cancel() is True
    assert fake.interrupts == 1


@pytest.mark.asyncio
async def test_session_runner_close_disconnects(monkeypatch):
    runner, fake = _make_runner(
        monkeypatch,
        scripted=[
            ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                total_cost_usd=0,
                usage={"input_tokens": 0, "output_tokens": 0},
                session_id="sid",
            ),
        ],
    )
    _ = [ev async for ev in runner.send("hi")]
    await runner.close()
    assert fake.disconnected is True


@pytest.mark.asyncio
async def test_session_runner_refuses_when_cli_claude_holds_same_cwd(
    monkeypatch, tmp_path
):
    """CLI-vs-bridge mutex — refuse to send while CLI Claude has the same cwd."""
    from daemon.process_inspector import ClaudeProcInfo

    fake_cli = ClaudeProcInfo(
        pid=12345,
        ppid=1,
        cwd=str(tmp_path),
        started_at=0.0,
        kind="cli",
        cmdline=("claude",),
    )
    monkeypatch.setattr(
        "daemon.process_inspector.find_cli_processes_for_cwd",
        lambda cwd: [fake_cli] if cwd else [],
    )
    # Stub out the SDK client so we don't even try to construct it
    monkeypatch.setattr("daemon.sdk_runner.ClaudeSDKClient", lambda **_: _FakeClient())

    broker = ApprovalBroker(
        redis=FakeRedis(),
        chat_id_resolver=lambda sid: 12345,
        permission_store=SessionPermissionStore(),
        timeout_seconds=5,
    )
    runner = SessionRunner(
        session_id="sid",
        cwd=str(tmp_path),
        broker=broker,
    )

    events = [ev async for ev in runner.send("hi")]
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert events[0].code == "session_busy_cli"
    assert "12345" in events[0].message


@pytest.mark.asyncio
async def test_session_runner_force_bypasses_mutex(monkeypatch, tmp_path):
    """/yolo path: force=True ignores CLI mutex check."""
    from daemon.process_inspector import ClaudeProcInfo

    fake_cli = ClaudeProcInfo(
        pid=12345,
        ppid=1,
        cwd=str(tmp_path),
        started_at=0.0,
        kind="cli",
        cmdline=("claude",),
    )

    runner, fake = _make_runner(
        monkeypatch,
        cwd=str(tmp_path),
        scripted=[
            ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                total_cost_usd=0,
                usage={"input_tokens": 0, "output_tokens": 0},
                session_id="sid",
            ),
        ],
    )
    monkeypatch.setattr(
        "daemon.process_inspector.find_cli_processes_for_cwd",
        lambda cwd: [fake_cli],
    )

    events = [ev async for ev in runner.send("hi", force=True)]
    # Mutex bypassed → ran → StreamEnd
    assert isinstance(events[-1], StreamEnd)


# ---------------------------------------------------------------------------
# SessionRunnerRegistry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_get_or_create_returns_same_instance(monkeypatch):
    broker = ApprovalBroker(
        redis=FakeRedis(),
        chat_id_resolver=lambda sid: 12345,
        permission_store=SessionPermissionStore(),
        timeout_seconds=5,
    )
    reg = SessionRunnerRegistry(broker)
    r1 = await reg.get_or_create(session_id="s", cwd="/x")
    r2 = await reg.get_or_create(session_id="s", cwd="/x")
    assert r1 is r2


@pytest.mark.asyncio
async def test_registry_close_removes_runner(monkeypatch):
    broker = ApprovalBroker(
        redis=FakeRedis(),
        chat_id_resolver=lambda sid: 12345,
        permission_store=SessionPermissionStore(),
        timeout_seconds=5,
    )
    reg = SessionRunnerRegistry(broker)
    await reg.get_or_create(session_id="s", cwd="/x")
    assert "s" in reg.active_session_ids()
    closed = await reg.close("s")
    assert closed is True
    assert "s" not in reg.active_session_ids()


@pytest.mark.asyncio
async def test_registry_cancel_unknown_session_returns_false():
    broker = ApprovalBroker(
        redis=FakeRedis(),
        chat_id_resolver=lambda sid: 12345,
        permission_store=SessionPermissionStore(),
        timeout_seconds=5,
    )
    reg = SessionRunnerRegistry(broker)
    assert await reg.cancel("does-not-exist") is False
