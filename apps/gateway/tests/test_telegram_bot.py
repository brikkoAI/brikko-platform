"""Tests for the Telegram bot integration (Sprint 4 Поток M).

Coverage:

* `issue_link_token` / `consume_link_token` — happy path + idempotency
  (consume removes; second consume returns None).
* `handle_update` dispatches /start, /balance, /usage, /keys, /link,
  unknown command.
* /link with valid token sets ``users.telegram_chat_id``.
* /link with expired/missing token returns user-friendly error.
* /balance for non-linked chat_id returns "not linked" hint.

The Bot API (sendMessage) is NOT exercised by HTTP — handle_update returns
a TelegramReply DTO; the webhook adapter is tested separately.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from voltari_gateway.db.models import User
from voltari_gateway.integrations.telegram_bot import (
    consume_link_token,
    handle_update,
    issue_link_token,
)


@pytest.mark.asyncio
async def test_issue_and_consume_link_token(redis_client) -> None:
    issued = await issue_link_token(redis_client, str(uuid.uuid4()))
    assert issued is not None
    # First consume returns the user_id.
    user_id = await consume_link_token(redis_client, issued.token)
    assert user_id == issued.user_id
    # Second consume returns None (token already deleted).
    assert await consume_link_token(redis_client, issued.token) is None


@pytest.mark.asyncio
async def test_issue_token_redis_unavailable_returns_none() -> None:
    assert await issue_link_token(None, "user-123") is None


@pytest.mark.asyncio
async def test_consume_unknown_token_returns_none(redis_client) -> None:
    assert await consume_link_token(redis_client, "definitely-not-a-token") is None


@pytest.mark.asyncio
async def test_handle_update_start(db, redis_client) -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 100, "type": "private"},
            "text": "/start",
        },
    }
    reply = await handle_update(update=update, db=db, redis=redis_client, bot=None)
    assert reply is not None
    assert "BrikkoAI_bot" in reply.text


@pytest.mark.asyncio
async def test_handle_update_start_with_deeplink_payload(db, redis_client, api_key_fixture) -> None:
    """``/start <token>`` from a t.me deep-link must auto-link the chat
    exactly like ``/link <token>`` — no extra step required from the user."""
    issued = await issue_link_token(redis_client, str(api_key_fixture.user.id))
    assert issued is not None
    chat_id = 555_222_111
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "text": f"/start {issued.token}",
        },
    }
    reply = await handle_update(update=update, db=db, redis=redis_client, bot=None)
    assert reply is not None
    assert "Привязал" in reply.text or "✅" in reply.text

    refreshed = (
        await db.execute(select(User).where(User.id == api_key_fixture.user.id))
    ).scalar_one()
    assert refreshed.telegram_chat_id == chat_id


@pytest.mark.asyncio
async def test_handle_update_unknown_command(db, redis_client) -> None:
    update = {
        "update_id": 2,
        "message": {
            "message_id": 1,
            "chat": {"id": 100, "type": "private"},
            "text": "/quack",
        },
    }
    reply = await handle_update(update=update, db=db, redis=redis_client, bot=None)
    assert reply is not None
    assert "Неизвестная" in reply.text


@pytest.mark.asyncio
async def test_handle_update_link_happy_path(db, redis_client, api_key_fixture) -> None:
    """/link with a freshly issued token sets users.telegram_chat_id."""
    issued = await issue_link_token(redis_client, str(api_key_fixture.user.id))
    assert issued is not None
    chat_id = 999_111_777
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "text": f"/link {issued.token}",
        },
    }
    reply = await handle_update(update=update, db=db, redis=redis_client, bot=None)
    assert reply is not None
    assert "✅" in reply.text or "Привязал" in reply.text

    # Reload user and verify column.
    refreshed = (
        await db.execute(select(User).where(User.id == api_key_fixture.user.id))
    ).scalar_one()
    assert refreshed.telegram_chat_id == chat_id


@pytest.mark.asyncio
async def test_handle_update_link_with_expired_token(db, redis_client) -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 100, "type": "private"},
            "text": "/link totally-fake-token-string",
        },
    }
    reply = await handle_update(update=update, db=db, redis=redis_client, bot=None)
    assert reply is not None
    assert "истёк" in reply.text or "невалид" in reply.text


@pytest.mark.asyncio
async def test_handle_update_balance_not_linked(db, redis_client) -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 555_555, "type": "private"},
            "text": "/balance",
        },
    }
    reply = await handle_update(update=update, db=db, redis=redis_client, bot=None)
    assert reply is not None
    assert "не привязан" in reply.text


@pytest.mark.asyncio
async def test_handle_update_balance_linked(db, redis_client, api_key_fixture) -> None:
    """After linking, /balance returns balance in rubles."""
    user = api_key_fixture.user
    user.telegram_chat_id = 12345
    db.add(user)
    await db.commit()

    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 12345, "type": "private"},
            "text": "/balance",
        },
    }
    reply = await handle_update(update=update, db=db, redis=redis_client, bot=None)
    assert reply is not None
    # api_key_fixture seeds 100_000 kopecks = 1000 rub
    assert "1 000.00" in reply.text or "1000.00" in reply.text


@pytest.mark.asyncio
async def test_handle_update_usage_linked_zero(db, redis_client, api_key_fixture) -> None:
    """Linked user with no UsageEvent rows — should report 0 ₽."""
    user = api_key_fixture.user
    user.telegram_chat_id = 12346
    db.add(user)
    await db.commit()

    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 12346, "type": "private"},
            "text": "/usage",
        },
    }
    reply = await handle_update(update=update, db=db, redis=redis_client, bot=None)
    assert reply is not None
    assert "0.00" in reply.text
    assert "запросов" in reply.text


@pytest.mark.asyncio
async def test_handle_update_keys_linked(db, redis_client, api_key_fixture) -> None:
    """Linked user with one active key — should appear in /keys."""
    user = api_key_fixture.user
    user.telegram_chat_id = 12347
    db.add(user)
    await db.commit()

    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 12347, "type": "private"},
            "text": "/keys",
        },
    }
    reply = await handle_update(update=update, db=db, redis=redis_client, bot=None)
    assert reply is not None
    assert api_key_fixture.api_key.key_prefix in reply.text


@pytest.mark.asyncio
async def test_handle_update_topup_with_arg(db, redis_client) -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 100, "type": "private"},
            "text": "/topup 5000",
        },
    }
    reply = await handle_update(update=update, db=db, redis=redis_client, bot=None)
    assert reply is not None
    assert "5 000" in reply.text or "5000" in reply.text
    assert "brikko.ru/app/billing" in reply.text


@pytest.mark.asyncio
async def test_handle_update_ignores_non_message_update(db, redis_client) -> None:
    """Updates without a message (callback_query, edited_message, etc.) are no-ops."""
    update = {"update_id": 1, "edited_message": {}}
    assert await handle_update(update=update, db=db, redis=redis_client, bot=None) is None


@pytest.mark.asyncio
async def test_handle_update_ignores_non_text_messages(db, redis_client) -> None:
    """Photo/sticker/voice messages — no-op."""
    update = {
        "update_id": 1,
        "message": {"message_id": 1, "chat": {"id": 100}, "photo": [{}]},
    }
    assert await handle_update(update=update, db=db, redis=redis_client, bot=None) is None
