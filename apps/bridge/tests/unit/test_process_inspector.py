"""Tests for daemon.process_inspector."""
from unittest.mock import MagicMock, patch

import pytest

from daemon import process_inspector
from daemon.process_inspector import (
    ClaudeProcInfo,
    find_cli_processes_for_cwd,
    list_claude_processes,
)


def _proc(name: str, cmdline: list[str], cwd: str = "C:/proj", pid: int = 100) -> MagicMock:
    """Build a fake psutil.Process mock with the .info dict shape."""
    p = MagicMock()
    p.info = {
        "pid": pid,
        "ppid": 1,
        "name": name,
        "cmdline": cmdline,
        "cwd": cwd,
        "create_time": 1700000000.0,
    }
    return p


def test_lists_only_claude_processes():
    procs = [
        _proc("notepad.exe", []),
        _proc("claude.exe", ["claude"]),
        _proc("python.exe", ["python", "-m", "stuff"]),
        _proc("CLAUDE.EXE", ["claude", "--help"], pid=200),  # case-insensitive name
    ]
    with patch("psutil.process_iter", return_value=procs):
        out = list_claude_processes()
    assert {p.pid for p in out} == {100, 200}


def test_classifies_daemon_spawned():
    """daemon.claude_runner uses both --print and --resume."""
    procs = [
        _proc(
            "claude.exe",
            ["claude", "--resume", "abc-uuid", "--print", "do thing",
             "--output-format=stream-json", "--verbose",
             "--dangerously-skip-permissions"],
            pid=300,
        ),
    ]
    with patch("psutil.process_iter", return_value=procs):
        out = list_claude_processes()
    assert len(out) == 1
    assert out[0].kind == "daemon-spawned"


def test_classifies_cli_when_no_print_or_resume():
    procs = [
        _proc("claude.exe", ["claude"]),  # bare REPL
        _proc("claude.exe", ["claude", "--resume", "x"], pid=101),  # has --resume but no --print
        _proc("claude.exe", ["claude", "--print", "x"], pid=102),  # has --print but no --resume
    ]
    with patch("psutil.process_iter", return_value=procs):
        out = list_claude_processes()
    assert all(p.kind == "cli" for p in out)


def test_skips_processes_that_vanished():
    """psutil raises NoSuchProcess if proc died mid-iteration; skip cleanly."""
    import psutil
    bad = MagicMock()
    bad.info = {"name": "claude.exe", "pid": 1, "ppid": 1, "cmdline": [], "cwd": "/x", "create_time": 0}
    # Make accessing .info raise — simulating partial enumeration
    type(bad).info = property(lambda self: (_ for _ in ()).throw(psutil.NoSuchProcess(1)))

    good = _proc("claude.exe", ["claude"], pid=2)
    with patch("psutil.process_iter", return_value=[bad, good]):
        out = list_claude_processes()
    # bad skipped, good kept
    assert {p.pid for p in out} == {2}


def test_find_cli_for_cwd_matches_case_insensitive():
    procs = [
        _proc("claude.exe", ["claude"], cwd="C:\\Users\\x\\Project", pid=10),
        _proc("claude.exe", ["claude"], cwd="C:/Other/Project", pid=11),
    ]
    with patch("psutil.process_iter", return_value=procs):
        out = find_cli_processes_for_cwd("C:/users/x/project")
    assert {p.pid for p in out} == {10}


def test_find_cli_ignores_daemon_spawned():
    procs = [
        _proc("claude.exe", ["claude", "--resume", "x", "--print", "p"], cwd="C:/proj", pid=10),
        _proc("claude.exe", ["claude"], cwd="C:/proj", pid=11),
    ]
    with patch("psutil.process_iter", return_value=procs):
        out = find_cli_processes_for_cwd("C:/proj")
    assert [p.pid for p in out] == [11]


def test_find_cli_returns_empty_for_blank_cwd():
    """Defensive: if caller passes None/'', don't match every CLI proc on system."""
    procs = [_proc("claude.exe", ["claude"], cwd="C:/proj")]
    with patch("psutil.process_iter", return_value=procs):
        assert find_cli_processes_for_cwd("") == []
        assert find_cli_processes_for_cwd(None) == []  # type: ignore[arg-type]


def test_find_cli_handles_proc_without_cwd():
    """Some procs (system) have None cwd — must not crash on _norm(None)."""
    procs = [
        _proc("claude.exe", ["claude"], cwd=None, pid=10),
        _proc("claude.exe", ["claude"], cwd="C:/proj", pid=11),
    ]
    with patch("psutil.process_iter", return_value=procs):
        out = find_cli_processes_for_cwd("C:/proj")
    assert [p.pid for p in out] == [11]
