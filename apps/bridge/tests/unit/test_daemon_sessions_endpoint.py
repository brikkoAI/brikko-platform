"""Test that GET /sessions returns the array from list_sessions()."""
from fastapi.testclient import TestClient

from daemon.main import app


def test_sessions_endpoint_returns_array(monkeypatch):
    fake_sessions = [
        {
            "session_id": "abc-123",
            "project_path": "C--Users-x-repo",
            "last_modified": 1714654321.0,
            "summary": "fixing tests",
        },
        {
            "session_id": "def-456",
            "project_path": "C--Users-x-other",
            "last_modified": 1714654000.0,
            "summary": None,
        },
    ]
    monkeypatch.setattr("daemon.api.list_sessions", lambda: fake_sessions)
    client = TestClient(app)
    resp = client.get("/sessions")
    assert resp.status_code == 200
    assert resp.json() == {"sessions": fake_sessions}


def test_sessions_endpoint_returns_empty_when_no_sessions(monkeypatch):
    monkeypatch.setattr("daemon.api.list_sessions", lambda: [])
    client = TestClient(app)
    resp = client.get("/sessions")
    assert resp.status_code == 200
    assert resp.json() == {"sessions": []}
