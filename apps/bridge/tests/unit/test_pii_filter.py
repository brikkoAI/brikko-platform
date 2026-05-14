"""Tests for daemon.pii_filter — secret scrubber."""

from __future__ import annotations

from daemon.pii_filter import scrub_event, scrub_text
from shared.events import (
    ErrorEvent,
    StreamEnd,
    TextDelta,
    ToolResult,
    ToolUse,
    UserPrompt,
)


def test_scrub_text_no_match_passes_through():
    text, modified = scrub_text("hello world, no secrets here")
    assert text == "hello world, no secrets here"
    assert modified is False


def test_scrub_text_empty_string():
    text, modified = scrub_text("")
    assert text == ""
    assert modified is False


def test_scrub_text_openai_key():
    text, modified = scrub_text("OPENAI=sk-proj-abcDEF1234567890ABCDEF1234567890ABCDEF1234")
    assert modified is True
    assert "sk-proj-" not in text
    assert "[REDACTED:openai_key]" in text


def test_scrub_text_anthropic_key():
    secret = "sk-ant-api03-" + "A" * 50
    text, modified = scrub_text(f"key={secret}")
    assert modified is True
    assert secret not in text
    assert "[REDACTED:anthropic_key]" in text


def test_scrub_text_brikko_key():
    text, modified = scrub_text("brk_live_AbCdEf1234567890XYZ")
    assert modified is True
    assert "brk_live_" not in text
    assert "[REDACTED:brikko_key]" in text


def test_scrub_text_brikko_mcp_token():
    text, modified = scrub_text("token=mcp-brk-1234567890abcdefxyz")
    assert modified is True
    assert "mcp-brk-" not in text
    assert "[REDACTED:brikko_mcp_token]" in text


def test_scrub_text_jwt():
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NSJ9."
        "abcdefghijklmnop123"
    )
    text, modified = scrub_text(jwt)
    assert modified is True
    assert "[REDACTED:jwt]" in text


def test_scrub_text_bearer_token():
    text, modified = scrub_text("Authorization: Bearer abcdef1234567890XYZABC123456")
    assert modified is True
    assert "Bearer " not in text
    assert "[REDACTED:bearer_token]" in text


def test_scrub_text_aws_access_key():
    text, modified = scrub_text("AWS_KEY=AKIAIOSFODNN7EXAMPLE")
    assert modified is True
    assert "AKIA" not in text


def test_scrub_text_google_api_key():
    text, modified = scrub_text("AIzaSyA-12345_67890_abcdef_ghijkl_mnopqr_x")
    assert modified is True
    assert "AIza" not in text


def test_scrub_text_multiple_secrets_in_one_string():
    """Two distinct secret types in one string both get redacted."""
    inp = (
        "openai=sk-proj-abcDEF1234567890ABCDEF1234567890ABCDEFABCD "
        "and aws=AKIAIOSFODNN7EXAMPLE"
    )
    text, modified = scrub_text(inp)
    assert modified is True
    assert "[REDACTED:openai_key]" in text
    assert "[REDACTED:aws_access_key]" in text


def test_scrub_text_short_sk_not_matched():
    """``sk-abc`` alone shouldn't trigger — too short, false-positive risk."""
    text, modified = scrub_text("function sk-abc()")
    assert modified is False
    assert text == "function sk-abc()"


# ---------------------------------------------------------------------------
# scrub_event — per-event-type tests
# ---------------------------------------------------------------------------


def test_scrub_event_text_delta_clean():
    ev = TextDelta(delta="just normal text")
    out, masked = scrub_event(ev)
    assert masked is False
    assert isinstance(out, TextDelta)
    assert out.delta == "just normal text"


def test_scrub_event_text_delta_with_secret():
    ev = TextDelta(delta="here is sk-proj-abcDEF1234567890ABCDEF1234567890ABCDEF1234")
    out, masked = scrub_event(ev)
    assert masked is True
    assert isinstance(out, TextDelta)
    assert "[REDACTED:openai_key]" in out.delta


def test_scrub_event_user_prompt_with_secret():
    ev = UserPrompt(text="my key is sk-ant-api03-" + "A" * 50)
    out, masked = scrub_event(ev)
    assert masked is True
    assert isinstance(out, UserPrompt)
    assert "[REDACTED:anthropic_key]" in out.text


def test_scrub_event_tool_use_input_nested():
    """Tool input is a dict — scrubber recurses into values."""
    ev = ToolUse(
        tool="Bash",
        input={
            "command": "echo sk-ant-api03-" + "B" * 50,
            "description": "leak the key",
        },
    )
    out, masked = scrub_event(ev)
    assert masked is True
    assert isinstance(out, ToolUse)
    assert "[REDACTED:" in out.input["command"]
    # untouched field stays as-is
    assert out.input["description"] == "leak the key"


def test_scrub_event_tool_result_summary():
    ev = ToolResult(
        tool="Bash",
        ok=True,
        summary="cat ~/.env: API_KEY=sk-proj-abcDEF1234567890ABCDEF1234567890ABCDABCD",
    )
    out, masked = scrub_event(ev)
    assert masked is True
    assert "[REDACTED:openai_key]" in out.summary


def test_scrub_event_unsupported_type_passes_through_unchanged():
    """StreamEnd has nothing to scrub — and shouldn't be flagged as masked."""
    ev = StreamEnd(reason="stop", tokens_in=5, tokens_out=10, cost_usd=0.01)
    out, masked = scrub_event(ev)
    assert masked is False
    assert out is ev  # identity preserved


def test_scrub_event_error_event_passes_through():
    """ErrorEvent.message could carry secrets in theory but we don't scrub
    it currently — the error path is rare and we'd rather see the full text
    when debugging. Verify the contract."""
    ev = ErrorEvent(message="boom: sk-proj-leak1234567890ABCDEF12345678901234567890")
    out, masked = scrub_event(ev)
    assert masked is False
    assert out is ev
