"""Background sweeper for stale ``account_holds`` rows (TD-028).

Why
---

A hold is meant to live no longer than the upstream provider call
(default TTL 120s, see ``billing/engine.py::DEFAULT_HOLD_TTL_SECONDS``).
Three things can leave one orphaned past its expiry:

1. Worker process crashes between ``hold_amount`` and either
   ``commit_hold_to_debit`` or ``release_hold``. The DB transaction
   committed (because ``hold_amount`` returns after commit) but the
   side effects didn't.
2. A request that timed out at the LB / client side after we placed the
   hold but before we got a chance to release it. Less common because
   FastAPI handlers see ``CancelledError`` and we have ``finally``
   release paths — but defensive cleanup is cheap.
3. A bug in our own code that misses a release branch. Janitor is the
   safety net that lets this kind of bug ship without locking up
   customer balance for ever.

Each orphaned hold reduces the visible balance for that account because
``hold_amount`` sums all active holds against the balance during
pre-flight. Without a janitor, an account with several minutes of crash
history would have a permanently understated available balance.

Design
------

* One async task started during FastAPI ``lifespan``. Polls every
  ``HOLD_JANITOR_INTERVAL_SECONDS`` (default 60s).
* Sweep predicate: ``expires_at < NOW() - HOLD_JANITOR_GRACE_SECONDS``.
  The grace window is double-belt-and-braces: even if the application
  hands a request that's running 4 minutes longer than its hold TTL,
  we don't yank its hold out from under it.
* Single SQL ``DELETE`` per tick — Postgres handles thousands of rows
  in one statement faster than we could iterate.
* Uses a fresh DB session per tick (the task isn't request-scoped), and
  ignores Redis entirely.

Failure modes
-------------

* DB unreachable → log and retry next tick. Janitor is best-effort
  cleanup; another tick fixes it.
* Loop crashes → outer ``while`` re-raises after logging; ``lifespan``
  catches it on shutdown, the supervisor (gunicorn) restarts the worker.

Metrics
-------

Logs ``hold_janitor_tick`` with the row count for now. Once we have
Prometheus (Sprint 3 / TD-027) we'll emit a counter
``holds_garbage_collected_total`` keyed by ``reason='expired'``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voltari_gateway.db.models import AccountHold
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


async def sweep_expired_holds(db: AsyncSession, *, grace_seconds: int) -> int:
    """Delete every hold whose ``expires_at`` is older than the grace window.

    Returns the number of rows deleted. Caller commits via the standard
    session protocol — we don't commit ourselves so this is testable
    without `commit-then-rollback` gymnastics.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=grace_seconds)
    stmt = delete(AccountHold).where(AccountHold.expires_at < cutoff)
    result = await db.execute(stmt)
    rowcount = getattr(result, "rowcount", 0) or 0
    return int(rowcount)


async def hold_janitor_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    interval_seconds: int,
    grace_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """Run the sweep on a fixed interval until ``stop_event`` is set.

    The loop swallows individual-tick exceptions so a transient DB blip
    doesn't kill the task — we log and retry on the next interval.
    """
    log.info(
        "hold_janitor_started",
        interval_seconds=interval_seconds,
        grace_seconds=grace_seconds,
    )
    while not stop_event.is_set():
        try:
            async with session_factory() as session:
                deleted = await sweep_expired_holds(session, grace_seconds=grace_seconds)
                await session.commit()
            if deleted:
                log.info("hold_janitor_tick", holds_cleaned=deleted)
        except Exception as exc:
            log.warning("hold_janitor_tick_failed", error=str(exc))

        # Sleep with cancellation responsiveness — wait_for stop_event with
        # a timeout so shutdown doesn't have to wait an entire interval.
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue

    log.info("hold_janitor_stopped")


__all__ = [
    "hold_janitor_loop",
    "sweep_expired_holds",
]
