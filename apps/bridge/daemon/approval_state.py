"""Per-session always-allow / always-deny cache (daemon-local, in-memory).

Why in-memory and not Redis or SQLite:

  * Single-tenant by design — only one CEO uses the bridge.
  * "Always" decisions are SESSION-scoped (per ``ClaudeSDKClient`` lifetime).
    Persistence is intentionally NOT a feature here: the design rejects a
    permission DB in §11 because the blast radius of "wrong cached permission"
    is high and the upside is low for solo use.
  * Daemon restart wipes the cache — design says this is the right behaviour.
    The bot's ``/start`` welcome text documents it explicitly.

The ``/permissions reset`` bot command (CEO decision 2026-05-11 #4) calls
``clear_session(session_id)`` to drop the cache mid-conversation when the CEO
realises they tapped "always" by mistake.

Thread/coroutine safety:

  The daemon runs every prompt under ``SessionRunner._lock`` (serial per
  session), so two concurrent callbacks for the same session_id can't happen.
  We still guard the dict ops trivially because dict mutation from multiple
  asyncio tasks is fine on CPython — we only need to make sure we don't
  TypeError on a missing key.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionPermissions:
    """Always-allow / always-deny set for one Claude session."""

    always_allow: set[str] = field(default_factory=set)
    always_deny: set[str] = field(default_factory=set)


class SessionPermissionStore:
    """In-memory store, keyed by Claude session_id.

    Use one instance per daemon process. It's safe to share across
    ``SessionRunner`` instances — the broker resolves session by id.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionPermissions] = {}

    def _get(self, session_id: str) -> SessionPermissions:
        perms = self._sessions.get(session_id)
        if perms is None:
            perms = SessionPermissions()
            self._sessions[session_id] = perms
        return perms

    def is_always_allowed(self, session_id: str, tool_name: str) -> bool:
        perms = self._sessions.get(session_id)
        return bool(perms and tool_name in perms.always_allow)

    def is_always_denied(self, session_id: str, tool_name: str) -> bool:
        perms = self._sessions.get(session_id)
        return bool(perms and tool_name in perms.always_deny)

    def add_always_allow(self, session_id: str, tool_name: str) -> None:
        perms = self._get(session_id)
        perms.always_allow.add(tool_name)
        # An "always allow" overrides any prior "always deny" for the same tool.
        perms.always_deny.discard(tool_name)

    def add_always_deny(self, session_id: str, tool_name: str) -> None:
        perms = self._get(session_id)
        perms.always_deny.add(tool_name)
        perms.always_allow.discard(tool_name)

    def clear_session(self, session_id: str) -> None:
        """Drop ALL always-allow / always-deny for ``session_id``.

        Called by the ``/permissions reset`` bot command and when the
        ``SessionRunner`` for this session disposes.
        """
        self._sessions.pop(session_id, None)

    def clear_all(self) -> None:
        """Drop every session's cache. Used by daemon-wide /permissions reset."""
        self._sessions.clear()

    def snapshot(self, session_id: str) -> SessionPermissions:
        """Read-only view for ``/permissions list`` / debugging.

        Returns a shallow-copy SessionPermissions — mutating the result does
        not affect the store.
        """
        perms = self._sessions.get(session_id)
        if perms is None:
            return SessionPermissions()
        return SessionPermissions(
            always_allow=set(perms.always_allow),
            always_deny=set(perms.always_deny),
        )


# Default daemon-wide singleton. Tests should construct their own and inject.
DEFAULT_STORE = SessionPermissionStore()
