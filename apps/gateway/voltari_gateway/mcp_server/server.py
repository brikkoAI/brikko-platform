"""Build the MCP ``Server`` instance and the streamable-HTTP session manager.

Two factories live here:

* ``build_server()`` — registers the three S2 tools on a fresh
  ``mcp.server.lowlevel.Server`` instance. Pure-function, side-effect-free.
* ``build_session_manager(server)`` — wraps the server in a
  ``StreamableHTTPSessionManager`` and returns it. The caller (lifespan
  in ``main.py``) is responsible for entering its ``run()`` context.

The tool dispatch pattern keeps per-tool scope enforcement in one place:
``TOOLS`` maps name → (handler, required_scope). The single
``@server.call_tool`` decorator looks up the handler, checks the scope
against the calling principal, and dispatches. Adding a tool in S3 is
two lines (one in ``TOOLS``, one new file under ``tools/``).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from voltari_gateway.db.models import McpScope
from voltari_gateway.db.session import get_session_factory
from voltari_gateway.mcp_server.context import current_principal
from voltari_gateway.mcp_server.tools import (
    get_recent_traces,
    list_cookbook_recipes,
    list_integrations,
    list_models,
    read_account,
    read_usage,
    recommend_model,
)
from voltari_gateway.utils.errors import GatewayError
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool dispatch table
# ---------------------------------------------------------------------------


# Handler signature: (arguments_dict, db_session) -> dict (structured content).
_Handler = Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]]


# (handler, required_scope, description, input_schema). Required scope is
# the McpScope value the calling token MUST carry — tokens issued with a
# narrower scope hit a 403-style "scope_insufficient" error before the
# handler runs.
_TOOLS: dict[str, tuple[_Handler, McpScope, str, dict[str, Any]]] = {
    read_account.NAME: (
        read_account.handler,
        McpScope.READ_ACCOUNT,
        read_account.DESCRIPTION,
        read_account.INPUT_SCHEMA,
    ),
    read_usage.NAME: (
        read_usage.handler,
        McpScope.READ_USAGE,
        read_usage.DESCRIPTION,
        read_usage.INPUT_SCHEMA,
    ),
    recommend_model.NAME: (
        recommend_model.handler,
        McpScope.RECOMMEND_MODEL,
        recommend_model.DESCRIPTION,
        recommend_model.INPUT_SCHEMA,
    ),
    # ---- S3 read-only tools --------------------------------------------------
    list_models.NAME: (
        list_models.handler,
        McpScope.LIST_MODELS,
        list_models.DESCRIPTION,
        list_models.INPUT_SCHEMA,
    ),
    get_recent_traces.NAME: (
        get_recent_traces.handler,
        McpScope.READ_TRACES,
        get_recent_traces.DESCRIPTION,
        get_recent_traces.INPUT_SCHEMA,
    ),
    list_cookbook_recipes.NAME: (
        list_cookbook_recipes.handler,
        McpScope.LIST_COOKBOOK,
        list_cookbook_recipes.DESCRIPTION,
        list_cookbook_recipes.INPUT_SCHEMA,
    ),
    list_integrations.NAME: (
        list_integrations.handler,
        McpScope.LIST_INTEGRATIONS,
        list_integrations.DESCRIPTION,
        list_integrations.INPUT_SCHEMA,
    ),
}


def list_tool_names() -> list[str]:
    """Return registered tool names. Test convenience."""
    return list(_TOOLS.keys())


# ---------------------------------------------------------------------------
# Audit + tool dispatch
# ---------------------------------------------------------------------------


def _hash_args(arguments: dict[str, Any]) -> str:
    """SHA-256 the canonical-JSON representation of args (first 16 hex chars).

    Used for the audit log so we can correlate calls without storing raw
    tool inputs (which can contain PII — recommend_model's task_description
    can include free-form user content). 16 hex chars = 64 bits = enough
    to disambiguate audit events while staying unsearchable.
    """
    blob = json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


async def _write_audit_async(
    *,
    tool: str,
    args_hash: str,
    latency_ms: int,
    success: bool,
    error_code: str | None,
) -> None:
    """Append one ``mcp_tool_called`` audit row.

    Opens its own DB session so the dispatch path doesn't depend on the
    request-bound session (which the MCP transport doesn't expose).
    Failure to write is logged but never re-raised — audit must never
    take down the request.
    """
    from voltari_gateway.auth.audit import write_audit

    principal = current_principal()

    factory = get_session_factory()
    try:
        async with factory() as db:
            await write_audit(
                db,
                user_id=principal.user_id,
                account_id=principal.account_id,
                action="mcp_tool_called",
                outcome="ok" if success else "failed",
                meta={
                    "tool": tool,
                    "args_hash": args_hash,
                    "latency_ms": latency_ms,
                    "scope": principal.scope.value,
                    "token_id": str(principal.token_id),
                    "error_code": error_code,
                },
            )
            await db.commit()
    except Exception as exc:  # pragma: no cover — audit failure is non-fatal
        log.warning("mcp_audit_write_failed", tool=tool, error=str(exc))


def _scope_satisfies(token_scope: McpScope, required: McpScope) -> bool:
    """Scope-check with one wildcard rule.

    S1+S2 shipped strict equality (single-scope-per-token KISS). S3
    introduces ``McpScope.ALL`` as a super-scope granted to the default
    helper-skill onboarding token — it passes the check for every
    ``required`` scope. Otherwise equality still wins. This keeps
    restrictive tokens (those issued via UI with a specific scope) tight
    while making the one-prompt onboarding work without a scope-picker.
    """
    if token_scope is McpScope.ALL:
        return True
    return token_scope == required


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def build_server() -> Server:
    """Construct a fresh MCP server with the three S2 tools registered.

    Returns a ``Server`` instance ready to be wrapped by
    ``StreamableHTTPSessionManager``. Idempotent and side-effect-free —
    safe to call from tests.
    """
    server: Server = Server("brikko-mcp")

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]  # MCP SDK 1.27 decorators are untyped
    async def _list_tools() -> list[types.Tool]:
        """List all S2 tools. Annotations mark them as read-only."""
        return [
            types.Tool(
                name=name,
                description=description,
                inputSchema=input_schema,
                annotations=types.ToolAnnotations(
                    title=name.replace("_", " ").title(),
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
            for name, (_handler, _scope, description, input_schema) in _TOOLS.items()
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]  # MCP SDK 1.27 decorators are untyped
    async def _dispatch(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Single dispatch entry point.

        1. Lookup the tool. Unknown → MCP-level error.
        2. Check scope against the calling principal.
        3. Open a DB session (transport doesn't carry one).
        4. Run the handler, time it, audit it.

        Any GatewayError is converted to a structured result with an
        ``isError=true`` marker. We do NOT raise out of here — the SDK
        wraps and rethrows but the audit row is more reliable when we
        own the success/failure decision.
        """
        if tool_name not in _TOOLS:
            raise GatewayError(
                status_code=404,
                message=f"Unknown tool '{tool_name}'.",
                type="invalid_request_error",
                code="unknown_tool",
            )

        handler, required_scope, _desc, _schema = _TOOLS[tool_name]
        principal = current_principal()

        if not _scope_satisfies(principal.scope, required_scope):
            # Audit denial — useful for catching mis-scoped tokens in dev.
            args_hash = _hash_args(arguments)
            await _write_audit_async(
                tool=tool_name,
                args_hash=args_hash,
                latency_ms=0,
                success=False,
                error_code="scope_insufficient",
            )
            raise GatewayError(
                status_code=403,
                message=(
                    f"This MCP token has scope '{principal.scope.value}' but "
                    f"'{tool_name}' requires '{required_scope.value}'. Create "
                    f"a new token with the correct scope at /app/keys."
                ),
                type="invalid_request_error",
                code="scope_insufficient",
            )

        factory = get_session_factory()
        args_hash = _hash_args(arguments)
        started = time.perf_counter()
        error_code: str | None = None
        success = True
        try:
            async with factory() as db:
                result = await handler(arguments, db)
        except GatewayError as exc:
            success = False
            error_code = exc.error_code
            raise
        except Exception:
            success = False
            error_code = "internal_error"
            raise
        finally:
            latency_ms = int((time.perf_counter() - started) * 1000)
            await _write_audit_async(
                tool=tool_name,
                args_hash=args_hash,
                latency_ms=latency_ms,
                success=success,
                error_code=error_code,
            )

        return result

    return server


# ---------------------------------------------------------------------------
# Session manager factory + lifespan helper
# ---------------------------------------------------------------------------


def build_session_manager(server: Server | None = None) -> StreamableHTTPSessionManager:
    """Build a streamable-HTTP session manager for the given server.

    If ``server`` is None, a fresh one is built via ``build_server()``.
    ``stateless=True`` is intentional: a stateless transport means each
    HTTP POST is a complete JSON-RPC exchange — no MCP-Session-Id
    accounting on the server side. Simpler to operate (no expiring
    session state in Redis), and Claude Desktop / Cursor both work in
    stateless mode out of the box. We can flip to stateful when V2
    introduces tool sampling / progress notifications.

    ``json_response=False`` keeps the SDK's default streaming-or-json
    content negotiation — clients that send ``Accept: text/event-stream``
    get SSE, plain JSON-Accept clients get JSON.
    """
    server = server or build_server()
    return StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=False,
    )


@asynccontextmanager
async def lifespan(_app: Any) -> AsyncIterator[None]:
    """Standalone lifespan wrapper.

    Not currently mounted from main.py (which composes the MCP manager
    inside its own lifespan), but exported so tests can spin up an MCP-
    only ASGI fixture without dragging in the full gateway lifespan.
    """
    session_manager = build_session_manager()
    async with session_manager.run():
        yield


__all__ = [
    "build_server",
    "build_session_manager",
    "lifespan",
    "list_tool_names",
]
