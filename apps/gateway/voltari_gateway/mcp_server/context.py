"""Per-request principal context for the MCP server.

The official ``mcp`` SDK's tool handlers take ``(name, arguments)`` and
have no direct access to the underlying ASGI request — by design, the
transport sits between the HTTP layer and the JSON-RPC dispatcher.

To still let tools know *who* is calling, the ASGI wrapper authenticates
the request, builds an ``McpPrincipal``, and stores it in a
``ContextVar`` before calling into the session manager. Tool handlers
then read the principal via ``current_principal()``.

This is the standard pattern recommended by the SDK team for auth-aware
servers running over the streamable-HTTP transport. ``ContextVar`` is
asyncio-safe — each request lives in its own task and sees only its own
binding.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from voltari_gateway.db.models import McpScope


@dataclass(frozen=True)
class McpPrincipal:
    """Fully-resolved MCP caller. Built by the auth layer, read by tools."""

    account_id: uuid.UUID
    token_id: uuid.UUID
    user_id: uuid.UUID
    scope: McpScope
    tariff: str


# Per-request principal. ``None`` outside an authenticated MCP request
# (e.g. SDK probing, never expected in production). Tools must defend.
MCP_PRINCIPAL_CTX: ContextVar[McpPrincipal | None] = ContextVar("mcp_principal", default=None)


def current_principal() -> McpPrincipal:
    """Return the principal bound to the current request, or raise.

    Tools call this; the auth wrapper guarantees the binding exists
    before the SDK dispatches to a handler. A missing binding means the
    server was misconfigured (handler called without auth) — surface that
    as a programmer error, not a quiet ``None``.
    """
    principal = MCP_PRINCIPAL_CTX.get()
    if principal is None:
        raise RuntimeError(
            "MCP tool invoked without an authenticated principal. "
            "This indicates a missing auth wrapper around the session manager."
        )
    return principal


__all__ = [
    "MCP_PRINCIPAL_CTX",
    "McpPrincipal",
    "current_principal",
]
