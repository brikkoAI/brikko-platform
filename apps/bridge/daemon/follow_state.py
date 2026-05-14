"""Daemon-side registry of active CLI-mirror follows.

One entry per ``(chat_id, session_id)``. Stops/restarts the corresponding
:class:`JsonlTailer` task in response to bot-sent FollowControl messages.

This is **in-memory only** — restart of the daemon drops all follows, and
the bot has to re-register. That's fine for solo use; multi-tenant would
persist follows in SQLite.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from daemon.jsonl_tailer import JsonlTailer, Publisher, TailerStatus

log = logging.getLogger(__name__)


@dataclass
class _Entry:
    """One follow — tailer task + its handle for cancellation."""

    chat_id: int
    session_id: str
    task: asyncio.Task[None]
    tailer: JsonlTailer


SessionLocator = Callable[[str], Path | None]
"""Callable that maps ``session_id`` to the jsonl path on disk, or None
if the session isn't found. Production injects
``daemon.session_discovery``-backed resolver; tests inject a fake."""


@dataclass
class FollowRegistry:
    """Owns the tailer asyncio tasks; (chat, session) is the key.

    Concurrent calls into ``register`` / ``unregister`` for the same key
    are serialised by a per-registry lock — the operations are short
    (start/cancel a task) so coarse-grained locking is fine.
    """

    publisher: Publisher
    locate: SessionLocator
    poll_interval_s: float = 0.2
    _entries: dict[tuple[int, str], _Entry] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _on_register: Callable[[int, str], Awaitable[Any]] | None = None
    _on_unregister: Callable[[int, str, str], Awaitable[Any]] | None = None

    # ------------------------------------------------------------------
    # Notification hooks (bot-bound)
    # ------------------------------------------------------------------

    def set_hooks(
        self,
        *,
        on_register: Callable[[int, str], Awaitable[Any]] | None = None,
        on_unregister: Callable[[int, str, str], Awaitable[Any]] | None = None,
    ) -> None:
        """Wire optional callbacks fired on successful (de)registration.

        ``on_register(chat_id, session_id)``
        ``on_unregister(chat_id, session_id, reason)``

        Used by FollowService to send a FollowNotice over Redis.
        """
        self._on_register = on_register
        self._on_unregister = on_unregister

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def register(self, *, chat_id: int, session_id: str) -> tuple[bool, str]:
        """Start (or no-op confirm) a tailer for this pair.

        Returns ``(ok, message)``. ``ok=False`` typically means the session
        isn't on disk — caller should surface the message to the user.
        """
        async with self._lock:
            key = (chat_id, session_id)
            if key in self._entries and not self._entries[key].task.done():
                return True, f"already following {session_id[:8]}"

            path = self.locate(session_id)
            if path is None:
                return False, (
                    f"session {session_id[:8]}… not found on this PC"
                )
            if not path.exists():
                return False, (
                    f"session jsonl missing: {path.name}"
                )

            tailer = JsonlTailer(
                path=path,
                chat_id=chat_id,
                session_id=session_id,
                publisher=self.publisher,
                poll_interval_s=self.poll_interval_s,
                start_from_end=True,
            )
            task = asyncio.create_task(
                tailer.run(),
                name=f"jsonl-tailer-{chat_id}-{session_id[:8]}",
            )

            # When the tailer task finishes (e.g. cli_session_ended fires
            # StreamEnd → loop drops to ``ended=True``), tell the bot once.
            # We bind chat_id + session_id via closures (default args) to
            # avoid the late-binding gotcha when the registry has many entries.
            def _done_cb(
                t: asyncio.Task[None],
                cid: int = chat_id,
                sid: str = session_id,
            ) -> None:
                self._on_task_done(t, cid, sid)

            task.add_done_callback(_done_cb)

            self._entries[key] = _Entry(
                chat_id=chat_id,
                session_id=session_id,
                task=task,
                tailer=tailer,
            )

        if self._on_register is not None:
            try:
                await self._on_register(chat_id, session_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("on_register hook failed: %s", exc)

        return True, f"now following {session_id[:8]}"

    async def unregister(
        self, *, chat_id: int, session_id: str, reason: str = "user_request"
    ) -> tuple[bool, str]:
        """Cancel the tailer task for one pair. Idempotent."""
        async with self._lock:
            key = (chat_id, session_id)
            entry = self._entries.pop(key, None)

        if entry is None:
            return False, f"not currently following {session_id[:8]}"

        entry.tailer.stop()
        try:
            await asyncio.wait_for(entry.task, timeout=2.0)
        except asyncio.TimeoutError:
            entry.task.cancel()
        except asyncio.CancelledError:
            pass

        if self._on_unregister is not None:
            try:
                await self._on_unregister(chat_id, session_id, reason)
            except Exception as exc:  # noqa: BLE001
                log.warning("on_unregister hook failed: %s", exc)

        return True, f"stopped following {session_id[:8]}"

    async def unregister_all(self, *, chat_id: int) -> int:
        """Cancel all tailers belonging to ``chat_id``. Returns count cancelled."""
        async with self._lock:
            keys = [k for k in self._entries if k[0] == chat_id]
            entries = [self._entries.pop(k) for k in keys]

        for e in entries:
            e.tailer.stop()
            try:
                await asyncio.wait_for(e.task, timeout=2.0)
            except asyncio.TimeoutError:
                e.task.cancel()
            except asyncio.CancelledError:
                pass

            if self._on_unregister is not None:
                try:
                    await self._on_unregister(
                        chat_id, e.session_id, "unregister_all"
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("on_unregister hook failed: %s", exc)

        return len(entries)

    async def close_all(self) -> None:
        """Hard-stop everything. Called from lifespan shutdown."""
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()

        for e in entries:
            e.tailer.stop()
        # Best-effort drain
        for e in entries:
            try:
                await asyncio.wait_for(e.task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                e.task.cancel()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def snapshot(self, *, chat_id: int | None = None) -> list[TailerStatus]:
        """Return active tailer statuses, optionally filtered to one chat.

        Caller must NOT mutate the returned objects (they're live refs).
        """
        out: list[TailerStatus] = []
        for (cid, _), entry in self._entries.items():
            if chat_id is not None and cid != chat_id:
                continue
            out.append(entry.tailer.status)
        return out

    def is_following(self, chat_id: int, session_id: str) -> bool:
        return (chat_id, session_id) in self._entries

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_task_done(
        self, task: asyncio.Task[None], chat_id: int, session_id: str
    ) -> None:
        """Tailer's run() returned by itself (StreamEnd / file gone)."""
        key = (chat_id, session_id)
        if key not in self._entries:
            return  # already unregistered explicitly
        # Schedule the cleanup on the loop (we're in callback context).
        asyncio.get_event_loop().create_task(
            self._cleanup_done(chat_id, session_id, task)
        )

    async def _cleanup_done(
        self,
        chat_id: int,
        session_id: str,
        task: asyncio.Task[None],
    ) -> None:
        async with self._lock:
            self._entries.pop((chat_id, session_id), None)
        if self._on_unregister is not None:
            reason = "session_ended"
            if task.cancelled():
                reason = "cancelled"
            elif task.exception() is not None:
                reason = f"error: {task.exception()}"
            try:
                await self._on_unregister(chat_id, session_id, reason)
            except Exception as exc:  # noqa: BLE001
                log.warning("on_unregister hook failed: %s", exc)


# ---------------------------------------------------------------------------
# Default session locator — wraps daemon.session_discovery
# ---------------------------------------------------------------------------


def make_default_locator(*, sessions_dir: Path | None = None) -> SessionLocator:
    """Return a callable that maps ``session_id`` → jsonl path.

    Walks ``~/.claude/projects/*/*.jsonl`` and returns the first file whose
    stem matches. ``session_id`` is a UUID so cross-project collisions are
    astronomically unlikely.

    ``sessions_dir`` is the optional override (used by tests).
    """
    base = sessions_dir or (
        Path(
            os.environ.get("USERPROFILE")
            or os.environ.get("HOME")
            or "~"
        ).expanduser()
        / ".claude"
        / "projects"
    )

    def locate(session_id: str) -> Path | None:
        if not session_id:
            return None
        if not base.exists() or not base.is_dir():
            return None
        for project in base.iterdir():
            if not project.is_dir():
                continue
            candidate = project / f"{session_id}.jsonl"
            if candidate.exists():
                return candidate
        return None

    return locate
