"""Tracks live claude subprocesses so /cancel can kill them.

The runner registry is process-local; we don't persist anything. The bot
stays in sync because it simply calls ``POST /sessions/{id}/cancel`` —
if there's no live process for that session_id we return ``ok: false``
with reason ``not_running``.

Key by ``session_id`` because that's what the user identifies a run by.
If multiple prompts are sent to the same session in parallel (rare —
single-tenant), the latest one wins; previous ones get orphaned but
should also be killed when the new one registers.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

log = logging.getLogger(__name__)


_active: dict[str, asyncio.subprocess.Process] = {}


def register(session_id: str, proc: asyncio.subprocess.Process) -> None:
    """Mark ``proc`` as the live process for ``session_id``.

    If a previous process is already registered for this session, it is
    terminated so we don't accumulate zombies.
    """
    prev = _active.get(session_id)
    if prev is not None and prev.returncode is None:
        log.info("evicting prior claude proc for session=%s pid=%s", session_id, prev.pid)
        _terminate(prev)
    _active[session_id] = proc


def unregister(session_id: str, proc: asyncio.subprocess.Process) -> None:
    """Drop the registration if ``proc`` is still the active one."""
    cur = _active.get(session_id)
    if cur is proc:
        _active.pop(session_id, None)


def get(session_id: str) -> Optional[asyncio.subprocess.Process]:
    return _active.get(session_id)


def cancel(session_id: str) -> bool:
    """Terminate the live claude process for ``session_id``.

    Returns True if a process was killed, False if no live process found.
    """
    proc = _active.get(session_id)
    if proc is None or proc.returncode is not None:
        return False
    _terminate(proc)
    _active.pop(session_id, None)
    return True


def active_session_ids() -> list[str]:
    """Return session_ids that currently have a live claude proc."""
    return [sid for sid, p in _active.items() if p.returncode is None]


def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Kill a subprocess. On Windows, terminate(); elsewhere, SIGTERM."""
    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            proc.terminate()
    except ProcessLookupError:
        # Already gone
        pass
    except Exception as e:
        log.warning("terminate failed: %s", e)
