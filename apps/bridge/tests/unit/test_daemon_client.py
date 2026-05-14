"""Tests for bot.services.daemon_client (httpx + SSE parsing)."""
import json

import httpx
import pytest
import respx

from bot.services.daemon_client import (
    DaemonOffline,
    active_sessions,
    cancel_session,
    health,
    list_processes,
    list_sessions,
    stream_send,
)
from shared.events import StreamEnd, TextDelta, ToolUse


DAEMON = "http://daemon.test"


@pytest.mark.asyncio
@respx.mock
async def test_health_returns_payload():
    respx.get(f"{DAEMON}/health").mock(
        return_value=httpx.Response(200, json={"status": "ok", "version": "0.1.0"})
    )
    out = await health(DAEMON)
    assert out["status"] == "ok"
    assert out["version"] == "0.1.0"


@pytest.mark.asyncio
@respx.mock
async def test_health_offline_raises():
    respx.get(f"{DAEMON}/health").mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(DaemonOffline):
        await health(DAEMON)


@pytest.mark.asyncio
@respx.mock
async def test_list_sessions_unwraps_envelope():
    respx.get(f"{DAEMON}/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "sessions": [
                    {"session_id": "a", "project_path": "/x"},
                    {"session_id": "b", "project_path": "/y"},
                ]
            },
        )
    )
    sessions = await list_sessions(DAEMON)
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == "a"


@pytest.mark.asyncio
@respx.mock
async def test_list_sessions_offline_raises():
    respx.get(f"{DAEMON}/sessions").mock(side_effect=httpx.ConnectError("nope"))
    with pytest.raises(DaemonOffline):
        await list_sessions(DAEMON)


def _sse_blob(events: list[dict]) -> bytes:
    """Encode events as a valid SSE response body."""
    parts: list[str] = []
    for e in events:
        parts.append(f"data: {json.dumps(e)}\n\n")
    return "".join(parts).encode("utf-8")


@pytest.mark.asyncio
@respx.mock
async def test_stream_send_yields_typed_events():
    payload = [
        {"type": "text", "delta": "hello"},
        {"type": "tool_use", "tool": "Read", "input": {"file_path": "/x"}},
        {
            "type": "end",
            "reason": "stop",
            "is_error": False,
            "tokens_in": 10,
            "tokens_out": 5,
            "cost_usd": 0.01,
            "text": "done",
        },
    ]
    respx.post(f"{DAEMON}/sessions/sess-1/send").mock(
        return_value=httpx.Response(
            200,
            content=_sse_blob(payload),
            headers={"content-type": "text/event-stream"},
        )
    )

    events = []
    async for e in stream_send(DAEMON, "sess-1", "do thing"):
        events.append(e)

    assert len(events) == 3
    assert isinstance(events[0], TextDelta)
    assert events[0].delta == "hello"
    assert isinstance(events[1], ToolUse)
    assert events[1].tool == "Read"
    assert isinstance(events[2], StreamEnd)
    assert events[2].tokens_in == 10


@pytest.mark.asyncio
@respx.mock
async def test_stream_send_skips_unknown_event_types():
    payload = [
        {"type": "future_event", "field": 123},  # unknown — must be skipped
        {"type": "text", "delta": "ok"},
    ]
    respx.post(f"{DAEMON}/sessions/x/send").mock(
        return_value=httpx.Response(
            200,
            content=_sse_blob(payload),
            headers={"content-type": "text/event-stream"},
        )
    )

    events = [e async for e in stream_send(DAEMON, "x", "p")]
    assert len(events) == 1
    assert isinstance(events[0], TextDelta)


@pytest.mark.asyncio
@respx.mock
async def test_stream_send_skips_malformed_json():
    body = b"data: {not json\n\ndata: " + json.dumps({"type": "text", "delta": "ok"}).encode() + b"\n\n"
    respx.post(f"{DAEMON}/sessions/x/send").mock(
        return_value=httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/event-stream"},
        )
    )

    events = [e async for e in stream_send(DAEMON, "x", "p")]
    assert len(events) == 1
    assert events[0].delta == "ok"


@pytest.mark.asyncio
@respx.mock
async def test_stream_send_offline_raises():
    respx.post(f"{DAEMON}/sessions/x/send").mock(side_effect=httpx.ConnectError("dn"))
    with pytest.raises(DaemonOffline):
        async for _ in stream_send(DAEMON, "x", "p"):
            pass


@pytest.mark.asyncio
@respx.mock
async def test_stream_send_passes_cwd_when_set():
    respx.post(f"{DAEMON}/sessions/x/send").mock(
        return_value=httpx.Response(
            200,
            content=_sse_blob([{"type": "text", "delta": "ok"}]),
            headers={"content-type": "text/event-stream"},
        )
    )

    async for _ in stream_send(DAEMON, "x", "hi", cwd="/proj"):
        pass

    sent = json.loads(respx.calls[0].request.content)
    assert sent == {"prompt": "hi", "cwd": "/proj"}


@pytest.mark.asyncio
@respx.mock
async def test_cancel_session_returns_killed_payload():
    respx.post(f"{DAEMON}/sessions/sess-1/cancel").mock(
        return_value=httpx.Response(200, json={"ok": True, "killed": True})
    )
    out = await cancel_session(DAEMON, "sess-1")
    assert out["ok"] is True
    assert out["killed"] is True


@pytest.mark.asyncio
@respx.mock
async def test_cancel_session_offline_raises():
    respx.post(f"{DAEMON}/sessions/sess-1/cancel").mock(
        side_effect=httpx.ConnectError("nope")
    )
    with pytest.raises(DaemonOffline):
        await cancel_session(DAEMON, "sess-1")


@pytest.mark.asyncio
@respx.mock
async def test_active_sessions_returns_list():
    respx.get(f"{DAEMON}/sessions/active").mock(
        return_value=httpx.Response(200, json={"active": ["a", "b"]})
    )
    assert await active_sessions(DAEMON) == ["a", "b"]


@pytest.mark.asyncio
@respx.mock
async def test_list_processes_returns_payload():
    respx.get(f"{DAEMON}/processes").mock(
        return_value=httpx.Response(
            200,
            json={
                "processes": [
                    {"pid": 1, "kind": "cli", "cwd": "/proj", "started_at": 0},
                    {"pid": 2, "kind": "daemon-spawned", "cwd": "/proj", "started_at": 0},
                ]
            },
        )
    )
    out = await list_processes(DAEMON)
    assert len(out) == 2
    assert {p["kind"] for p in out} == {"cli", "daemon-spawned"}


@pytest.mark.asyncio
@respx.mock
async def test_list_processes_offline_raises():
    respx.get(f"{DAEMON}/processes").mock(side_effect=httpx.ConnectError("nope"))
    with pytest.raises(DaemonOffline):
        await list_processes(DAEMON)


@pytest.mark.asyncio
@respx.mock
async def test_stream_send_omits_cwd_when_none():
    respx.post(f"{DAEMON}/sessions/x/send").mock(
        return_value=httpx.Response(
            200,
            content=_sse_blob([{"type": "text", "delta": "ok"}]),
            headers={"content-type": "text/event-stream"},
        )
    )

    async for _ in stream_send(DAEMON, "x", "hi"):
        pass

    sent = json.loads(respx.calls[0].request.content)
    assert sent == {"prompt": "hi"}
    assert "cwd" not in sent
    assert "force" not in sent  # default False


@pytest.mark.asyncio
@respx.mock
async def test_stream_send_passes_force_flag():
    respx.post(f"{DAEMON}/sessions/x/send").mock(
        return_value=httpx.Response(
            200,
            content=_sse_blob([{"type": "text", "delta": "ok"}]),
            headers={"content-type": "text/event-stream"},
        )
    )
    async for _ in stream_send(DAEMON, "x", "p", force=True):
        pass
    sent = json.loads(respx.calls[0].request.content)
    assert sent.get("force") is True
