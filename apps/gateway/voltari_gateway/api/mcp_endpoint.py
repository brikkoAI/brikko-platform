"""ASGI wrapper for the Brikko-MCP streamable-HTTP endpoint.

Mounted as ``/mcp`` on the FastAPI app. The flow per request:

  1. The wrapper sees an ASGI ``scope`` (HTTP request).
  2. It parses ``Authorization: Bearer mcp-brk-...``.
  3. Calls ``resolve_mcp_principal()`` against the DB + Redis cache.
  4. On success, binds the principal to ``MCP_PRINCIPAL_CTX``, runs the
     per-token rate-limit check, then delegates to the SDK's
     ``StreamableHTTPSessionManager.handle_request``.
  5. On failure, writes a minimal HTTP 401/429 response without ever
     invoking the SDK — keeps the error envelope predictable for the
     helper-skill.

Why ASGI-level (not FastAPI-route-level): the SDK's session manager
expects a raw ASGI handler — it streams SSE responses itself, manages
its own lifecycle. Wrapping it as a FastAPI endpoint would require
re-implementing the streaming layer. Mounting as ASGI keeps the SDK in
charge of the wire format and lets us focus on auth + rate-limit.

Notes on JSON-RPC errors
------------------------

We do NOT translate HTTP 401/429 into JSON-RPC error responses. The
MCP transport spec is clear: auth happens at the HTTP layer; clients
that miss auth get a regular HTTP error and must retry with a fresh
token. The SDK clients (Claude Desktop, Cursor) handle this correctly.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.types import Receive, Scope, Send

from voltari_gateway.db.session import get_session_factory
from voltari_gateway.mcp_server.auth import resolve_mcp_principal
from voltari_gateway.mcp_server.context import MCP_PRINCIPAL_CTX
from voltari_gateway.mcp_server.rate_limit import get_mcp_rate_limiter
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


async def _send_json(
    send: Send, status: int, body: dict[str, Any], headers: list[tuple[bytes, bytes]] | None = None
) -> None:
    """Minimal ASGI JSON responder. Used only for the auth/ratelimit error path."""
    payload = json.dumps(body).encode("utf-8")
    base_headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode("ascii")),
    ]
    if headers:
        base_headers.extend(headers)
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": base_headers,
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": payload,
        }
    )


def _read_header(scope: Scope, name: str) -> str | None:
    """Case-insensitive lookup. Returns the first matching header value."""
    target = name.lower().encode("ascii")
    for key, value in scope.get("headers", []):
        if key.lower() == target:
            decoded: str = value.decode("latin-1")
            return decoded
    return None


def build_mcp_asgi(session_manager: Any) -> Any:
    """Return an ASGI callable wrapping ``session_manager.handle_request``.

    The session manager is constructed once in ``main.py`` lifespan (so
    its task group can be entered cleanly) and passed here. The returned
    callable is what FastAPI mounts under ``/mcp``.
    """

    async def asgi_app(scope: Scope, receive: Receive, send: Send) -> None:
        # The SDK only handles HTTP scopes; reject websocket / lifespan
        # politely (FastAPI's mount handles lifespan separately).
        if scope["type"] != "http":
            if scope["type"] == "lifespan":
                # Lifespan passes through — the parent FastAPI app owns it.
                msg = await receive()
                while True:
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
                    msg = await receive()
            return

        authorization = _read_header(scope, "authorization")

        # Auth — fresh DB session, owned by this request.
        factory = get_session_factory()
        async with factory() as db:
            principal = await resolve_mcp_principal(authorization, db)

        if principal is None:
            await _send_json(
                send,
                401,
                {
                    "error": {
                        "type": "authentication_error",
                        "code": "invalid_mcp_token",
                        "message": (
                            "Authentication required. Send a valid MCP token via "
                            "'Authorization: Bearer mcp-brk-...'. Create one at "
                            "https://brikko.ru/app/keys (MCP tokens tab)."
                        ),
                    }
                },
                headers=[(b"www-authenticate", b'Bearer realm="brikko-mcp"')],
            )
            return

        # Rate limit. ``None`` limiter (e.g. test env without redis) → allow.
        limiter = get_mcp_rate_limiter()
        if limiter is not None:
            decision = await limiter.check(principal.token_id)
            if not decision.allowed:
                await _send_json(
                    send,
                    429,
                    {
                        "error": {
                            "type": "rate_limit_error",
                            "code": "mcp_rate_limit_exceeded",
                            "message": (
                                f"MCP rate limit {decision.limit_per_min}/min exceeded. "
                                f"Retry in {decision.reset_in_seconds}s."
                            ),
                        }
                    },
                    headers=[
                        (b"retry-after", str(decision.reset_in_seconds).encode("ascii")),
                        (
                            b"x-ratelimit-limit",
                            str(decision.limit_per_min).encode("ascii"),
                        ),
                        (
                            b"x-ratelimit-remaining",
                            str(decision.remaining).encode("ascii"),
                        ),
                    ],
                )
                log.info(
                    "mcp_rate_limited",
                    token_id=str(principal.token_id),
                    account_id=str(principal.account_id),
                )
                return

        # Bind the principal to the ContextVar for the duration of this
        # request. ``set`` returns a Token that ``reset`` consumes; doing
        # this in try/finally guarantees cleanup even on cancel.
        ctx_token = MCP_PRINCIPAL_CTX.set(principal)
        try:
            await session_manager.handle_request(scope, receive, send)
        finally:
            MCP_PRINCIPAL_CTX.reset(ctx_token)

    return asgi_app


__all__ = ["build_mcp_asgi"]
