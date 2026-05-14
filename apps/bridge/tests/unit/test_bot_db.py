"""Tests for bot.db — sqlite single-tenant schema."""
import pytest
import pytest_asyncio

from bot.db import (
    init_db,
    authorize_chat,
    is_authorized,
    revoke_chat,
    touch_last_seen,
    set_active_session,
    get_active_session,
    audit,
)


@pytest_asyncio.fixture
async def db(tmp_path):
    path = str(tmp_path / "test.db")
    await init_db(path)
    return path


async def test_init_creates_all_tables(db):
    import aiosqlite

    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = {row[0] for row in await cur.fetchall()}
    assert "authorized_chats" in names
    assert "active_sessions" in names
    assert "audit_log" in names


async def test_authorize_and_check(db):
    assert await is_authorized(db, 42) is False
    await authorize_chat(db, 42, nickname="CEO")
    assert await is_authorized(db, 42) is True


async def test_authorize_is_idempotent_and_updates_last_seen(db):
    """Re-authorizing same chat should update last_seen, not duplicate."""
    await authorize_chat(db, 42, nickname="A")
    await authorize_chat(db, 42, nickname=None)  # no nickname change
    import aiosqlite

    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute("SELECT count(*) FROM authorized_chats WHERE chat_id=42")
        cnt = (await cur.fetchone())[0]
    assert cnt == 1


async def test_revoke_chat(db):
    await authorize_chat(db, 42)
    await revoke_chat(db, 42)
    assert await is_authorized(db, 42) is False


async def test_active_session_set_and_get(db):
    await authorize_chat(db, 42)
    assert await get_active_session(db, 42) is None
    await set_active_session(db, 42, "uuid-1", "C--Users-x-repo")
    sess = await get_active_session(db, 42)
    assert sess is not None
    assert sess["session_id"] == "uuid-1"
    assert sess["project_path"] == "C--Users-x-repo"


async def test_active_session_overwrites_on_switch(db):
    await authorize_chat(db, 42)
    await set_active_session(db, 42, "uuid-1", "proj-A")
    await set_active_session(db, 42, "uuid-2", "proj-B")
    sess = await get_active_session(db, 42)
    assert sess["session_id"] == "uuid-2"


async def test_audit_log_append(db):
    await audit(db, chat_id=42, event="auth_success", daemon_url="http://x")
    await audit(db, chat_id=None, event="bot_started")
    import aiosqlite

    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute(
            "SELECT chat_id, event, metadata_json FROM audit_log ORDER BY id"
        )
        rows = await cur.fetchall()
    assert len(rows) == 2
    assert rows[0][1] == "auth_success"
    assert "http://x" in rows[0][2]
    assert rows[1][0] is None  # global event
