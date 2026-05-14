"""SQLite layer for the bot — single-tenant simple schema.

Tables:
  authorized_chats (chat_id, nickname, created_at, last_seen)
  active_sessions  (chat_id PK, session_id, project_path, set_at)
  audit_log        (id, chat_id, event, metadata_json, ts)
"""
from __future__ import annotations

import json
import time
from typing import Optional

import aiosqlite


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS authorized_chats (
    chat_id    INTEGER PRIMARY KEY,
    nickname   TEXT,
    created_at INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS active_sessions (
    chat_id      INTEGER PRIMARY KEY,
    session_id   TEXT NOT NULL,
    project_path TEXT NOT NULL,
    set_at       INTEGER NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES authorized_chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER,
    event         TEXT NOT NULL,
    metadata_json TEXT,
    ts            INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_log_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS audit_log_chat ON audit_log(chat_id);
"""


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()


# === authorized_chats ===

async def authorize_chat(
    db_path: str, chat_id: int, nickname: str | None = None
) -> None:
    """Add chat to whitelist (or update last_seen if already there)."""
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO authorized_chats (chat_id, nickname, created_at, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                last_seen = excluded.last_seen,
                nickname = COALESCE(excluded.nickname, nickname)
            """,
            (chat_id, nickname, now, now),
        )
        await db.commit()


async def is_authorized(db_path: str, chat_id: int) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT 1 FROM authorized_chats WHERE chat_id = ?", (chat_id,)
        )
        return await cur.fetchone() is not None


async def revoke_chat(db_path: str, chat_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM authorized_chats WHERE chat_id = ?", (chat_id,)
        )
        await db.commit()


async def touch_last_seen(db_path: str, chat_id: int) -> None:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE authorized_chats SET last_seen = ? WHERE chat_id = ?",
            (now, chat_id),
        )
        await db.commit()


# === active_sessions ===

async def set_active_session(
    db_path: str, chat_id: int, session_id: str, project_path: str
) -> None:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO active_sessions (chat_id, session_id, project_path, set_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                session_id = excluded.session_id,
                project_path = excluded.project_path,
                set_at = excluded.set_at
            """,
            (chat_id, session_id, project_path, now),
        )
        await db.commit()


async def get_active_session(db_path: str, chat_id: int) -> Optional[dict]:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT session_id, project_path, set_at FROM active_sessions WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cur.fetchone()
    if not row:
        return None
    return {"session_id": row[0], "project_path": row[1], "set_at": row[2]}


# === audit_log ===

async def audit(
    db_path: str, chat_id: int | None, event: str, **metadata
) -> None:
    """Append-only audit row. Best-effort; failure here doesn't crash bot."""
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO audit_log (chat_id, event, metadata_json, ts) VALUES (?, ?, ?, ?)",
                (chat_id, event, json.dumps(metadata, ensure_ascii=False), int(time.time())),
            )
            await db.commit()
    except Exception:
        pass


async def recent_audit(
    db_path: str, chat_id: int, limit: int = 20
) -> list[dict]:
    """Return last ``limit`` audit rows for ``chat_id``, newest first."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT event, metadata_json, ts FROM audit_log "
            "WHERE chat_id = ? ORDER BY ts DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = await cur.fetchall()
    out: list[dict] = []
    for event, meta_str, ts in rows:
        try:
            meta = json.loads(meta_str) if meta_str else {}
        except json.JSONDecodeError:
            meta = {}
        out.append({"event": event, "meta": meta, "ts": ts})
    return out
