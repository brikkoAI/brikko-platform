"""Edge-case tests for ``voltari_gateway.billing.janitor.hold_janitor_loop``.

The base ``test_janitor.py`` covers the happy path. This file exercises:

* Exception in a single tick → loop continues.
* ``wait_for(stop_event)`` timeout path → next iteration runs.

Targets the missing lines in janitor.py (109-110, 116-117).
"""

from __future__ import annotations

import asyncio

import pytest

from voltari_gateway.billing.janitor import hold_janitor_loop


@pytest.mark.asyncio
async def test_janitor_loop_continues_after_factory_exception() -> None:
    """If the session factory raises, the loop logs + retries on next tick."""

    class _BoomFactory:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self):
            self.calls += 1
            raise RuntimeError("DB pool exhausted")

    factory = _BoomFactory()
    stop = asyncio.Event()
    task = asyncio.create_task(
        hold_janitor_loop(
            session_factory=factory,  # type: ignore[arg-type]
            interval_seconds=0.02,
            grace_seconds=300,
            stop_event=stop,
        )
    )
    # Let it tick at least twice — both raise, both swallowed.
    await asyncio.sleep(0.08)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert factory.calls >= 2


@pytest.mark.asyncio
async def test_janitor_loop_timeout_path_continues(session_factory) -> None:
    """``except TimeoutError: continue`` branch in the wait_for sleep."""
    stop = asyncio.Event()
    task = asyncio.create_task(
        hold_janitor_loop(
            session_factory=session_factory,
            interval_seconds=0.01,
            grace_seconds=300,
            stop_event=stop,
        )
    )
    # Run for ~30 ms — multiple intervals must fire and each times out.
    await asyncio.sleep(0.03)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
