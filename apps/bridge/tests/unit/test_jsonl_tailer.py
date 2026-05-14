"""Tests for daemon.jsonl_tailer — file follower + publisher.

The tailer is the load-bearing piece of Part A. These tests exercise:

  * Lines appended after start are parsed + published
  * Initial file content is skipped when ``start_from_end=True`` (default)
  * Initial file content IS parsed when ``start_from_end=False`` (test mode)
  * ``stop()`` cancels the loop within one poll interval
  * File rotation (truncate then refill) is detected and offset resets
  * File deletion mid-stream emits StreamEnd and stops the loop
  * Partial trailing line is buffered, picked up after newline arrives
  * Malformed JSON yields ErrorEvent without crashing
  * UserPrompt events emitted from CLI text-block user messages
  * Secrets in published events are scrubbed (``masked=True`` flag)
  * Per-tailer monotonic ``seq`` increases across publishes
  * TailerStatus counters reflect work done
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from daemon.jsonl_tailer import JsonlTailer
from shared.follow_protocol import CliMirrorEvent, cli_mirror_channel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakePublisher:
    """Collects published (channel, payload) pairs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, channel: str, payload: str) -> None:
        self.calls.append((channel, payload))

    def parsed(self) -> list[CliMirrorEvent]:
        return [
            CliMirrorEvent.model_validate_json(p) for _, p in self.calls
        ]


def _make_init_line(session_id: str = "sid-1") -> str:
    return json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "session_id": session_id,
            "cwd": "C:/x",
            "tools": ["Bash"],
            "model": "claude-opus",
        }
    )


def _make_assistant_text(text: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]},
        }
    )


def _make_user_prompt(text: str) -> str:
    return json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": text},
        }
    )


def _make_result_line(text: str = "all done") -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "duration_ms": 100,
            "total_cost_usd": 0.01,
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }
    )


async def _await_publishes(
    pub: FakePublisher, count: int, timeout: float = 2.0
) -> None:
    """Wait until ``pub`` has at least ``count`` published events."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(pub.calls) >= count:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"publisher only got {len(pub.calls)} events, expected ≥ {count}"
    )


# ---------------------------------------------------------------------------
# Append-after-start: the default flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_appends_after_start_are_published(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text(_make_init_line() + "\n", encoding="utf-8")

    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=42,
        session_id="sid-1",
        publisher=pub,
        poll_interval_s=0.05,
        start_from_end=True,
    )
    task = asyncio.create_task(tailer.run())
    try:
        # Append after the tailer is alive
        await asyncio.sleep(0.1)
        with path.open("a", encoding="utf-8") as f:
            f.write(_make_assistant_text("hello") + "\n")

        await _await_publishes(pub, 1)
        events = pub.parsed()
        assert any(e.event.get("type") == "text" for e in events)
        assert all(e.chat_id == 42 for e in events)
        assert all(
            ch == cli_mirror_channel(42) for ch, _ in pub.calls
        )
    finally:
        tailer.stop()
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_start_from_end_skips_existing_content(tmp_path: Path):
    """Default policy — don't dump pre-existing history into TG."""
    path = tmp_path / "session.jsonl"
    path.write_text(
        _make_init_line() + "\n" + _make_assistant_text("old") + "\n",
        encoding="utf-8",
    )

    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=42,
        session_id="sid-1",
        publisher=pub,
        poll_interval_s=0.05,
        start_from_end=True,
    )
    task = asyncio.create_task(tailer.run())
    try:
        await asyncio.sleep(0.2)  # plenty of time for at least 1 poll
        assert pub.calls == []  # nothing surfaced
    finally:
        tailer.stop()
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_start_from_beginning_processes_existing(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        _make_init_line() + "\n" + _make_assistant_text("history") + "\n",
        encoding="utf-8",
    )

    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=42,
        session_id="sid-1",
        publisher=pub,
        poll_interval_s=0.05,
        start_from_end=False,
    )
    task = asyncio.create_task(tailer.run())
    try:
        await _await_publishes(pub, 2)
        events = pub.parsed()
        types = [e.event.get("type") for e in events]
        assert "system_init" in types
        assert "text" in types
    finally:
        tailer.stop()
        await asyncio.wait_for(task, timeout=2.0)


# ---------------------------------------------------------------------------
# Stop control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_exits_loop_quickly(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")
    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=1,
        session_id="sid",
        publisher=pub,
        poll_interval_s=0.1,
    )
    task = asyncio.create_task(tailer.run())
    await asyncio.sleep(0.05)
    tailer.stop()
    await asyncio.wait_for(task, timeout=1.0)
    assert tailer.status.ended is True


# ---------------------------------------------------------------------------
# File rotation / deletion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_truncation_resets_offset(tmp_path: Path):
    """If the file shrinks below our offset (truncation / recreation),
    we reset to 0 and re-read from the start so we don't miss data."""
    path = tmp_path / "session.jsonl"
    path.write_text(_make_init_line() + "\n", encoding="utf-8")

    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=1,
        session_id="sid",
        publisher=pub,
        poll_interval_s=0.05,
        start_from_end=True,
    )
    task = asyncio.create_task(tailer.run())
    try:
        await asyncio.sleep(0.1)
        # Truncate and write fresh content
        path.write_text(_make_assistant_text("after-rotation") + "\n", encoding="utf-8")
        await _await_publishes(pub, 1)
        events = pub.parsed()
        assert any(
            e.event.get("type") == "text"
            and "after-rotation" in e.event.get("delta", "")
            for e in events
        )
    finally:
        tailer.stop()
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_file_deletion_emits_stream_end_and_stops(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text(_make_init_line() + "\n", encoding="utf-8")

    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=1,
        session_id="sid",
        publisher=pub,
        poll_interval_s=0.05,
        start_from_end=True,
    )
    task = asyncio.create_task(tailer.run())
    try:
        await asyncio.sleep(0.1)
        path.unlink()
        await asyncio.wait_for(task, timeout=2.0)
        assert tailer.status.ended is True
        end_events = [
            e for e in pub.parsed() if e.event.get("type") == "end"
        ]
        assert len(end_events) == 1
        assert end_events[0].event.get("reason") == "cli_session_ended"
    finally:
        if not task.done():
            tailer.stop()
            await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_missing_file_on_start_emits_error(tmp_path: Path):
    path = tmp_path / "never_existed.jsonl"
    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=1,
        session_id="sid",
        publisher=pub,
        poll_interval_s=0.05,
    )
    await tailer.run()  # returns quickly
    events = pub.parsed()
    assert len(events) == 1
    assert events[0].event.get("type") == "error"
    assert events[0].event.get("code") == "tailer_no_file"


# ---------------------------------------------------------------------------
# Partial-line buffering + malformed input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_line_buffered_until_newline(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")

    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=1,
        session_id="sid",
        publisher=pub,
        poll_interval_s=0.05,
        start_from_end=True,
    )
    task = asyncio.create_task(tailer.run())
    try:
        await asyncio.sleep(0.1)
        # Write the first half WITHOUT a newline.
        line = _make_assistant_text("split-line")
        with path.open("a", encoding="utf-8") as f:
            f.write(line[: len(line) // 2])
            f.flush()
        await asyncio.sleep(0.15)
        # No publish yet
        assert pub.calls == []

        # Finish the line
        with path.open("a", encoding="utf-8") as f:
            f.write(line[len(line) // 2 :] + "\n")
        await _await_publishes(pub, 1)
        events = pub.parsed()
        assert events[0].event.get("type") == "text"
        assert events[0].event.get("delta") == "split-line"
    finally:
        tailer.stop()
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_malformed_json_yields_error_event_without_crash(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")

    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=1,
        session_id="sid",
        publisher=pub,
        poll_interval_s=0.05,
        start_from_end=True,
    )
    task = asyncio.create_task(tailer.run())
    try:
        await asyncio.sleep(0.1)
        with path.open("a", encoding="utf-8") as f:
            f.write("{not valid json\n")
            f.write(_make_assistant_text("ok") + "\n")
        await _await_publishes(pub, 2)
        events = pub.parsed()
        types = [e.event.get("type") for e in events]
        assert "error" in types  # malformed line surfaces
        assert "text" in types   # next line still works
    finally:
        tailer.stop()
        await asyncio.wait_for(task, timeout=2.0)


# ---------------------------------------------------------------------------
# User prompts mirrored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_prompts_surface_as_user_prompt_events(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")

    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=1,
        session_id="sid",
        publisher=pub,
        poll_interval_s=0.05,
        start_from_end=True,
    )
    task = asyncio.create_task(tailer.run())
    try:
        await asyncio.sleep(0.1)
        with path.open("a", encoding="utf-8") as f:
            f.write(_make_user_prompt("hello claude") + "\n")
        await _await_publishes(pub, 1)
        events = pub.parsed()
        types_and_text = [
            (e.event.get("type"), e.event.get("text")) for e in events
        ]
        assert ("user_prompt", "hello claude") in types_and_text
    finally:
        tailer.stop()
        await asyncio.wait_for(task, timeout=2.0)


# ---------------------------------------------------------------------------
# PII scrubbing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secrets_in_text_are_scrubbed_and_masked_flag_set(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")

    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=1,
        session_id="sid",
        publisher=pub,
        poll_interval_s=0.05,
        start_from_end=True,
    )
    task = asyncio.create_task(tailer.run())
    try:
        await asyncio.sleep(0.1)
        secret = "sk-proj-abcDEF1234567890ABCDEF1234567890ABCDABCD"
        with path.open("a", encoding="utf-8") as f:
            f.write(_make_assistant_text(f"key is {secret}") + "\n")
        await _await_publishes(pub, 1)
        events = pub.parsed()
        assert events[0].masked is True
        assert secret not in events[0].event.get("delta", "")
        assert "[REDACTED:" in events[0].event.get("delta", "")
    finally:
        tailer.stop()
        await asyncio.wait_for(task, timeout=2.0)


# ---------------------------------------------------------------------------
# Status / seq invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_tailer_seq_monotonic(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")
    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=1,
        session_id="sid",
        publisher=pub,
        poll_interval_s=0.05,
        start_from_end=True,
    )
    task = asyncio.create_task(tailer.run())
    try:
        await asyncio.sleep(0.1)
        with path.open("a", encoding="utf-8") as f:
            for i in range(3):
                f.write(_make_assistant_text(f"line{i}") + "\n")
        await _await_publishes(pub, 3)
        events = pub.parsed()
        seqs = [e.seq for e in events]
        # Strict monotonic across published events
        assert seqs == sorted(seqs)
        assert seqs[0] >= 1
    finally:
        tailer.stop()
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_status_counters_update(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")
    pub = FakePublisher()
    tailer = JsonlTailer(
        path=path,
        chat_id=1,
        session_id="sid",
        publisher=pub,
        poll_interval_s=0.05,
        start_from_end=True,
    )
    task = asyncio.create_task(tailer.run())
    try:
        await asyncio.sleep(0.1)
        with path.open("a", encoding="utf-8") as f:
            f.write(_make_assistant_text("a") + "\n")
            f.write(_make_assistant_text("b") + "\n")
        await _await_publishes(pub, 2)
        st = tailer.status
        assert st.lines_parsed >= 2
        assert st.events_published >= 2
        assert st.bytes_read > 0
        assert st.ended is False
    finally:
        tailer.stop()
        await asyncio.wait_for(task, timeout=2.0)
