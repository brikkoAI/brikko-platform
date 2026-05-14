"""End-to-end smoke tests for the ``/mcp`` ASGI mount.

We hit the endpoint through the existing httpx ASGITransport-backed
``client`` fixture from the top-level conftest. The MCP SDK over HTTP
speaks JSON-RPC 2.0 — we hand-craft the minimal ``initialize`` +
``tools/list`` exchange so the test stays SDK-version agnostic.

What we verify
--------------

* Missing/invalid Authorization → 401 with JSON envelope.
* Valid token + bad MCP body → still 200 from the auth layer (the SDK
  itself returns the JSON-RPC error).
* The mount path is reachable (not 404).
* Per-token rate limit kicks in.

We do NOT try to exercise the full JSON-RPC handshake here — that's the
SDK's responsibility, and pinning to a specific protocol-version flow
would make this suite a maintenance burden every time mcp ships an
update. The S1 management-API tests cover the auth surface; here we
validate the wiring.

The auth-rejection cases run **without** the session manager being
started: the wrapper short-circuits to 401 before the SDK is reached,
so we get to skip the anyio task-group dance for those. The valid-
token + rate-limit cases enter ``manager.run()`` inline so the SDK
sees a live task group.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Unauthenticated paths — no session manager needed (auth short-circuits)
# ---------------------------------------------------------------------------


async def test_mcp_endpoint_requires_bearer(client):
    """POST /mcp without auth → 401 with our error envelope."""
    response = await client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["type"] == "authentication_error"
    assert body["error"]["code"] == "invalid_mcp_token"
    assert response.headers.get("www-authenticate") == 'Bearer realm="brikko-mcp"'


async def test_mcp_endpoint_rejects_wrong_literal(client):
    """``sk-brk-*`` is a chat key — never an MCP token."""
    response = await client.post(
        "/mcp/",
        headers={"Authorization": "Bearer sk-brk-fakekey1234"},
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )
    assert response.status_code == 401


async def test_mcp_endpoint_rejects_random_bytes(client):
    response = await client.post(
        "/mcp/",
        headers={"Authorization": "Bearer mcp-brk-nonexistent"},
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Authenticated path — wiring smoke (needs live task group)
# ---------------------------------------------------------------------------


async def test_mcp_endpoint_accepts_valid_token(client, mcp_token_fixture, mcp_session_manager):
    """A real token passes the auth layer. The SDK may reject the body
    (we send a minimal init payload), but we expect either:
    * 200 + JSON-RPC body, OR
    * 4xx with body NOT from our auth layer.

    What we want to assert: NOT a 401, NOT a 404 (mount works), NOT a 5xx.
    """
    async with mcp_session_manager.run():
        response = await client.post(
            "/mcp/",
            headers={
                "Authorization": f"Bearer {mcp_token_fixture.plaintext}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "id": 1,
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "brikko-test", "version": "0.1"},
                },
            },
        )
    # SDK responds with 200 (JSON or SSE) on a valid initialize.
    # If the protocol version doesn't match it'd be a JSON-RPC error
    # inside a 200 body. Either way: NOT an auth failure.
    assert response.status_code != 401
    assert response.status_code != 404
    assert response.status_code < 500


# ---------------------------------------------------------------------------
# Rate limiting (auth happens, limiter trips before SDK is reached)
# ---------------------------------------------------------------------------


async def test_mcp_rate_limit_blocks_after_threshold(
    client, mcp_token_fixture, redis_client, mcp_session_manager
):
    """Push past the configured cap → 429 with our error envelope.

    We use a small bucket here (limit=1). The first request consumes
    the token; the second request must trip the limit. Both go through
    the auth layer; only the first reaches the SDK, so we still need
    the session manager running.
    """
    from voltari_gateway.mcp_server.rate_limit import (
        McpRateLimiter,
        set_mcp_rate_limiter,
    )

    set_mcp_rate_limiter(McpRateLimiter(redis_client, limit_per_min=1))
    try:
        async with mcp_session_manager.run():
            first = await client.post(
                "/mcp/",
                headers={"Authorization": f"Bearer {mcp_token_fixture.plaintext}"},
                json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
            )
            assert first.status_code != 429  # we used our 1 token

            second = await client.post(
                "/mcp/",
                headers={"Authorization": f"Bearer {mcp_token_fixture.plaintext}"},
                json={"jsonrpc": "2.0", "method": "initialize", "id": 2},
            )
        assert second.status_code == 429
        body = second.json()
        assert body["error"]["code"] == "mcp_rate_limit_exceeded"
        assert second.headers.get("retry-after") is not None
    finally:
        # Restore the test-default limiter so other tests aren't capped.
        set_mcp_rate_limiter(McpRateLimiter(redis_client, limit_per_min=60))
