"""Tests for shared.approval_protocol — wire-format schemas."""

from __future__ import annotations

import json

import pytest

from shared.approval_protocol import (
    APPROVAL_REQUEST_CHANNEL,
    YOLO_BANNER_CHANNEL,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    YoloBannerEvent,
    approval_response_key,
)


def test_channel_names_are_namespaced():
    assert APPROVAL_REQUEST_CHANNEL.startswith("bridge:")
    assert YOLO_BANNER_CHANNEL.startswith("bridge:")


def test_response_key_includes_request_id():
    assert approval_response_key("abc-123") == "bridge:approval-response:abc-123"


def test_decision_enum_string_values():
    """Wire format relies on these exact strings — bot/daemon don't drift."""
    assert ApprovalDecision.ALLOW.value == "allow"
    assert ApprovalDecision.DENY.value == "deny"
    assert ApprovalDecision.ALWAYS_ALLOW.value == "always_allow"
    assert ApprovalDecision.ALWAYS_DENY.value == "always_deny"


def test_approval_request_round_trip():
    req = ApprovalRequest(
        request_id="01TEST",
        session_id="s1",
        tg_chat_id=12345,
        tool_name="Bash",
        display_name="Bash",
        description="delete temp",
        tool_input_summary="rm -rf temp",
        tool_input_full={"command": "rm -rf temp", "description": "delete temp"},
        tool_use_id="toolu_abc",
        deadline_unix=1700000000,
        always_allow_offered=False,
    )
    raw = req.model_dump_json()
    restored = ApprovalRequest.model_validate_json(raw)
    assert restored == req


def test_approval_request_defaults():
    """Most fields have sensible defaults to keep daemon code terse."""
    req = ApprovalRequest(
        request_id="r",
        session_id="s",
        tg_chat_id=1,
        tool_name="Edit",
        display_name="Edit",
        tool_input_summary="x.py (Edit)",
        deadline_unix=0,
    )
    assert req.description == ""
    assert req.tool_input_full == {}
    assert req.tool_use_id is None
    assert req.always_allow_offered is True


def test_approval_request_summary_capped_at_400():
    """Pydantic max_length is enforced — protects against oversized messages."""
    with pytest.raises(Exception):
        ApprovalRequest(
            request_id="r",
            session_id="s",
            tg_chat_id=1,
            tool_name="Bash",
            display_name="Bash",
            tool_input_summary="x" * 1000,
            deadline_unix=0,
        )


def test_approval_response_round_trip():
    resp = ApprovalResponse(
        request_id="r",
        decision=ApprovalDecision.ALWAYS_ALLOW,
        actor_chat_id=42,
        decided_at_unix=1700000001,
        message="ok",
    )
    raw = resp.model_dump_json()
    restored = ApprovalResponse.model_validate_json(raw)
    assert restored == resp
    # decision serialises as a plain string (no enum wrapper)
    assert json.loads(raw)["decision"] == "always_allow"


def test_approval_response_decision_accepts_string():
    """Bot serialises decision as plain string — round-trip via JSON works."""
    raw = json.dumps(
        {
            "request_id": "r",
            "decision": "deny",
            "actor_chat_id": 1,
            "decided_at_unix": 0,
        }
    )
    restored = ApprovalResponse.model_validate_json(raw)
    assert restored.decision == ApprovalDecision.DENY


def test_yolo_banner_round_trip():
    evt = YoloBannerEvent(
        tg_chat_id=99,
        reason="redis_connection_refused",
        emitted_at_unix=1700000099,
    )
    raw = evt.model_dump_json()
    assert YoloBannerEvent.model_validate_json(raw) == evt
