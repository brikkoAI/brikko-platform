"""Tests for daemon.follow_state — FollowRegistry + default locator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daemon.follow_state import FollowRegistry, make_default_locator


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _Publisher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, ch: str, payload: str) -> None:
        self.calls.append((ch, payload))


def _make_locator(mapping: dict[str, Path]):
    def locate(session_id: str) -> Path | None:
        return mapping.get(session_id)

    return locate


def _write_seed(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "x",
                "cwd": "C:/x",
            }
        )
        + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_unknown_session_returns_error(tmp_path: Path):
    pub = _Publisher()
    reg = FollowRegistry(publisher=pub, locate=_make_locator({}))
    ok, msg = await reg.register(chat_id=1, session_id="nope")
    assert ok is False
    assert "not found" in msg
    assert reg.is_following(1, "nope") is False


@pytest.mark.asyncio
async def test_register_starts_tailer_task(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    _write_seed(p)
    pub = _Publisher()
    reg = FollowRegistry(
        publisher=pub,
        locate=_make_locator({"sid": p}),
        poll_interval_s=0.05,
    )
    ok, msg = await reg.register(chat_id=1, session_id="sid")
    try:
        assert ok is True
        assert "following" in msg
        assert reg.is_following(1, "sid") is True
        # Snapshot reflects it
        snap = reg.snapshot(chat_id=1)
        assert len(snap) == 1
        assert snap[0].session_id == "sid"
    finally:
        await reg.close_all()


@pytest.mark.asyncio
async def test_double_register_is_idempotent(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    _write_seed(p)
    pub = _Publisher()
    reg = FollowRegistry(
        publisher=pub,
        locate=_make_locator({"sid": p}),
        poll_interval_s=0.05,
    )
    ok1, _ = await reg.register(chat_id=1, session_id="sid")
    ok2, msg2 = await reg.register(chat_id=1, session_id="sid")
    try:
        assert ok1 is True
        assert ok2 is True
        assert "already" in msg2
        assert len(reg.snapshot(chat_id=1)) == 1
    finally:
        await reg.close_all()


@pytest.mark.asyncio
async def test_unregister_cancels_task(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    _write_seed(p)
    pub = _Publisher()
    reg = FollowRegistry(
        publisher=pub,
        locate=_make_locator({"sid": p}),
        poll_interval_s=0.05,
    )
    await reg.register(chat_id=1, session_id="sid")
    ok, msg = await reg.unregister(chat_id=1, session_id="sid")
    assert ok is True
    assert reg.is_following(1, "sid") is False


@pytest.mark.asyncio
async def test_unregister_unknown_returns_false(tmp_path: Path):
    pub = _Publisher()
    reg = FollowRegistry(publisher=pub, locate=_make_locator({}))
    ok, msg = await reg.unregister(chat_id=1, session_id="sid")
    assert ok is False


@pytest.mark.asyncio
async def test_unregister_all_drops_every_session_for_chat(tmp_path: Path):
    pub = _Publisher()
    paths = {}
    for sid in ("a", "b", "c"):
        p = tmp_path / f"{sid}.jsonl"
        _write_seed(p)
        paths[sid] = p
    reg = FollowRegistry(
        publisher=pub,
        locate=_make_locator(paths),
        poll_interval_s=0.05,
    )
    for sid in paths:
        await reg.register(chat_id=42, session_id=sid)
    # And one entry for a different chat — must NOT be dropped
    other = tmp_path / "other.jsonl"
    _write_seed(other)
    reg.locate = _make_locator({**paths, "other": other})
    await reg.register(chat_id=99, session_id="other")

    try:
        n = await reg.unregister_all(chat_id=42)
        assert n == 3
        assert reg.snapshot(chat_id=42) == []
        # The other chat is untouched
        assert len(reg.snapshot(chat_id=99)) == 1
    finally:
        await reg.close_all()


@pytest.mark.asyncio
async def test_close_all_cancels_all_tailers(tmp_path: Path):
    pub = _Publisher()
    p = tmp_path / "s.jsonl"
    _write_seed(p)
    reg = FollowRegistry(
        publisher=pub,
        locate=_make_locator({"sid": p}),
        poll_interval_s=0.05,
    )
    await reg.register(chat_id=1, session_id="sid")
    await reg.close_all()
    assert reg.snapshot() == []


@pytest.mark.asyncio
async def test_on_register_hook_fires(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    _write_seed(p)
    pub = _Publisher()
    reg = FollowRegistry(
        publisher=pub,
        locate=_make_locator({"sid": p}),
        poll_interval_s=0.05,
    )
    fired: list[tuple[int, str]] = []

    async def on_reg(cid: int, sid: str) -> None:
        fired.append((cid, sid))

    reg.set_hooks(on_register=on_reg)
    await reg.register(chat_id=7, session_id="sid")
    try:
        assert fired == [(7, "sid")]
    finally:
        await reg.close_all()


@pytest.mark.asyncio
async def test_on_unregister_hook_fires_with_reason(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    _write_seed(p)
    pub = _Publisher()
    reg = FollowRegistry(
        publisher=pub,
        locate=_make_locator({"sid": p}),
        poll_interval_s=0.05,
    )
    fired: list[tuple[int, str, str]] = []

    async def on_unreg(cid: int, sid: str, reason: str) -> None:
        fired.append((cid, sid, reason))

    reg.set_hooks(on_unregister=on_unreg)
    await reg.register(chat_id=7, session_id="sid")
    await reg.unregister(chat_id=7, session_id="sid", reason="user_request")
    assert len(fired) == 1
    assert fired[0] == (7, "sid", "user_request")


# ---------------------------------------------------------------------------
# Default locator
# ---------------------------------------------------------------------------


def test_default_locator_finds_file(tmp_path: Path):
    base = tmp_path / "projects"
    project = base / "C--projects-foo"
    project.mkdir(parents=True)
    target = project / "abc-123.jsonl"
    target.write_text("", encoding="utf-8")

    locate = make_default_locator(sessions_dir=base)
    assert locate("abc-123") == target


def test_default_locator_missing_returns_none(tmp_path: Path):
    base = tmp_path / "projects"
    base.mkdir(parents=True)
    locate = make_default_locator(sessions_dir=base)
    assert locate("nope") is None


def test_default_locator_no_base_dir_returns_none(tmp_path: Path):
    locate = make_default_locator(sessions_dir=tmp_path / "nonexistent")
    assert locate("anything") is None


def test_default_locator_empty_session_id_returns_none(tmp_path: Path):
    locate = make_default_locator(sessions_dir=tmp_path)
    assert locate("") is None
