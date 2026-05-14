"""Process-singleton wiring for the :class:`FollowRegistry`.

Daemon lifespan constructs the registry and FollowService once, and the
API needs access to introspect it (`GET /follows`). Following the same
pattern :mod:`daemon.sdk_runner` uses for ``SessionRunnerRegistry``.
"""

from __future__ import annotations

from daemon.follow_state import FollowRegistry

_SHARED: FollowRegistry | None = None


def set_shared_follow_registry(registry: FollowRegistry | None) -> None:
    global _SHARED
    _SHARED = registry


def get_shared_follow_registry() -> FollowRegistry | None:
    return _SHARED
