"""Async SQLAlchemy engine + session factory.

We expose ``get_db()`` as a FastAPI dependency, plus ``init_engine()`` /
``dispose_engine()`` for app lifecycle. Engine is created lazily so test code
can override ``DATABASE_URL`` before first use.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from voltari_gateway.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str | None = None) -> AsyncEngine:
    """Create the global engine and session factory. Idempotent."""
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    url = database_url or get_settings().database_url
    kwargs: dict[str, Any] = {
        "echo": False,
        "pool_pre_ping": True,
    }
    # SQLite (used in tests) does not understand pg pool parameters
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20

    _engine = create_async_engine(url, **kwargs)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


async def dispose_engine() -> None:
    """Close all pooled connections. Call on app shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields one session per request."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
