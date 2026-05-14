"""Brikko-MCP server (Sprint MCP S2).

Streamable-HTTP MCP transport mounted under ``/mcp`` on the gateway
FastAPI app. Auth flows over Bearer ``mcp-brk-*`` tokens issued by the
management API in Sprint MCP S1 (``/v1/mcp/tokens``).

Three tools shipped in S2 (all read-only):

* ``read_account``       — current balance, tariff, account status.
* ``read_usage``         — usage summary for ``24h | 7d | 30d``.
* ``recommend_model``    — keyword heuristic over task description.

The real router-driven ``select_provider(dry_run=True)`` recommendation
backend is deferred to Sprint MCP S3 — S2 ships a deterministic heuristic
so the helper-skill UX is testable end-to-end before the router has dry-run
mode wired.

Public surface
==============

* ``build_server()`` — construct the ``mcp.server.lowlevel.Server`` with
  the three tools registered. Used by ``api/mcp_endpoint.py`` and tests.
* ``build_session_manager()`` — factory for ``StreamableHTTPSessionManager``
  bound to the server. Lifecycle is managed via
  ``mcp_server.session_manager.run()`` inside ``main.py``'s lifespan.
* ``MCP_PRINCIPAL_CTX`` — ``contextvars.ContextVar`` set by the ASGI
  wrapper and read by each tool to discover the calling account.
"""

from voltari_gateway.mcp_server.context import (
    MCP_PRINCIPAL_CTX,
    McpPrincipal,
)
from voltari_gateway.mcp_server.server import build_server, build_session_manager

__all__ = [
    "MCP_PRINCIPAL_CTX",
    "McpPrincipal",
    "build_server",
    "build_session_manager",
]
