"""Tests for bot.handlers.approval_callback — inline button click handler.

We mock the CallbackQuery + From-user + Message, and verify:
  * Correct callback_data parses to (request_id, decision)
  * router.post_decision is called with right args
  * call.answer is invoked exactly once (Telegram requirement)
  * Invalid prefix → no decision posted
  * Invalid decision → answered with error
  * Original message is edited to show decision label
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from bot.handlers.approval_callback import on_approval_click
from bot.services.approval_handler import (
    APPROVAL_CB_PREFIX,
    set_shared_router,
)


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


@dataclass
class _FakeUser:
    id: int = 7777
    full_name: str = "CEO"


@dataclass
class _FakeMessage:
    text: str = "🔒 Claude wants to use *Bash*"
    chat_id: int = 12345
    edits: list[tuple[str, Any, str | None]] = field(default_factory=list)

    async def edit_text(self, text, reply_markup=None, parse_mode=None):
        self.edits.append((text, reply_markup, parse_mode))


@dataclass
class _FakeCallbackQuery:
    data: str
    from_user: _FakeUser = field(default_factory=_FakeUser)
    message: _FakeMessage | None = field(default_factory=_FakeMessage)
    answers: list[tuple[str | None, bool]] = field(default_factory=list)

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


class _RecordingRouter:
    """Stand-in for ApprovalRouter — only post_decision is called."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.next_return: Any = None

    async def post_decision(
        self, *, request_id: str, decision_str: str, actor_chat_id: int
    ):
        self.calls.append(
            {
                "request_id": request_id,
                "decision_str": decision_str,
                "actor_chat_id": actor_chat_id,
            }
        )
        return self.next_return


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allow_click_posts_decision_and_answers_callback():
    router = _RecordingRouter()
    set_shared_router(router)  # type: ignore[arg-type]

    call = _FakeCallbackQuery(data=f"{APPROVAL_CB_PREFIX}01ABC:allow")
    await on_approval_click(call)

    assert len(router.calls) == 1
    assert router.calls[0] == {
        "request_id": "01ABC",
        "decision_str": "allow",
        "actor_chat_id": 7777,
    }
    assert len(call.answers) == 1
    assert "Allowed" in (call.answers[0][0] or "")


@pytest.mark.asyncio
async def test_deny_click_posts_decision_with_correct_label():
    router = _RecordingRouter()
    set_shared_router(router)  # type: ignore[arg-type]

    call = _FakeCallbackQuery(data=f"{APPROVAL_CB_PREFIX}01ABC:deny")
    await on_approval_click(call)

    assert router.calls[0]["decision_str"] == "deny"
    assert "Denied" in (call.answers[0][0] or "")


@pytest.mark.asyncio
async def test_always_allow_click_uses_correct_label():
    router = _RecordingRouter()
    set_shared_router(router)  # type: ignore[arg-type]

    call = _FakeCallbackQuery(data=f"{APPROVAL_CB_PREFIX}01XYZ:always_allow")
    await on_approval_click(call)

    assert router.calls[0]["decision_str"] == "always_allow"
    label = call.answers[0][0] or ""
    assert "always" in label.lower() or "🔓" in label


@pytest.mark.asyncio
async def test_always_deny_click_uses_correct_label():
    router = _RecordingRouter()
    set_shared_router(router)  # type: ignore[arg-type]

    call = _FakeCallbackQuery(data=f"{APPROVAL_CB_PREFIX}01XYZ:always_deny")
    await on_approval_click(call)

    assert router.calls[0]["decision_str"] == "always_deny"
    label = call.answers[0][0] or ""
    assert "always" in label.lower() or "🔒" in label


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_appr_prefix_is_ignored():
    """The callback handler should be filtered out by aiogram filters, but
    defensive: if it does fire, do nothing and just answer."""
    router = _RecordingRouter()
    set_shared_router(router)  # type: ignore[arg-type]

    call = _FakeCallbackQuery(data="cancel:abc")
    await on_approval_click(call)

    # No decision posted (wrong prefix)
    assert router.calls == []
    # But we DID answer (clears the Telegram spinner)
    assert len(call.answers) == 1


@pytest.mark.asyncio
async def test_invalid_format_callback_answers_with_warning():
    router = _RecordingRouter()
    set_shared_router(router)  # type: ignore[arg-type]

    call = _FakeCallbackQuery(data=f"{APPROVAL_CB_PREFIX}only-id-no-colon")
    await on_approval_click(call)

    assert router.calls == []
    assert "invalid" in (call.answers[0][0] or "").lower()


@pytest.mark.asyncio
async def test_unknown_decision_answers_with_warning():
    router = _RecordingRouter()
    set_shared_router(router)  # type: ignore[arg-type]

    call = _FakeCallbackQuery(data=f"{APPROVAL_CB_PREFIX}01ABC:fly_to_moon")
    await on_approval_click(call)

    assert router.calls == []
    assert len(call.answers) == 1


@pytest.mark.asyncio
async def test_no_shared_router_returns_alert():
    set_shared_router(None)

    call = _FakeCallbackQuery(data=f"{APPROVAL_CB_PREFIX}01ABC:allow")
    await on_approval_click(call)

    assert call.answers[0][1] is True  # show_alert
    assert "config" in (call.answers[0][0] or "").lower()


# ---------------------------------------------------------------------------
# Message edit on successful click
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_click_edits_message_text():
    router = _RecordingRouter()
    # Have post_decision return a non-None live so the handler tries to edit
    from bot.services.approval_handler import _LiveRequest

    router.next_return = _LiveRequest(
        request_id="01EDIT",
        chat_id=12345,
        message_id=1,
        deadline_unix=0,
        tool_name="Edit",
        summary="x.py",
        description="",
    )
    set_shared_router(router)  # type: ignore[arg-type]

    call = _FakeCallbackQuery(data=f"{APPROVAL_CB_PREFIX}01EDIT:allow")
    await on_approval_click(call)

    assert call.message is not None
    assert len(call.message.edits) == 1
    new_text, reply_markup, parse_mode = call.message.edits[0]
    assert "Allowed" in new_text
    assert "CEO" in new_text
    assert reply_markup is None  # keyboard cleared
