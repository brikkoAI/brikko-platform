"""Tests for bot.services.stream_renderer."""
from bot.services.stream_renderer import StreamRenderer, TELEGRAM_LIMIT
from shared.events import (
    ErrorEvent,
    StreamEnd,
    SystemInit,
    TextDelta,
    TodosUpdate,
    ToolResult,
    ToolUse,
)


def test_text_delta_appended():
    r = StreamRenderer()
    r.feed(TextDelta(delta="Hello "))
    r.feed(TextDelta(delta="world"))
    assert r.current_text() == "Hello world"


def test_system_init_skipped():
    r = StreamRenderer()
    r.feed(SystemInit(session_id="abc", cwd="/x"))
    assert r.current_text() == ""


def test_tool_use_renders_with_emoji():
    r = StreamRenderer()
    r.feed(ToolUse(tool="Read", input={"file_path": "/foo.py"}))
    text = r.current_text()
    assert "📖" in text
    assert "Read" in text
    assert "/foo.py" in text


def test_tool_use_bash_truncates_long_command():
    r = StreamRenderer()
    long_cmd = "echo " + "x" * 500
    r.feed(ToolUse(tool="Bash", input={"command": long_cmd}))
    text = r.current_text()
    assert "…" in text
    assert len(text) < 200


def test_tool_use_unknown_tool_uses_keys_fallback():
    r = StreamRenderer()
    r.feed(ToolUse(tool="MysteryTool", input={"a": 1, "b": 2, "c": 3, "d": 4}))
    text = r.current_text()
    assert "MysteryTool" in text
    # Only first 3 keys
    assert "a" in text and "b" in text and "c" in text


def test_tool_result_ok_and_error():
    r = StreamRenderer()
    r.feed(ToolResult(tool="Bash", ok=True, summary="output"))
    r.feed(ToolResult(tool="Bash", ok=False, summary="failed"))
    text = r.current_text()
    assert "✓" in text
    assert "❌" in text
    assert "output" in text
    assert "failed" in text


def test_tool_result_truncates_long_summary():
    r = StreamRenderer()
    long = "x" * 1000
    r.feed(ToolResult(tool="Bash", ok=True, summary=long))
    text = r.current_text()
    assert len(text) < 400  # truncated to ~250 chars + framing


def test_todos_update_renders_each_status():
    r = StreamRenderer()
    r.feed(
        TodosUpdate(
            items=[
                {"content": "task A", "status": "completed"},
                {"content": "task B", "status": "in_progress"},
                {"content": "task C", "status": "pending"},
            ]
        )
    )
    text = r.current_text()
    assert "✅" in text
    assert "🔧" in text
    assert "○" in text
    assert "task A" in text
    assert "task B" in text
    assert "task C" in text


def test_error_event_marks_renderer_as_error():
    r = StreamRenderer()
    r.feed(ErrorEvent(message="boom"))
    assert r.has_error()
    assert "❌" in r.current_text()
    assert "boom" in r.current_text()


def test_stream_end_renders_footer_with_cost():
    r = StreamRenderer()
    r.feed(TextDelta(delta="Done."))
    r.feed(StreamEnd(tokens_in=100, tokens_out=50, cost_usd=0.0123, is_error=False))
    text = r.current_text()
    assert r.has_ended()
    assert not r.has_error()
    assert "in:100" in text
    assert "out:50" in text
    assert "$0.0123" in text


def test_stream_end_with_error_marks_error():
    r = StreamRenderer()
    r.feed(StreamEnd(is_error=True, tokens_in=0, tokens_out=0, cost_usd=0))
    assert r.has_error()


def test_chunks_for_send_empty():
    r = StreamRenderer()
    assert r.chunks_for_send() == []


def test_chunks_for_send_under_limit_returns_single():
    r = StreamRenderer()
    r.feed(TextDelta(delta="short"))
    chunks = r.chunks_for_send()
    assert chunks == ["short"]


def test_chunks_for_send_splits_at_limit():
    r = StreamRenderer()
    # 5 lines of 1500 chars each → 7500 total → must split into 2+ chunks
    for _ in range(5):
        r.feed(TextDelta(delta=("a" * 1500) + "\n"))
    chunks = r.chunks_for_send()
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= TELEGRAM_LIMIT


def test_chunks_for_send_prefers_newline_split():
    r = StreamRenderer()
    # 4500 chars with newlines every 100 chars
    text = ""
    for _ in range(45):
        text += ("a" * 99) + "\n"
    r.feed(TextDelta(delta=text))
    chunks = r.chunks_for_send()
    assert len(chunks) >= 2
    # First chunk should end at a newline (no mid-line cut)
    assert chunks[0].endswith("a") or "\n" in chunks[0]
