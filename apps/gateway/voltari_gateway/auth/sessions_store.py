"""Helpers for the Postgres mirror of the Redis refresh-JTI whitelist.

Postgres ``sessions`` is a UI-side **view** of the same data Redis owns.
Truth = Redis (whitelist controls authorisation). Postgres = list/revoke
endpoints + audit references.

Why mirror at all?

* The dashboard wants device / browser / IP / last_seen alongside the
  bare JTI. Storing that in Redis would either bloat each whitelist key
  or require a parallel hash that has to stay in sync.
* Audit log references ``session.id`` (UUID) — leaking a JTI into log
  rows is a key-material disclosure. Stable session.id solves that.

We keep both stores in lockstep:

* Login / refresh → ``record_session`` (insert) + Redis whitelist set.
* Logout / revoke → ``mark_session_revoked`` (set ``revoked_at``) +
  Redis whitelist delete.
* Refresh rotation → tombstone old ``refresh_jti`` row, insert new one
  (cleaner than mutating in place — keeps the "history of devices"
  story intact for audit).

If Postgres is unreachable but Redis is fine, login still succeeds —
the row insert is best-effort. The whitelist is what matters for
authorisation.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.config import get_settings
from voltari_gateway.db.models import Session as SessionRow
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return None


def _user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    return ua[:512] if ua else None


# Tiny UA → device label heuristic. Good enough for the dashboard list.
# Real geo / device parsing — V2 with ua-parser. Keeping this dependency-free.
_DEVICE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Windows NT 10", re.I), "Windows 10/11"),
    (re.compile(r"Mac OS X", re.I), "macOS"),
    (re.compile(r"Linux", re.I), "Linux"),
    (re.compile(r"iPhone", re.I), "iPhone"),
    (re.compile(r"iPad", re.I), "iPad"),
    (re.compile(r"Android", re.I), "Android"),
]
_BROWSER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Edg/(\d+)", re.I), "Edge {0}"),
    (re.compile(r"Chrome/(\d+)", re.I), "Chrome {0}"),
    (re.compile(r"Safari/", re.I), "Safari"),
    (re.compile(r"Firefox/(\d+)", re.I), "Firefox {0}"),
]


def _device_label(user_agent: str | None) -> str | None:
    """Best-effort device + browser label for the UI."""
    if not user_agent:
        return None
    device = "Unknown"
    for pat, name in _DEVICE_PATTERNS:
        if pat.search(user_agent):
            device = name
            break
    browser = "Unknown"
    for pat, fmt in _BROWSER_PATTERNS:
        m = pat.search(user_agent)
        if m:
            browser = fmt.format(*m.groups()) if m.groups() else fmt
            break
    label = f"{browser} on {device}"
    return label[:128]


async def record_session(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    refresh_jti: str,
    expires_at: datetime,
    request: Request | None = None,
) -> SessionRow:
    """Insert a new Postgres session row mirroring the Redis whitelist entry.

    Idempotent on (user_id, refresh_jti). Caller is expected to commit
    the surrounding session.
    """
    ip = _client_ip(request)
    ua = _user_agent(request)
    label = _device_label(ua)

    row = SessionRow(
        user_id=user_id,
        refresh_jti=refresh_jti,
        ip_address=ip,
        user_agent=ua,
        device_label=label,
        expires_at=expires_at,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        # Concurrent insert with the same JTI (shouldn't happen — JTI is
        # 16 random bytes — but the unique index is defence in depth).
        await db.rollback()
        existing = (
            await db.execute(
                select(SessionRow).where(
                    SessionRow.user_id == user_id,
                    SessionRow.refresh_jti == refresh_jti,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return existing
    return row


async def touch_session(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    refresh_jti: str,
    request: Request | None = None,
) -> SessionRow | None:
    """Update last_seen_at on an existing row. None if not found."""
    now = datetime.now(UTC)
    ip = _client_ip(request)
    stmt = (
        update(SessionRow)
        .where(
            SessionRow.user_id == user_id,
            SessionRow.refresh_jti == refresh_jti,
            SessionRow.revoked_at.is_(None),
        )
        .values(last_seen_at=now, ip_address=ip)
        .returning(SessionRow)
    )
    try:
        result = await db.execute(stmt)
    except Exception as exc:
        log.debug("touch_session_failed", error=str(exc))
        return None
    return result.scalar_one_or_none()


async def mark_session_revoked(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    refresh_jti: str,
) -> None:
    """Set ``revoked_at`` on the Postgres row. Idempotent."""
    now = datetime.now(UTC)
    stmt = (
        update(SessionRow)
        .where(
            SessionRow.user_id == user_id,
            SessionRow.refresh_jti == refresh_jti,
            SessionRow.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await db.execute(stmt)


async def revoke_session_by_id(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
) -> SessionRow | None:
    """Set ``revoked_at`` for a session owned by ``user_id``. None if not found.

    Returns the (already-revoked or freshly-revoked) row so the caller
    can also drop the JTI from Redis.
    """
    row = (
        await db.execute(
            select(SessionRow).where(
                SessionRow.id == session_id,
                SessionRow.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
    return row


async def revoke_all_other_sessions(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    keep_jti: str | None = None,
) -> list[str]:
    """Mark every active row revoked except the one with ``keep_jti``.

    Returns the list of revoked JTIs so the caller can also tear them
    down in Redis.
    """
    now = datetime.now(UTC)
    stmt = select(SessionRow).where(
        SessionRow.user_id == user_id,
        SessionRow.revoked_at.is_(None),
    )
    if keep_jti is not None:
        stmt = stmt.where(SessionRow.refresh_jti != keep_jti)
    rows = (await db.execute(stmt)).scalars().all()
    revoked: list[str] = []
    for row in rows:
        row.revoked_at = now
        revoked.append(row.refresh_jti)
    return revoked


def session_ttl(refresh_expires_at: datetime) -> int:
    """Compute the cookie max_age — used by both the cookie layer and the
    sessions store at the same time. Module exposes it so the truncation
    rules (1s minimum) are applied identically.
    """
    delta = (refresh_expires_at - datetime.now(refresh_expires_at.tzinfo)).total_seconds()
    return max(1, int(delta))


def default_refresh_expires_at() -> datetime:
    """Convenience: ``now + JWT_REFRESH_TTL_DAYS`` for places that don't have
    the live refresh-token expiry handy (e.g. test factories).
    """
    settings = get_settings()
    return datetime.now(UTC) + timedelta(days=settings.jwt_refresh_ttl_days)


__all__ = [
    "default_refresh_expires_at",
    "mark_session_revoked",
    "record_session",
    "revoke_all_other_sessions",
    "revoke_session_by_id",
    "session_ttl",
    "touch_session",
]
