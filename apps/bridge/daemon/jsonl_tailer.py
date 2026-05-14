"""Tail Claude Code session jsonl files, parse new lines, fan out to Redis.

The CEO runs ``claude`` interactively in a CLI window on his PC. The CLI
appends every event (system init, assistant text, tool_use, tool_result,
result) as a JSON line to::

    ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl

This module follows one such file per registered ``(chat_id, session_id)``
pair, parses each new line via :mod:`daemon.stream_parser`, scrubs known
secrets via :mod:`daemon.pii_filter`, and publishes a ``CliMirrorEvent``
to the per-chat Redis channel for the bot to render.

Design choices
--------------

* **Polling, not inotify.** Windows doesn't have a reliable inotify-like
  API for cross-volume FS notifications, and ``watchdog`` on Windows
  internally uses ``ReadDirectoryChangesW`` which mis-fires under heavy
  load and on network drives. A 200 ms poll loop is simpler, predictable,
  and adds <2 ms / cycle of CPU. The trade-off documented in
  ``backlog.md`` §Part A.
* **Read-only.** Never mutates the jsonl. Only ``open(..., 'rb')``,
  ``seek``, ``read`` — never write. ASCII-encoded session files are
  decoded as utf-8 with ``errors='replace'`` to survive embedded BOMs or
  malformed bytes.
* **Cooperative cancel.** ``stop()`` flips an event and the loop exits
  inside one poll interval. Caller awaits the task to drain.
* **Survives mid-line writes.** When the CLI flushes a partial JSON line
  (rare but happens at OS-level on fast writes), we buffer the trailing
  partial in ``_pending`` and pick it up next iteration once the newline
  arrives.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from daemon.pii_filter import scrub_event
from daemon.stream_parser import parse_stream_lines
from shared.events import BridgeEvent, ErrorEvent, StreamEnd
from shared.follow_protocol import CliMirrorEvent, cli_mirror_channel

log = logging.getLogger(__name__)


# How often we poll. 200 ms is the latency budget mentioned in backlog.md
# §Part A. CEO can override via ``BRIDGE_DAEMON_TAILER_POLL_MS`` env var
# if profiling shows we want faster (we won't go below 50 ms — diminishing
# returns vs the CLI's own stdout buffering).
DEFAULT_POLL_INTERVAL_S = 0.2

# Defensive cap on bytes read per poll iteration. Prevents a runaway log
# (e.g. CLI pasting 50 MB of stack trace into a single tool result) from
# blocking the event loop. 1 MiB per cycle = ~5 MB/s sustained, far above
# anything Claude actually emits.
_MAX_BYTES_PER_POLL = 1 * 1024 * 1024


# Publisher abstraction — production wires this to redis.publish, tests
# inject a list-appender.
Publisher = Callable[[str, str], Awaitable[Any]]


@dataclass
class TailerStatus:
    """Snapshot for ``/follow status`` and debug endpoints."""

    chat_id: int
    session_id: str
    path: str
    bytes_read: int
    lines_parsed: int
    events_published: int
    started_at: float
    last_activity_at: float
    ended: bool


class JsonlTailer:
    """Tails ONE jsonl file for ONE (chat_id, session_id) pair.

    Construct via :class:`JsonlTailerService` — direct instantiation is
    only used in tests so they can inject a fake publisher.
    """

    def __init__(
        self,
        *,
        path: Path,
        chat_id: int,
        session_id: str,
        publisher: Publisher,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        start_from_end: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._chat_id = chat_id
        self._session_id = session_id
        self._publish = publisher
        self._poll_s = max(0.05, poll_interval_s)
        self._start_from_end = start_from_end
        self._clock = clock

        self._stop = asyncio.Event()
        self._pending = ""  # buffered partial line carried across reads
        self._offset = 0  # last byte position we read up to
        self._inode = 0  # detect file rotation/recreation
        self._seq = 0  # monotonic per-tailer sequence number

        self._status = TailerStatus(
            chat_id=chat_id,
            session_id=session_id,
            path=str(path),
            bytes_read=0,
            lines_parsed=0,
            events_published=0,
            started_at=self._clock(),
            last_activity_at=self._clock(),
            ended=False,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def status(self) -> TailerStatus:
        return self._status

    def stop(self) -> None:
        """Request cooperative shutdown — next poll iteration exits."""
        self._stop.set()

    async def run(self) -> None:
        """Main loop. Returns when ``stop()`` is called or the file ends."""
        try:
            self._initialise_offset()
        except FileNotFoundError:
            await self._publish_event(
                ErrorEvent(
                    message=f"session file not found: {self._path.name}",
                    code="tailer_no_file",
                )
            )
            self._status.ended = True
            return

        while not self._stop.is_set():
            try:
                processed_any = await self._poll_once()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "tailer poll error session=%s: %s", self._session_id, exc
                )
                processed_any = False

            if not processed_any:
                # Sleep until next poll OR until stop is set, whichever first.
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self._poll_s
                    )
                except asyncio.TimeoutError:
                    pass

        self._status.ended = True

    # ------------------------------------------------------------------
    # File handling
    # ------------------------------------------------------------------

    def _initialise_offset(self) -> None:
        """Seed ``_offset`` based on current file size and policy.

        ``start_from_end=True`` (default) starts at EOF — the CEO doesn't
        want history dumped to Telegram when /follow on fires after the
        session has already been running. ``False`` starts at 0 — used by
        tests so they can write the file before the tailer starts.
        """
        st = self._path.stat()  # raises FileNotFoundError if missing
        self._inode = self._stable_id(st)
        if self._start_from_end:
            self._offset = st.st_size
        else:
            self._offset = 0

    @staticmethod
    def _stable_id(st: os.stat_result) -> int:
        """A best-effort "identity" for a file across renames/rotations.

        Linux/macOS: inode. Windows: 0 (stat_result.st_ino is always 0 on
        Windows for non-NTFS or python <3.12). We treat 0 as "can't
        detect rotation" and just rely on size shrinkage as a fallback.
        """
        return int(getattr(st, "st_ino", 0) or 0)

    async def _poll_once(self) -> bool:
        """Read whatever's new, parse, publish. Returns True iff anything fired."""
        try:
            st = self._path.stat()
        except FileNotFoundError:
            # File vanished — treat as session end. Don't loop on error.
            await self._publish_event(
                StreamEnd(reason="cli_session_ended", text="")
            )
            self._stop.set()
            return False

        size = st.st_size
        new_inode = self._stable_id(st)

        # Detect rotation: a different inode (Linux) OR a size that's
        # smaller than where we left off (file was truncated/recreated).
        if (new_inode and new_inode != self._inode) or size < self._offset:
            log.info(
                "tailer detected rotation session=%s — resetting offset",
                self._session_id,
            )
            self._offset = 0
            self._pending = ""
            self._inode = new_inode

        if size == self._offset:
            return False  # nothing new

        to_read = min(size - self._offset, _MAX_BYTES_PER_POLL)
        try:
            with self._path.open("rb") as f:
                f.seek(self._offset)
                chunk = f.read(to_read)
        except OSError as exc:
            log.debug("tailer read failed: %s", exc)
            return False

        self._offset += len(chunk)
        self._status.bytes_read += len(chunk)
        self._status.last_activity_at = self._clock()

        text = chunk.decode("utf-8", errors="replace")
        await self._process_text(text)
        return True

    async def _process_text(self, text: str) -> None:
        """Split text on newlines, buffer the trailing partial, parse complete lines."""
        buffer = self._pending + text
        if "\n" not in buffer:
            self._pending = buffer
            return

        # Split into complete lines + trailing partial. ``splitlines(True)``
        # would keep newlines; we use plain ``split`` and remember the
        # remainder ourselves.
        parts = buffer.split("\n")
        self._pending = parts[-1]
        complete_lines = parts[:-1]

        if not complete_lines:
            return

        # Stream-parse and publish.
        for event in parse_stream_lines(
            complete_lines, include_user_prompts=True
        ):
            self._status.lines_parsed += 1
            await self._publish_event(event)

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def _publish_event(self, event: BridgeEvent) -> None:
        """Scrub + wrap + publish a single BridgeEvent."""
        scrubbed, masked = scrub_event(event)
        self._seq += 1
        wire = CliMirrorEvent(
            chat_id=self._chat_id,
            session_id=self._session_id,
            source="cli",
            seq=self._seq,
            event=scrubbed.model_dump(),
            masked=masked,
        )
        try:
            await self._publish(
                cli_mirror_channel(self._chat_id),
                wire.model_dump_json(),
            )
            self._status.events_published += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "tailer publish failed session=%s seq=%d: %s",
                self._session_id,
                self._seq,
                exc,
            )
