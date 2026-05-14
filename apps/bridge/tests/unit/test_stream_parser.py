"""Tests for daemon.stream_parser.

Uses Day 1 fixtures captured from actual claude.exe v2.1.128 runs:
  - 01_simple_prompt.jsonl       (Glob + Read, no approval)
  - 02_edit_without_skip_permissions_REJECTED.jsonl  (Edit blocked)
  - 03_edit_with_skip_permissions_OK.jsonl  (Read + Edit applied)
"""
import json
from pathlib import Path

import pytest

from daemon.stream_parser import parse_stream_lines, parse_stream_line
from shared.events import (
    SystemInit,
    TextDelta,
    ToolUse,
    ToolResult,
    StreamEnd,
    ErrorEvent,
)


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "claude_stream_samples"


def _read_fixture(name: str) -> list[str]:
    path = FIXTURE_DIR / name
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# === Single-line tests ===


def test_parse_blank_line_returns_none():
    assert parse_stream_line("") is None
    assert parse_stream_line("   \n") is None


def test_parse_invalid_json_returns_error_event():
    e = parse_stream_line("{not valid json")
    assert isinstance(e, ErrorEvent)
    assert e.code == "parse_error"


def test_parse_unknown_type_returns_error_event():
    line = json.dumps({"type": "totally_made_up", "foo": "bar"})
    e = parse_stream_line(line)
    assert isinstance(e, ErrorEvent)
    assert e.code == "unknown_type"


def test_parse_system_init_event():
    line = json.dumps({
        "type": "system",
        "subtype": "init",
        "session_id": "abc-123",
        "cwd": "C:/work",
        "model": "claude-sonnet-4-5",
        "tools": ["Read", "Edit", "Bash"],
    })
    e = parse_stream_line(line)
    assert isinstance(e, SystemInit)
    assert e.session_id == "abc-123"
    assert e.cwd == "C:/work"
    assert e.model == "claude-sonnet-4-5"
    assert e.tools_available == ["Read", "Edit", "Bash"]


def test_parse_assistant_text_event():
    line = json.dumps({
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "Hello world"}],
        },
    })
    events = list(parse_stream_lines([line]))
    text_events = [e for e in events if isinstance(e, TextDelta)]
    assert len(text_events) == 1
    assert text_events[0].delta == "Hello world"


def test_parse_assistant_tool_use_event():
    line = json.dumps({
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "auth.py"},
                }
            ],
        },
    })
    events = list(parse_stream_lines([line]))
    tu = [e for e in events if isinstance(e, ToolUse)]
    assert len(tu) == 1
    assert tu[0].tool == "Read"
    assert tu[0].input == {"file_path": "auth.py"}


def test_parse_user_tool_result_event_ok():
    line = json.dumps({
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "is_error": False,
                    "content": "1\told line\n2\tnew line\n",
                }
            ],
        },
    })
    events = list(parse_stream_lines([line]))
    tr = [e for e in events if isinstance(e, ToolResult)]
    assert len(tr) == 1
    assert tr[0].ok is True
    assert "old line" in tr[0].summary


def test_parse_user_tool_result_error():
    line = json.dumps({
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "is_error": True,
                    "content": "Permission denied",
                }
            ],
        },
    })
    events = list(parse_stream_lines([line]))
    tr = [e for e in events if isinstance(e, ToolResult)]
    assert len(tr) == 1
    assert tr[0].ok is False
    assert tr[0].summary == "Permission denied"


def test_parse_result_event_emits_stream_end():
    line = json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 12345,
        "num_turns": 3,
        "total_cost_usd": 0.092,
        "result": "Done!",
        "usage": {"input_tokens": 100, "output_tokens": 50},
    })
    e = parse_stream_line(line)
    assert isinstance(e, StreamEnd)
    assert e.is_error is False
    assert e.cost_usd == pytest.approx(0.092)
    assert e.tokens_in == 100
    assert e.tokens_out == 50
    assert e.text == "Done!"


def test_parse_rate_limit_event_skipped_silently():
    """rate_limit_event is informational; we don't surface it to user."""
    line = json.dumps({"type": "rate_limit_event", "remaining": 100})
    e = parse_stream_line(line)
    assert e is None


# === Real-fixture tests (Day 1 captures) ===


def test_fixture_01_simple_prompt_parses_to_expected_events():
    """Glob + Read happy-path session."""
    lines = _read_fixture("01_simple_prompt.jsonl")
    assert len(lines) == 9   # captured during Day 1

    events = list(parse_stream_lines(lines))

    # Must have exactly one system init at the start
    assert isinstance(events[0], SystemInit)
    assert events[0].session_id  # non-empty UUID

    # Must have at least 2 tool uses (Glob, Read)
    tools = [e.tool for e in events if isinstance(e, ToolUse)]
    assert "Glob" in tools or "Read" in tools

    # Must end with StreamEnd, not error
    last = events[-1]
    assert isinstance(last, StreamEnd)
    assert last.is_error is False
    assert last.cost_usd > 0


def test_fixture_02_edit_rejected_shows_tool_result_error():
    """Edit without --dangerously-skip-permissions: tool_result has error=True."""
    lines = _read_fixture("02_edit_without_skip_permissions_REJECTED.jsonl")
    events = list(parse_stream_lines(lines))

    # Must contain a ToolResult with ok=False (the rejected Edit)
    rejections = [e for e in events if isinstance(e, ToolResult) and not e.ok]
    assert len(rejections) >= 1
    assert "permission" in rejections[0].summary.lower()


def test_fixture_03_edit_with_skip_permissions_succeeds():
    """Edit with --dangerously-skip-permissions: completes successfully."""
    lines = _read_fixture("03_edit_with_skip_permissions_OK.jsonl")
    events = list(parse_stream_lines(lines))

    # Must have ToolUse Edit
    edits = [e for e in events if isinstance(e, ToolUse) and e.tool == "Edit"]
    assert len(edits) >= 1

    # Must end with successful StreamEnd
    last = events[-1]
    assert isinstance(last, StreamEnd)
    assert last.is_error is False


# === Part A — user prompt surfacing (CLI mirror) ===

from shared.events import UserPrompt  # noqa: E402


def test_user_prompt_NOT_emitted_when_flag_off_string_content():
    """The default (daemon prompt path) must NOT emit UserPrompt — that would
    echo the bot's own prompt back as a fake CLI input."""
    line = json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}})
    events = list(parse_stream_lines([line]))
    assert not any(isinstance(e, UserPrompt) for e in events)


def test_user_prompt_emitted_when_flag_on_string_content():
    line = json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}})
    events = list(parse_stream_lines([line], include_user_prompts=True))
    user_prompts = [e for e in events if isinstance(e, UserPrompt)]
    assert len(user_prompts) == 1
    assert user_prompts[0].text == "hi"


def test_user_prompt_emitted_from_list_text_block():
    """Some CLI versions write user content as list[{type:text, text}]."""
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "hello claude"}],
            },
        }
    )
    events = list(parse_stream_lines([line], include_user_prompts=True))
    user_prompts = [e for e in events if isinstance(e, UserPrompt)]
    assert len(user_prompts) == 1
    assert user_prompts[0].text == "hello claude"


def test_user_prompt_continuation_blurb_filtered():
    """``This session is being continued`` is auto-injected by /compact —
    don't mirror it to TG as if the user typed it."""
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "This session is being continued from a previous one…",
            },
        }
    )
    events = list(parse_stream_lines([line], include_user_prompts=True))
    assert not any(isinstance(e, UserPrompt) for e in events)


def test_user_tool_result_still_emitted_with_user_prompt_flag_on():
    """Don't regress the existing tool_result emission when the flag is on."""
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "is_error": False,
                        "content": "file contents",
                    }
                ],
            },
        }
    )
    events = list(parse_stream_lines([line], include_user_prompts=True))
    tool_results = [e for e in events if isinstance(e, ToolResult)]
    assert len(tool_results) == 1
    assert tool_results[0].summary == "file contents"


def test_summary_type_line_skipped():
    """``type=summary`` rows appear in CLI jsonl when auto-summarised; not
    interesting for live mirroring (and existed before the parser had a
    case for it — was previously surfaced as unknown_type ErrorEvent)."""
    line = json.dumps({"type": "summary", "summary": "Day 5 session"})
    events = list(parse_stream_lines([line]))
    assert events == []
