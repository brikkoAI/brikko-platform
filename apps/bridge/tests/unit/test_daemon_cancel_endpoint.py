"""Test POST /sessions/{id}/cancel and GET /sessions/active."""
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from daemon import runner_registry
from daemon.main import app


@pytest.fixture(autouse=True)
def reset_registry():
    runner_registry._active.clear()
    yield
    runner_registry._active.clear()


def _live_proc():
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 1234
    return proc


def test_cancel_kills_live_session():
    proc = _live_proc()
    runner_registry.register("sess-1", proc)

    client = TestClient(app)
    r = client.post("/sessions/sess-1/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["killed"] is True
    proc.terminate.assert_called_once()


def test_cancel_returns_not_running_for_idle_session():
    client = TestClient(app)
    r = client.post("/sessions/missing/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["killed"] is False
    assert body["reason"] == "not_running"


def test_active_endpoint_lists_running_sessions():
    runner_registry.register("a", _live_proc())
    runner_registry.register("b", _live_proc())

    client = TestClient(app)
    r = client.get("/sessions/active")
    assert r.status_code == 200
    assert set(r.json()["active"]) == {"a", "b"}
