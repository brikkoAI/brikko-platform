"""Daemon-side Redis bridge for the CLI-mirror flow.

Subscribes to :data:`shared.follow_protocol.FOLLOW_CONTROL_CHANNEL` and
dispatches the bot's ``FollowControl`` messages into a
:class:`FollowRegistry`. Publishes ``FollowNotice`` confirmations back
on the per-chat ``bridge:cli-mirror:<chat_id>`` channel so the bot can
render "now following …" / "stopped …" / "session ended" text.

The service is single-instance per daemon process. ``run()`` loops until
``stop_event`` is set; failure paths log and continue (we don't want a
malformed control message to take the subscriber down).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import ValidationError

from daemon.follow_state import FollowRegistry
from shared.follow_protocol import (
    FOLLOW_CONTROL_CHANNEL,
    CliMirrorEvent,
    FollowAction,
    FollowControl,
    FollowNotice,
    cli_mirror_channel,
)

log = logging.getLogger(__name__)


class FollowService:
    """Wraps the registry + Redis subscriber for one daemon.

    Construct once at daemon startup, call ``run(stop_event)`` as a
    background task. Calls into ``self.registry`` for state.
    """

    def __init__(
        self,
        *,
        redis: Any,
        registry: FollowRegistry,
    ) -> None:
        self._redis = redis
        self.registry = registry

        # Wire hooks so the registry notifies the bot directly.
        self.registry.set_hooks(
            on_register=self._notify_registered,
            on_unregister=self._notify_unregistered,
        )

    # ------------------------------------------------------------------
    # Subscriber loop
    # ------------------------------------------------------------------

    async def run(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Subscribe and dispatch forever (or until stop_event is set)."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(FOLLOW_CONTROL_CHANNEL)
        log.info("follow service subscribed to %s", FOLLOW_CONTROL_CHANNEL)
        try:
            while stop_event is None or not stop_event.is_set():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                asyncio.create_task(self._handle_control(data))
        finally:
            try:
                await pubsub.unsubscribe()
                await pubsub.aclose()
            except Exception:  # pragma: no cover
                pass
            log.info("follow service stopped")

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _handle_control(self, raw: str) -> None:
        try:
            ctrl = FollowControl.model_validate_json(raw)
        except ValidationError as exc:
            log.warning("malformed follow control message: %s", exc)
            return

        try:
            await self.dispatch(ctrl)
        except Exception as exc:  # noqa: BLE001
            log.exception("follow control dispatch failed: %s", exc)
            await self._send_notice(
                chat_id=ctrl.tg_chat_id,
                session_id=ctrl.session_id,
                kind="error",
                message=f"dispatch failed: {exc}",
            )

    async def dispatch(self, ctrl: FollowControl) -> None:
        """Public entry-point used by tests; production goes through ``run()``."""
        if ctrl.action == FollowAction.REGISTER:
            ok, msg = await self.registry.register(
                chat_id=ctrl.tg_chat_id,
                session_id=ctrl.session_id,
            )
            if not ok:
                await self._send_notice(
                    chat_id=ctrl.tg_chat_id,
                    session_id=ctrl.session_id,
                    kind="error",
                    message=msg,
                )
            # Successful register → hook fires registered notice
            return

        if ctrl.action == FollowAction.UNREGISTER:
            ok, msg = await self.registry.unregister(
                chat_id=ctrl.tg_chat_id,
                session_id=ctrl.session_id,
            )
            if not ok:
                await self._send_notice(
                    chat_id=ctrl.tg_chat_id,
                    session_id=ctrl.session_id,
                    kind="error",
                    message=msg,
                )
            return

        if ctrl.action == FollowAction.UNREGISTER_ALL:
            count = await self.registry.unregister_all(chat_id=ctrl.tg_chat_id)
            await self._send_notice(
                chat_id=ctrl.tg_chat_id,
                session_id="",
                kind="status",
                message=f"unregistered {count} follow(s)",
            )
            return

    # ------------------------------------------------------------------
    # Bot-bound notice publishers
    # ------------------------------------------------------------------

    async def _notify_registered(self, chat_id: int, session_id: str) -> None:
        await self._send_notice(
            chat_id=chat_id,
            session_id=session_id,
            kind="registered",
            message="",
        )

    async def _notify_unregistered(
        self, chat_id: int, session_id: str, reason: str
    ) -> None:
        kind = "unregistered" if reason == "user_request" else "session_ended"
        await self._send_notice(
            chat_id=chat_id,
            session_id=session_id,
            kind=kind,
            message=reason if reason != "user_request" else "",
        )

    async def _send_notice(
        self,
        *,
        chat_id: int,
        session_id: str,
        kind: str,
        message: str,
    ) -> None:
        """Publish a wrapped FollowNotice on the per-chat mirror channel.

        We piggyback on the same channel the bot already subscribes to for
        mirror events, encoded as a CliMirrorEvent with a notice payload
        in the ``event`` dict (kind=``follow_notice``). The bot recognises
        the special type and renders it as plain text rather than a
        BridgeEvent.
        """
        notice = FollowNotice(
            chat_id=chat_id,
            session_id=session_id,
            kind=kind,
            message=message,
        )
        wrapper = CliMirrorEvent(
            chat_id=chat_id,
            session_id=session_id,
            source="cli",
            seq=0,
            event={"type": "follow_notice", **notice.model_dump()},
            masked=False,
        )
        try:
            await self._redis.publish(
                cli_mirror_channel(chat_id), wrapper.model_dump_json()
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "failed to publish follow notice chat=%s kind=%s: %s",
                chat_id,
                kind,
                exc,
            )
