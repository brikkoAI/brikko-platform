"""Server-level smoke tests.

These exercise the SDK-facing surface: the ``Server`` instance from
``build_server()`` registers tools, dispatches to handlers, enforces
scope. End-to-end ASGI/HTTP testing lives in ``test_endpoint_smoke.py``
— here we stay above the wire format.
"""

from __future__ import annotations

import uuid

import pytest
from mcp.server.lowlevel import Server

from voltari_gateway.db.models import McpScope
from voltari_gateway.mcp_server.context import MCP_PRINCIPAL_CTX, McpPrincipal
from voltari_gateway.mcp_server.server import (
    build_server,
    build_session_manager,
    list_tool_names,
)


def test_build_server_returns_server_instance():
    server = build_server()
    assert isinstance(server, Server)


def test_list_tool_names_returns_seven_tools_after_s3():
    """S1+S2 shipped 3 tools (read_account/read_usage/recommend_model). S3
    adds four more: list_models, get_recent_traces, list_cookbook_recipes,
    list_integrations. The registry is the contract — every new tool must
    appear here so a typo in ``server._TOOLS`` is caught on the first run.
    """
    names = list_tool_names()
    assert set(names) == {
        "read_account",
        "read_usage",
        "recommend_model",
        "list_models",
        "get_recent_traces",
        "list_cookbook_recipes",
        "list_integrations",
    }


def test_build_session_manager_returns_manager():
    """Constructor side-effect-free; instance can be created without I/O."""
    manager = build_session_manager()
    assert manager is not None
    # stateless flag set at construction
    assert manager.stateless is True


def test_two_calls_create_independent_instances():
    """Each call to build_session_manager → fresh instance.

    The SDK's ``run()`` can only be entered once per instance — tests
    that spin up an app twice (test isolation!) get separate manager
    instances and the constraint never triggers.
    """
    a = build_session_manager()
    b = build_session_manager()
    assert a is not b


# ---------------------------------------------------------------------------
# Scope enforcement
# ---------------------------------------------------------------------------


async def test_scope_mismatch_raises_403(db, mcp_token_fixture, bind_principal):
    """Token with read_account scope cannot call read_usage."""
    # Token's scope is READ_ACCOUNT (default fixture). Try to dispatch
    # read_usage through the server's tool table — should hit the guard.
    from voltari_gateway.mcp_server.server import _TOOLS, _scope_satisfies

    bind_principal(mcp_token_fixture)
    handler, required, _, _ = _TOOLS["read_usage"]
    principal_scope = MCP_PRINCIPAL_CTX.get().scope
    assert principal_scope == McpScope.READ_ACCOUNT
    assert not _scope_satisfies(principal_scope, required)


@pytest.mark.parametrize(
    ("tool_name", "required_scope"),
    [
        ("read_account", McpScope.READ_ACCOUNT),
        ("read_usage", McpScope.READ_USAGE),
        ("recommend_model", McpScope.RECOMMEND_MODEL),
        ("list_models", McpScope.LIST_MODELS),
        ("get_recent_traces", McpScope.READ_TRACES),
        ("list_cookbook_recipes", McpScope.LIST_COOKBOOK),
        ("list_integrations", McpScope.LIST_INTEGRATIONS),
    ],
)
def test_scope_table_matches_design(tool_name, required_scope):
    """Each tool maps to its dedicated scope — one tool, one scope."""
    from voltari_gateway.mcp_server.server import _TOOLS

    _, scope, _, _ = _TOOLS[tool_name]
    assert scope == required_scope


def test_all_scope_passes_every_required_scope():
    """``McpScope.ALL`` is the wildcard granted to default helper-skill
    tokens. It must pass the scope check for every tool — otherwise the
    one-prompt onboarding stops working the moment we add a new tool.
    """
    from voltari_gateway.mcp_server.server import _TOOLS, _scope_satisfies

    for _name, (_handler, required, _desc, _schema) in _TOOLS.items():
        assert _scope_satisfies(McpScope.ALL, required), (
            f"ALL scope must satisfy {required} (otherwise default tokens break)"
        )


def test_strict_scope_does_not_pass_other_scopes():
    """Restrictive tokens (single specific scope) must NOT pass other
    tools' scope checks. Wildcard is the only exception.
    """
    from voltari_gateway.mcp_server.server import _scope_satisfies

    assert _scope_satisfies(McpScope.READ_ACCOUNT, McpScope.READ_ACCOUNT)
    assert not _scope_satisfies(McpScope.READ_ACCOUNT, McpScope.READ_USAGE)
    assert not _scope_satisfies(McpScope.READ_ACCOUNT, McpScope.LIST_MODELS)
    assert not _scope_satisfies(McpScope.LIST_MODELS, McpScope.READ_TRACES)


# ---------------------------------------------------------------------------
# Context plumbing
# ---------------------------------------------------------------------------


def test_principal_round_trip_through_contextvar():
    """``MCP_PRINCIPAL_CTX`` is task-local — set/reset semantics work."""
    principal = McpPrincipal(
        account_id=uuid.uuid4(),
        token_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scope=McpScope.READ_ACCOUNT,
        tariff="pro",
    )
    token = MCP_PRINCIPAL_CTX.set(principal)
    try:
        from voltari_gateway.mcp_server.context import current_principal

        retrieved = current_principal()
        assert retrieved is principal
    finally:
        MCP_PRINCIPAL_CTX.reset(token)


def test_current_principal_raises_when_unbound():
    """No binding → RuntimeError, never None."""
    from voltari_gateway.mcp_server.context import current_principal

    # Make sure we're really unbound (some test before may have leaked).
    token = MCP_PRINCIPAL_CTX.set(None)
    try:
        with pytest.raises(RuntimeError, match="without an authenticated"):
            current_principal()
    finally:
        MCP_PRINCIPAL_CTX.reset(token)
