"""Daemon entry-point.

Run: ``python -m daemon.main``.

In production it's wrapped by a Windows Service (NSSM) or a Python
supervisor that also restarts the SSH reverse tunnel. See
``apps/bridge/deploy/`` for the supervisor.

Listens on 127.0.0.1 only — *never* bind 0.0.0.0. The bot reaches us
through the SSH reverse tunnel only.

Phase 8 — startup also wires the SDK runner registry + Redis-backed
ApprovalBroker. Failure to construct Redis at startup is non-fatal: the
broker is created lazily on first send_prompt, and yolo_on_redis_down
decides behaviour when Redis is unreachable.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import redis.asyncio as redis_async
import uvicorn
from fastapi import FastAPI

from daemon import auth_tokens
from daemon.api import router
from daemon.approval_broker import ApprovalBroker
from daemon.approval_state import DEFAULT_STORE
from daemon.config import get_settings
from daemon.follow_registry_holder import (
    set_shared_follow_registry,
)
from daemon.follow_service import FollowService
from daemon.follow_state import FollowRegistry, make_default_locator
from daemon.sdk_runner import (
    SessionRunnerRegistry,
    get_shared_registry,
    set_shared_registry,
)

log = logging.getLogger(__name__)


def _resolve_chat_id_for_session(_session_id: str) -> int | None:
    """Single-tenant resolver — return the first previously paired chat_id.

    Bridge is single-user today (CEO). Multi-tenant would require a
    session_id → chat_id mapping persisted in auth_tokens.py.
    """
    paired = auth_tokens.previously_paired_chat_ids()
    return paired[0] if paired else None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Construct (or skip) the SDK approval pipeline on startup."""
    settings = get_settings()

    # Loud warning when yolo_mode is set — every tool call WILL execute
    # without approval prompts. This is the "delete-this-protection" override.
    if settings.yolo_mode:
        log.warning(
            "BRIDGE_DAEMON_YOLO_MODE=true — all tools auto-approved! "
            "Approval flow disabled. To re-enable safety, unset the env var."
        )

    if settings.legacy_subprocess_runner:
        log.warning(
            "BRIDGE_DAEMON_LEGACY_SUBPROCESS_RUNNER=true — using subprocess "
            "runner. New approval flow (SDK callback) is NOT engaged."
        )

    # Build the SDK pipeline unless explicitly disabled.
    if settings.yolo_mode or settings.legacy_subprocess_runner:
        set_shared_registry(None)
        set_shared_follow_registry(None)
        yield
        return

    redis_client = redis_async.from_url(settings.redis_url)
    broker = ApprovalBroker(
        redis=redis_client,
        chat_id_resolver=_resolve_chat_id_for_session,
        permission_store=DEFAULT_STORE,
        timeout_seconds=settings.approval_timeout_seconds,
        yolo_on_redis_down=settings.yolo_on_redis_down,
    )
    registry = SessionRunnerRegistry(broker)
    set_shared_registry(registry)

    # CLI-mirror plumbing (Part A). Errors here are non-fatal — without the
    # follow service the rest of the daemon works as before.
    follow_stop = asyncio.Event()
    follow_task: asyncio.Task | None = None
    follow_registry = None
    try:
        follow_publisher = redis_client.publish
        follow_registry = FollowRegistry(
            publisher=follow_publisher,
            locate=make_default_locator(),
            poll_interval_s=settings.follow_poll_interval_s,
        )
        follow_service = FollowService(
            redis=redis_client, registry=follow_registry
        )
        set_shared_follow_registry(follow_registry)
        follow_task = asyncio.create_task(
            follow_service.run(stop_event=follow_stop),
            name="follow-service-subscriber",
        )
        log.info(
            "follow service ready (poll=%.2fs)", settings.follow_poll_interval_s
        )
    except Exception as exc:  # noqa: BLE001  pragma: no cover
        log.warning("follow service init failed (non-fatal): %s", exc)
        set_shared_follow_registry(None)

    log.info(
        "daemon startup: SDK approval pipeline ready (redis=%s, timeout=%ds, "
        "yolo_on_redis_down=%s)",
        settings.redis_url,
        settings.approval_timeout_seconds,
        settings.yolo_on_redis_down,
    )

    try:
        yield
    finally:
        # Stop follow service first so tailers don't observe a dying redis.
        follow_stop.set()
        if follow_task is not None:
            try:
                await asyncio.wait_for(follow_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                follow_task.cancel()
        if follow_registry is not None:
            try:
                await follow_registry.close_all()
            except Exception as exc:  # pragma: no cover
                log.warning("follow_registry.close_all failed: %s", exc)
        set_shared_follow_registry(None)

        # Best-effort cleanup on shutdown.
        reg = get_shared_registry()
        if reg is not None:
            try:
                await reg.close_all()
            except Exception as exc:  # pragma: no cover
                log.warning("registry.close_all failed: %s", exc)
        set_shared_registry(None)
        try:
            await redis_client.aclose()
        except Exception as exc:  # pragma: no cover
            log.warning("redis close failed: %s", exc)


app = FastAPI(title="Brikko Bridge Daemon", version="0.1.0", lifespan=lifespan)
app.include_router(router)


def run() -> None:
    settings = get_settings()
    if settings.listen_host not in ("127.0.0.1", "::1", "localhost"):
        raise RuntimeError(
            f"Refusing to bind to {settings.listen_host!r}. "
            "Daemon must listen on loopback only — bot connects via SSH "
            "reverse tunnel. Override only if you know what you're doing."
        )
    uvicorn.run(
        "daemon.main:app",
        host=settings.listen_host,
        port=settings.listen_port,
        log_level="info",
    )


if __name__ == "__main__":
    run()
