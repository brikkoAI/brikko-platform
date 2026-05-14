"""End-to-end integration test for POST /sessions/{id}/send.

Uses mock claude.exe (tests/fixtures/mock_claude.py) — no real Anthropic
API calls. Verifies the daemon streams SSE events that the bot can
parse via the same stream_parser.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from daemon.main import app

MOCK_SRC = (Path(__file__).parent.parent / "fixtures" / "mock_claude.py").resolve()


@pytest.fixture
def mock_claude_binary(monkeypatch):
    """Wrapper script in ASCII-clean tempdir (project root has cyrillic)."""
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


def test_send_prompt_streams_sse_events(mock_claude_binary):
    client = TestClient(app)
    with client.stream(
        "POST",
        "/sessions/test-session/send",
        json={"prompt": "hi"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        # Collect raw SSE lines
        chunks = list(resp.iter_lines())

    # SSE format: each event is "data: <json>" + blank line
    data_lines = [c for c in chunks if c.startswith("data:")]
    assert len(data_lines) >= 4  # init + at least one tool_use + tool_result + end

    # Parse each event JSON
    parsed = []
    for line in data_lines:
        payload = line[len("data: "):]
        parsed.append(json.loads(payload))

    types = [e.get("type") for e in parsed]
    assert types[0] == "system_init"
    assert types[-1] == "end"
    assert "tool_use" in types
    assert "tool_result" in types
    assert "text" in types


def test_send_prompt_streamed_end_has_cost(mock_claude_binary):
    client = TestClient(app)
    with client.stream(
        "POST",
        "/sessions/abc/send",
        json={"prompt": "test"},
    ) as resp:
        chunks = [c for c in resp.iter_lines() if c.startswith("data:")]

    last = json.loads(chunks[-1][len("data: "):])
    assert last["type"] == "end"
    assert last["is_error"] is False
    assert last["cost_usd"] == pytest.approx(0.001)
    assert last["tokens_in"] == 50
    assert last["tokens_out"] == 10
