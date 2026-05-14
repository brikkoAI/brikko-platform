"""Test GET /follows — surfaces FollowRegistry snapshot to the bot."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from daemon.jsonl_tailer import TailerStatus
from daemon.main import app


@dataclass
class _FakeReg:
    rows: list[TailerStatus]

    def snapshot(self, *, chat_id: int | None = None):
        if chat_id is None:
            return self.rows
        return [r for r in self.rows if r.chat_id == chat_id]


def test_follows_endpoint_returns_empty_when_no_registry(monkeypatch):
    monkeypatch.setattr(
        "daemon.api.get_shared_follow_registry", lambda: None
    )
    client = TestClient(app)
    resp = client.get("/follows", params={"chat_id": 1})
    assert resp.status_code == 200
    assert resp.json() == {"follows": []}


def test_follows_endpoint_returns_snapshot_rows(monkeypatch):
    rows = [
        TailerStatus(
            chat_id=42,
            session_id="abc-123",
            path="C:/x/abc-123.jsonl",
            bytes_read=100,
            lines_parsed=5,
            events_published=4,
            started_at=10.0,
            last_activity_at=20.0,
            ended=False,
        )
    ]
    monkeypatch.setattr(
        "daemon.api.get_shared_follow_registry",
        lambda: _FakeReg(rows=rows),
    )
    client = TestClient(app)
    resp = client.get("/follows", params={"chat_id": 42})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["follows"]) == 1
    row = data["follows"][0]
    assert row["chat_id"] == 42
    assert row["session_id"] == "abc-123"
    assert row["events_published"] == 4
    assert row["ended"] is False


def test_follows_endpoint_filters_by_chat_id(monkeypatch):
    rows = [
        TailerStatus(
            chat_id=1, session_id="a", path="p", bytes_read=0,
            lines_parsed=0, events_published=0, started_at=0,
            last_activity_at=0, ended=False,
        ),
        TailerStatus(
            chat_id=2, session_id="b", path="p", bytes_read=0,
            lines_parsed=0, events_published=0, started_at=0,
            last_activity_at=0, ended=False,
        ),
    ]
    monkeypatch.setattr(
        "daemon.api.get_shared_follow_registry",
        lambda: _FakeReg(rows=rows),
    )
    client = TestClient(app)
    resp = client.get("/follows", params={"chat_id": 1})
    data = resp.json()
    assert len(data["follows"]) == 1
    assert data["follows"][0]["chat_id"] == 1


def test_follows_endpoint_requires_chat_id():
    """FastAPI should 422 when chat_id is missing — protects from accidental
    info disclosure to anyone hitting the endpoint without a chat id."""
    client = TestClient(app)
    resp = client.get("/follows")
    assert resp.status_code == 422
