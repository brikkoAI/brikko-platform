"""Test GET /processes."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from daemon.main import app
from daemon.process_inspector import ClaudeProcInfo


def test_processes_returns_classified_list():
    fake = [
        ClaudeProcInfo(pid=10, ppid=1, cwd="C:/proj", started_at=1700000000.0,
                       kind="cli", cmdline=("claude",)),
        ClaudeProcInfo(pid=20, ppid=2, cwd="C:/proj", started_at=1700000100.0,
                       kind="daemon-spawned",
                       cmdline=("claude", "--resume", "x", "--print", "p")),
    ]
    with patch("daemon.process_inspector.list_claude_processes", return_value=fake):
        client = TestClient(app)
        r = client.get("/processes")
    assert r.status_code == 200
    payload = r.json()["processes"]
    assert len(payload) == 2
    by_pid = {p["pid"]: p for p in payload}
    assert by_pid[10]["kind"] == "cli"
    assert by_pid[10]["cwd"] == "C:/proj"
    assert by_pid[20]["kind"] == "daemon-spawned"


def test_processes_returns_empty_list_when_no_claude_running():
    with patch("daemon.process_inspector.list_claude_processes", return_value=[]):
        client = TestClient(app)
        r = client.get("/processes")
    assert r.status_code == 200
    assert r.json() == {"processes": []}
