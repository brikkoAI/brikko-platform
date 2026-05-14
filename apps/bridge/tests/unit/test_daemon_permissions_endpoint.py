"""Test GET /sessions/{id}/permissions and POST .../reset.

Both endpoints read/write daemon.approval_state.DEFAULT_STORE.
"""

import pytest
from fastapi.testclient import TestClient

from daemon.approval_state import DEFAULT_STORE
from daemon.main import app


@pytest.fixture(autouse=True)
def reset_store():
    DEFAULT_STORE.clear_all()
    yield
    DEFAULT_STORE.clear_all()


def test_permissions_get_empty_returns_empty_lists():
    client = TestClient(app)
    r = client.get("/sessions/sid-1/permissions")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "session_id": "sid-1",
        "always_allow": [],
        "always_deny": [],
    }


def test_permissions_get_returns_sorted_entries():
    DEFAULT_STORE.add_always_allow("sid-1", "Write")
    DEFAULT_STORE.add_always_allow("sid-1", "Edit")
    DEFAULT_STORE.add_always_deny("sid-1", "Bash")

    client = TestClient(app)
    r = client.get("/sessions/sid-1/permissions")
    body = r.json()
    assert body["always_allow"] == ["Edit", "Write"]
    assert body["always_deny"] == ["Bash"]


def test_permissions_get_isolates_sessions():
    DEFAULT_STORE.add_always_allow("sid-A", "Edit")

    client = TestClient(app)
    r = client.get("/sessions/sid-B/permissions")
    body = r.json()
    assert body["always_allow"] == []


def test_permissions_reset_clears_session_cache():
    DEFAULT_STORE.add_always_allow("sid-1", "Edit")
    DEFAULT_STORE.add_always_allow("sid-1", "Write")

    client = TestClient(app)
    r = client.post("/sessions/sid-1/permissions/reset")
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "session_id": "sid-1"}

    # Cache emptied
    r2 = client.get("/sessions/sid-1/permissions")
    assert r2.json()["always_allow"] == []


def test_permissions_reset_only_target_session():
    DEFAULT_STORE.add_always_allow("sid-A", "Edit")
    DEFAULT_STORE.add_always_allow("sid-B", "Write")

    client = TestClient(app)
    client.post("/sessions/sid-A/permissions/reset")

    # sid-B's cache is untouched
    r = client.get("/sessions/sid-B/permissions")
    assert r.json()["always_allow"] == ["Write"]


def test_permissions_reset_unknown_session_is_idempotent():
    client = TestClient(app)
    r = client.post("/sessions/never-existed/permissions/reset")
    assert r.status_code == 200
    assert r.json()["ok"] is True
