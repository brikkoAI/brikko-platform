"""MCP tool implementations.

Each tool is a single ``async`` function returning a ``dict`` of JSON-
serialisable values. The MCP SDK's ``Server.call_tool`` decorator wraps
the function so that the returned dict becomes the JSON-RPC
``structuredContent`` and is also serialised into the ``content[0].text``
fallback for clients that don't read structured output yet.

Tool registry is built by ``mcp_server.server.build_server`` — each tool
exposes a ``NAME``, ``DESCRIPTION``, ``INPUT_SCHEMA`` (JSON Schema) and a
``handler`` callable. The dispatch dict ``TOOLS`` in ``server.py`` maps
tool names to (handler, scope) pairs so the per-tool scope check can
live in one place rather than being copy-pasted across handlers.

S1+S2 tools: ``read_account``, ``read_usage``, ``recommend_model``.
S3 tools: ``list_models``, ``get_recent_traces``,
``list_cookbook_recipes``, ``list_integrations``.
"""

from voltari_gateway.mcp_server.tools import (
    get_recent_traces,
    list_cookbook_recipes,
    list_integrations,
    list_models,
    read_account,
    read_usage,
    recommend_model,
)

__all__ = [
    "get_recent_traces",
    "list_cookbook_recipes",
    "list_integrations",
    "list_models",
    "read_account",
    "read_usage",
    "recommend_model",
]
