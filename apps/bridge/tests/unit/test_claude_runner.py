"""Tests for daemon.claude_runner using a mock claude.exe."""
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from daemon.claude_runner import run_claude
from shared.events import (
    SystemInit,
    TextDelta,
    ToolUse,
    ToolResult,
    StreamEnd,
    ErrorEvent,
)


MOCK_SRC = (
    Path(__file__).parent.parent / "fixtures" / "mock_claude.py"
).resolve()


@pytest.fixture
def mock_claude(monkeypatch, tmp_path_factory):
    """Replace BRIDGE_DAEMON_CLAUDE_BINARY with a python script wrapper.

    Copies mock_claude.py to tmp dir (ASCII path) — pytest's tmp dir uses
    %TEMP% which is always under Users/<user>/AppData/Local/Temp on Windows,
    avoiding cyrillic path issues from the project root.
    """
    # Use a tempdir that's guaranteed ASCII-clean
    tmp = Path(tempfile.mkdtemp(prefix="bridge_mock_"))
    mock_in_tmp = tmp / "mock_claude.py"
    shutil.copy(MOCK_SRC, mock_in_tmp)

    if sys.platform == "win32":
        wrapper = tmp / "mock_claude.bat"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{mock_in_tmp}" %*\r\n',
            encoding="ascii",
        )
    else:
        wrapper = tmp / "mock_claude.sh"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{mock_in_tmp}" "$@"\n',
            encoding="ascii",
        )
        wrapper.chmod(0o755)

    monkeypatch.setenv("BRIDGE_DAEMON_CLAUDE_BINARY", str(wrapper))
    yield wrapper
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_run_claude_yields_expected_event_types(mock_claude):
    events = []
    async for ev in run_claude(session_id="abc-123", prompt="hello"):
        events.append(ev)

    types = [type(e).__name__ for e in events]
    # Must include SystemInit at start
    assert types[0] == "SystemInit"
    # Must end with StreamEnd
    assert types[-1] == "StreamEnd"
    # Must have at least one TextDelta and one ToolUse and one ToolResult
    assert any(isinstance(e, TextDelta) for e in events)
    assert any(isinstance(e, ToolUse) for e in events)
    assert any(isinstance(e, ToolResult) for e in events)


@pytest.mark.asyncio
async def test_run_claude_passes_correct_args_to_subprocess(mock_claude):
    """Mock claude asserts presence of --print, --output-format=stream-json, --dangerously-skip-permissions."""
    events = []
    async for ev in run_claude(session_id="my-id", prompt="prompt"):
        events.append(ev)
    # If args weren't passed correctly, mock would crash and we'd see an ErrorEvent or non-zero exit
    last = events[-1]
    assert isinstance(last, StreamEnd), f"expected StreamEnd, got {type(last).__name__}: {last}"
    assert last.is_error is False


@pytest.mark.asyncio
async def test_run_claude_handles_missing_binary(monkeypatch):
    monkeypatch.setenv("BRIDGE_DAEMON_CLAUDE_BINARY", "/nonexistent/path/to/claude")
    events = []
    async for ev in run_claude(session_id="abc", prompt="x"):
        events.append(ev)
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert events[0].code == "binary_missing"


@pytest.mark.asyncio
async def test_run_claude_emits_streamend_with_cost(mock_claude):
    events = [e async for e in run_claude(session_id="x", prompt="y")]
    end = events[-1]
    assert isinstance(end, StreamEnd)
    assert end.cost_usd == pytest.approx(0.001)
    assert end.tokens_in == 50
    assert end.tokens_out == 10


@pytest.mark.asyncio
async def test_run_claude_refuses_when_cli_claude_holds_same_cwd(
    mock_claude, monkeypatch
):
    """CLI-vs-bridge mutex — refuse to spawn so the user doesn't get
    two procs writing to the same session jsonl."""
    from daemon.process_inspector import ClaudeProcInfo

    fake_cli = ClaudeProcInfo(
        pid=99999, ppid=1, cwd="C:/proj", started_at=1700000000.0,
        kind="cli", cmdline=("claude",),
    )
    monkeypatch.setattr(
        "daemon.process_inspector.find_cli_processes_for_cwd",
        lambda cwd: [fake_cli] if cwd else [],
    )

    events = [e async for e in run_claude(
        session_id="abc", prompt="hi", cwd="C:/proj"
    )]
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert events[0].code == "session_busy_cli"
    assert "99999" in events[0].message  # PID surfaced to user


@pytest.mark.asyncio
async def test_run_claude_proceeds_when_no_cli_claude(
    mock_claude, monkeypatch, tmp_path
):
    """No CLI clash → run normally. Uses tmp_path so subprocess.cwd= works."""
    monkeypatch.setattr(
        "daemon.process_inspector.find_cli_processes_for_cwd",
        lambda cwd: [],
    )
    events = [e async for e in run_claude(
        session_id="abc", prompt="hi", cwd=str(tmp_path)
    )]
    assert isinstance(events[-1], StreamEnd)
    assert events[-1].is_error is False


@pytest.mark.asyncio
async def test_run_claude_force_bypasses_mutex(
    mock_claude, monkeypatch, tmp_path
):
    """/yolo path: force=True ignores any CLI-claude in the same cwd."""
    from daemon.process_inspector import ClaudeProcInfo

    blocking = ClaudeProcInfo(
        pid=99999, ppid=1, cwd=str(tmp_path), started_at=0.0,
        kind="cli", cmdline=("claude",),
    )
    monkeypatch.setattr(
        "daemon.process_inspector.find_cli_processes_for_cwd",
        lambda cwd: [blocking],
    )
    events = [e async for e in run_claude(
        session_id="abc", prompt="hi", cwd=str(tmp_path), force=True
    )]
    # Mutex was bypassed → ran normally → got StreamEnd
    assert isinstance(events[-1], StreamEnd)
    assert events[-1].is_error is False


@pytest.mark.asyncio
async def test_run_claude_skips_mutex_when_cwd_not_set(mock_claude, monkeypatch):
    """If caller didn't pass cwd, we can't compare; just run."""
    called = False

    def mock_find(cwd):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        "daemon.process_inspector.find_cli_processes_for_cwd", mock_find
    )
    events = [e async for e in run_claude(session_id="abc", prompt="hi")]
    assert called is False
    assert isinstance(events[-1], StreamEnd)
