"""Test that GET /health returns expected shape."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from daemon.main import app


@pytest.fixture
def fake_claude_version(monkeypatch):
    """Replace _claude_version so tests don't require claude on PATH."""

    async def fake() -> str:
        return "claude 2.1.128 (Claude Code)"

    monkeypatch.setattr("daemon.api._claude_version", fake)


def test_health_returns_200_with_expected_fields(fake_claude_version):
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert body["claude_version"] == "claude 2.1.128 (Claude Code)"


def test_health_handles_missing_claude_binary(monkeypatch):
    """If `claude --version` errors, we still return 200 with claude_version='unknown'."""

    async def fake() -> str:
        return "unknown"

    monkeypatch.setattr("daemon.api._claude_version", fake)
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["claude_version"] == "unknown"


def test_run_refuses_to_bind_public_ip(monkeypatch):
    """Daemon must refuse listening on anything other than loopback."""
    from daemon import main as main_mod

    monkeypatch.setenv("BRIDGE_DAEMON_LISTEN_HOST", "0.0.0.0")
    # Reload settings cache so monkeypatched env is picked up
    from daemon.config import get_settings

    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

    with pytest.raises(RuntimeError, match="loopback only"):
        main_mod.run()
