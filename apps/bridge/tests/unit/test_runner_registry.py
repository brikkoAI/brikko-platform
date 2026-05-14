"""Tests for daemon.runner_registry."""
from unittest.mock import MagicMock

import pytest

from daemon import runner_registry


@pytest.fixture(autouse=True)
def reset_registry():
    runner_registry._active.clear()
    yield
    runner_registry._active.clear()


def _live_proc() -> MagicMock:
    proc = MagicMock()
    proc.returncode = None
    proc.pid = 12345
    return proc


def _dead_proc(rc: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = rc
    proc.pid = 99999
    return proc


def test_register_then_get_returns_proc():
    proc = _live_proc()
    runner_registry.register("sess-1", proc)
    assert runner_registry.get("sess-1") is proc


def test_register_evicts_prior_alive_proc():
    old = _live_proc()
    new = _live_proc()
    runner_registry.register("sess-1", old)
    runner_registry.register("sess-1", new)
    # Old should be terminated, new should be the active one
    old.terminate.assert_called_once()
    assert runner_registry.get("sess-1") is new


def test_register_does_not_terminate_already_dead_proc():
    old = _dead_proc(rc=0)
    new = _live_proc()
    runner_registry.register("sess-1", old)
    runner_registry.register("sess-1", new)
    old.terminate.assert_not_called()


def test_unregister_removes_only_if_still_active():
    proc = _live_proc()
    runner_registry.register("sess-1", proc)
    runner_registry.unregister("sess-1", proc)
    assert runner_registry.get("sess-1") is None


def test_unregister_no_op_if_replaced():
    """If a newer proc replaced the old one, unregister(old) should not wipe new."""
    old = _live_proc()
    new = _live_proc()
    runner_registry.register("sess-1", old)
    runner_registry.register("sess-1", new)  # evicts old
    runner_registry.unregister("sess-1", old)  # called by old's finally block
    assert runner_registry.get("sess-1") is new


def test_cancel_kills_live_proc_returns_true():
    proc = _live_proc()
    runner_registry.register("sess-1", proc)
    assert runner_registry.cancel("sess-1") is True
    proc.terminate.assert_called_once()
    assert runner_registry.get("sess-1") is None


def test_cancel_returns_false_when_no_proc():
    assert runner_registry.cancel("missing") is False


def test_cancel_returns_false_when_proc_already_dead():
    proc = _dead_proc(rc=0)
    runner_registry.register("sess-1", proc)
    assert runner_registry.cancel("sess-1") is False
    proc.terminate.assert_not_called()


def test_active_session_ids_lists_only_live_procs():
    a = _live_proc()
    b = _dead_proc(rc=0)
    c = _live_proc()
    runner_registry.register("a", a)
    runner_registry.register("b", b)
    runner_registry.register("c", c)
    ids = runner_registry.active_session_ids()
    assert set(ids) == {"a", "c"}
