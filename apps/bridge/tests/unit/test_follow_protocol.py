"""Tests for shared.follow_protocol — CLI-mirror wire schemas."""

from __future__ import annotations

from shared.follow_protocol import (
    FOLLOW_CONTROL_CHANNEL,
    CliMirrorEvent,
    FollowAction,
    FollowControl,
    FollowNotice,
    cli_mirror_channel,
)


def test_control_channel_is_namespaced():
    assert FOLLOW_CONTROL_CHANNEL.startswith("bridge:")


def test_cli_mirror_channel_includes_chat_id():
    assert cli_mirror_channel(12345) == "bridge:cli-mirror:12345"
    # negative chat ids (groups) work too
    assert cli_mirror_channel(-100) == "bridge:cli-mirror:-100"


def test_follow_action_string_values_stable():
    """Wire format depends on these — don't change without daemon coordination."""
    assert FollowAction.REGISTER.value == "register"
    assert FollowAction.UNREGISTER.value == "unregister"
    assert FollowAction.UNREGISTER_ALL.value == "unregister_all"


def test_follow_control_register_roundtrip():
    ctrl = FollowControl(
        action=FollowAction.REGISTER,
        tg_chat_id=42,
        session_id="abc-123",
    )
    raw = ctrl.model_dump_json()
    parsed = FollowControl.model_validate_json(raw)
    assert parsed.action == FollowAction.REGISTER
    assert parsed.tg_chat_id == 42
    assert parsed.session_id == "abc-123"


def test_follow_control_unregister_all_session_id_optional():
    ctrl = FollowControl(
        action=FollowAction.UNREGISTER_ALL,
        tg_chat_id=42,
    )
    assert ctrl.session_id == ""


def test_cli_mirror_event_roundtrip():
    evt = CliMirrorEvent(
        chat_id=42,
        session_id="abc",
        source="cli",
        seq=7,
        event={"type": "text", "delta": "hello"},
        masked=False,
    )
    raw = evt.model_dump_json()
    parsed = CliMirrorEvent.model_validate_json(raw)
    assert parsed.event["type"] == "text"
    assert parsed.event["delta"] == "hello"
    assert parsed.seq == 7
    assert parsed.source == "cli"


def test_cli_mirror_event_source_pattern_rejects_unknown():
    """source must be 'cli' or 'tg' — never anything else."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CliMirrorEvent(
            chat_id=1,
            session_id="x",
            source="unknown",
            event={},
        )


def test_follow_notice_roundtrip():
    notice = FollowNotice(
        chat_id=1,
        session_id="sid",
        kind="registered",
        message="",
    )
    parsed = FollowNotice.model_validate_json(notice.model_dump_json())
    assert parsed.kind == "registered"
