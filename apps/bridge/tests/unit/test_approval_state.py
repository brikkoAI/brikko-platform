"""Tests for daemon.approval_state.SessionPermissionStore."""

from __future__ import annotations


from daemon.approval_state import SessionPermissionStore


_S1 = "session-one"
_S2 = "session-two"


def test_empty_store_returns_false_for_everything():
    store = SessionPermissionStore()
    assert not store.is_always_allowed(_S1, "Edit")
    assert not store.is_always_denied(_S1, "Edit")


def test_add_always_allow_then_is_always_allowed():
    store = SessionPermissionStore()
    store.add_always_allow(_S1, "Edit")
    assert store.is_always_allowed(_S1, "Edit")
    assert not store.is_always_allowed(_S1, "Write")  # separate tool
    assert not store.is_always_allowed(_S2, "Edit")  # separate session


def test_add_always_deny_then_is_always_denied():
    store = SessionPermissionStore()
    store.add_always_deny(_S1, "Bash")
    assert store.is_always_denied(_S1, "Bash")


def test_always_allow_overrides_prior_always_deny_same_tool():
    """If CEO denies-always then changes mind to allow-always, allow wins."""
    store = SessionPermissionStore()
    store.add_always_deny(_S1, "Edit")
    store.add_always_allow(_S1, "Edit")
    assert store.is_always_allowed(_S1, "Edit")
    assert not store.is_always_denied(_S1, "Edit")


def test_always_deny_overrides_prior_always_allow_same_tool():
    """Symmetric case."""
    store = SessionPermissionStore()
    store.add_always_allow(_S1, "Edit")
    store.add_always_deny(_S1, "Edit")
    assert store.is_always_denied(_S1, "Edit")
    assert not store.is_always_allowed(_S1, "Edit")


def test_clear_session_drops_only_that_session():
    store = SessionPermissionStore()
    store.add_always_allow(_S1, "Edit")
    store.add_always_allow(_S2, "Edit")
    store.clear_session(_S1)
    assert not store.is_always_allowed(_S1, "Edit")
    assert store.is_always_allowed(_S2, "Edit")


def test_clear_session_for_unknown_session_is_noop():
    store = SessionPermissionStore()
    store.clear_session("does-not-exist")  # must not raise


def test_clear_all_wipes_every_session():
    store = SessionPermissionStore()
    store.add_always_allow(_S1, "Edit")
    store.add_always_allow(_S2, "Write")
    store.clear_all()
    assert not store.is_always_allowed(_S1, "Edit")
    assert not store.is_always_allowed(_S2, "Write")


def test_snapshot_returns_shallow_copy():
    store = SessionPermissionStore()
    store.add_always_allow(_S1, "Edit")
    snap = store.snapshot(_S1)
    snap.always_allow.add("Write")  # mutate the snapshot
    assert not store.is_always_allowed(_S1, "Write")  # store unchanged


def test_snapshot_of_unknown_session_returns_empty():
    store = SessionPermissionStore()
    snap = store.snapshot("nonexistent")
    assert snap.always_allow == set()
    assert snap.always_deny == set()


def test_add_idempotent():
    store = SessionPermissionStore()
    store.add_always_allow(_S1, "Edit")
    store.add_always_allow(_S1, "Edit")
    snap = store.snapshot(_S1)
    assert snap.always_allow == {"Edit"}
