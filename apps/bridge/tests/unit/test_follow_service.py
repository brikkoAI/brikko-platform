"""Tests for daemon.follow_service — Redis subscriber + dispatch."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fakeredis.aioredis import FakeRedis

from daemon.follow_service import FollowService
from daemon.follow_state import FollowRegistry
from shared.follow_protocol import (
    FOLLOW_CONTROL_CHANNEL,
    CliMirrorEvent,
    FollowAction,
    FollowControl,
    cli_mirror_channel,
)


def _seed_session(tmp_path: Path, sid: str = "sid") -> Path:
    p = tmp_path / f"{sid}.jsonl"
    p.write_text(
        json.dumps(
            {"type": "system", "subtype": "init", "session_id": sid, "cwd": "C:/x"}
        )
        + "\n",
        encoding="utf-8",
    )
    return p


def _locator(mapping: dict):
    return lambda sid: mapping.get(sid)


# ---------------------------------------------------------------------------
# Direct dispatch — no subscriber loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_register_starts_tailer(tmp_path: Path):
    redis = FakeRedis()
    p = _seed_session(tmp_path)
    reg = FollowRegistry(
        publisher=redis.publish,
        locate=_locator({"sid": p}),
        poll_interval_s=0.05,
    )
    svc = FollowService(redis=redis, registry=reg)
    await svc.dispatch(
        FollowControl(action=FollowAction.REGISTER, tg_chat_id=1, session_id="sid")
    )
    try:
        assert reg.is_following(1, "sid") is True
    finally:
        await reg.close_all()


@pytest.mark.asyncio
async def test_dispatch_register_publishes_registered_notice(tmp_path: Path):
    redis = FakeRedis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(cli_mirror_channel(1))

    p = _seed_session(tmp_path)
    reg = FollowRegistry(
        publisher=redis.publish,
        locate=_locator({"sid": p}),
        poll_interval_s=0.05,
    )
    svc = FollowService(redis=redis, registry=reg)
    try:
        await svc.dispatch(
            FollowControl(
                action=FollowAction.REGISTER, tg_chat_id=1, session_id="sid"
            )
        )
        # Drain pubsub for the notice
        notice = None
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if msg and msg.get("type") == "message":
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                wire = CliMirrorEvent.model_validate_json(data)
                if wire.event.get("type") == "follow_notice":
                    notice = wire
                    break
        assert notice is not None
        assert notice.event["kind"] == "registered"
        assert notice.session_id == "sid"
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
        await reg.close_all()


@pytest.mark.asyncio
async def test_dispatch_unknown_session_publishes_error(tmp_path: Path):
    redis = FakeRedis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(cli_mirror_channel(1))

    reg = FollowRegistry(
        publisher=redis.publish,
        locate=_locator({}),  # nothing matches
        poll_interval_s=0.05,
    )
    svc = FollowService(redis=redis, registry=reg)
    try:
        await svc.dispatch(
            FollowControl(
                action=FollowAction.REGISTER, tg_chat_id=1, session_id="missing"
            )
        )
        notice = None
        deadline = asyncio.get_event_loop().time() + 1.0
        while asyncio.get_event_loop().time() < deadline:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if msg and msg.get("type") == "message":
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                wire = CliMirrorEvent.model_validate_json(data)
                if wire.event.get("type") == "follow_notice":
                    notice = wire
                    break
        assert notice is not None
        assert notice.event["kind"] == "error"
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()


@pytest.mark.asyncio
async def test_dispatch_unregister(tmp_path: Path):
    redis = FakeRedis()
    p = _seed_session(tmp_path)
    reg = FollowRegistry(
        publisher=redis.publish,
        locate=_locator({"sid": p}),
        poll_interval_s=0.05,
    )
    svc = FollowService(redis=redis, registry=reg)
    await svc.dispatch(
        FollowControl(action=FollowAction.REGISTER, tg_chat_id=1, session_id="sid")
    )
    assert reg.is_following(1, "sid")

    await svc.dispatch(
        FollowControl(action=FollowAction.UNREGISTER, tg_chat_id=1, session_id="sid")
    )
    assert reg.is_following(1, "sid") is False


@pytest.mark.asyncio
async def test_dispatch_unregister_all(tmp_path: Path):
    redis = FakeRedis()
    paths = {sid: _seed_session(tmp_path, sid) for sid in ("a", "b")}
    reg = FollowRegistry(
        publisher=redis.publish,
        locate=_locator(paths),
        poll_interval_s=0.05,
    )
    svc = FollowService(redis=redis, registry=reg)
    for sid in paths:
        await svc.dispatch(
            FollowControl(action=FollowAction.REGISTER, tg_chat_id=1, session_id=sid)
        )
    assert len(reg.snapshot(chat_id=1)) == 2

    await svc.dispatch(
        FollowControl(
            action=FollowAction.UNREGISTER_ALL, tg_chat_id=1, session_id=""
        )
    )
    assert reg.snapshot(chat_id=1) == []


# ---------------------------------------------------------------------------
# Subscriber loop — end-to-end with FakeRedis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_loop_processes_control_messages(tmp_path: Path):
    redis = FakeRedis()
    p = _seed_session(tmp_path)
    reg = FollowRegistry(
        publisher=redis.publish,
        locate=_locator({"sid": p}),
        poll_interval_s=0.05,
    )
    svc = FollowService(redis=redis, registry=reg)
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop_event=stop))
    try:
        await asyncio.sleep(0.1)  # let subscribe land
        await redis.publish(
            FOLLOW_CONTROL_CHANNEL,
            FollowControl(
                action=FollowAction.REGISTER, tg_chat_id=1, session_id="sid"
            ).model_dump_json(),
        )
        # Wait for the registry to have the entry
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            if reg.is_following(1, "sid"):
                break
            await asyncio.sleep(0.05)
        assert reg.is_following(1, "sid")
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
        await reg.close_all()


@pytest.mark.asyncio
async def test_subscriber_ignores_malformed_control(tmp_path: Path):
    redis = FakeRedis()
    reg = FollowRegistry(
        publisher=redis.publish,
        locate=_locator({}),
        poll_interval_s=0.05,
    )
    svc = FollowService(redis=redis, registry=reg)
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop_event=stop))
    try:
        await asyncio.sleep(0.1)
        await redis.publish(FOLLOW_CONTROL_CHANNEL, "not-json")
        await asyncio.sleep(0.2)
        # No crash, no entries
        assert reg.snapshot() == []
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
