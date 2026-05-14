"""``list_integrations`` MCP tool — tool-guides for IDE agents.

Surfaces the 5 IDE integrations Brikko maintains setup-guides for
(Cursor, Claude Code, Codex CLI, Copilot CLI, Gemini CLI). Each entry
carries the canonical ``base_url`` an agent would write into a user's
config plus the wire-format (``openai`` / ``anthropic`` / ``google``)
so the calling agent can pick the right env-var names.

Source of truth: ``apps/web/src/lib/integrations.ts``. We mirror the
identity fields here for the same reason ``list_cookbook_recipes``
hardcodes recipes — the gateway image doesn't ship the marketing
build artifacts, and the set is tiny + stable.

Setup URLs point at ``brikko.ru/integrations/{slug}`` — production
canonical.
"""

from __future__ import annotations

from typing import Any

from voltari_gateway.mcp_server.context import current_principal

NAME = "list_integrations"
DESCRIPTION = (
    "Return the 5 Brikko IDE integrations (Cursor, Claude Code, Codex CLI, "
    "Copilot CLI, Gemini CLI). Each entry includes slug, name, base_url to "
    "configure in the IDE, API wire-format (openai/anthropic/google), and "
    "the public setup-guide URL. Read-only."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

# Mirrors apps/web/src/lib/integrations.ts as of 2026-05-12. Adding a
# sixth integration is a one-line addition here AND there — CI lint
# (test_integrations_in_sync, follow-up) catches drift.
_INTEGRATIONS: tuple[dict[str, Any], ...] = (
    {
        "slug": "cursor",
        "name": "Cursor",
        "base_url": "https://api.brikko.ru/v1",
        "api_format": "openai",
        "description": (
            "Cursor IDE. Set 'Override OpenAI Base URL' to api.brikko.ru/v1 "
            "and paste a sk-brk-* key — works with Composer / Cmd+K / Chat."
        ),
    },
    {
        "slug": "claude-code",
        "name": "Claude Code",
        "base_url": "https://api.brikko.ru/v1/anthropic",
        "api_format": "anthropic",
        "description": (
            "Anthropic's official CLI agent. Set ANTHROPIC_BASE_URL + "
            "ANTHROPIC_API_KEY env vars; works with the standard Claude "
            "Sonnet/Opus subscription replacement flow."
        ),
    },
    {
        "slug": "openai-codex",
        "name": "Codex CLI",
        "base_url": "https://api.brikko.ru/v1",
        "api_format": "openai",
        "description": (
            "OpenAI Codex CLI. Set OPENAI_BASE_URL and OPENAI_API_KEY. "
            "Standard chat-completions surface — gpt-5.4-mini works as the "
            "default coding model."
        ),
    },
    {
        "slug": "github-copilot",
        "name": "Copilot CLI",
        "base_url": "https://api.brikko.ru/v1",
        "api_format": "openai",
        "description": (
            "GitHub Copilot CLI (gh extension). Point GITHUB_COPILOT_API_URL "
            "at Brikko via the .copilot config override; uses chat-completions."
        ),
    },
    {
        "slug": "gemini-cli",
        "name": "Gemini CLI",
        "base_url": "https://api.brikko.ru/v1",
        "api_format": "google",
        "description": (
            "Google Gemini CLI. Set the Vertex-compatible endpoint to Brikko "
            "and use a sk-brk-* key. Gemini 3 Flash / Pro models on a rouble bill."
        ),
    },
)

_PUBLIC_BASE = "https://brikko.ru/integrations"


def _render(entry: dict[str, Any]) -> dict[str, Any]:
    slug = entry["slug"]
    return {
        "slug": slug,
        "name": entry["name"],
        "base_url": entry["base_url"],
        "api_format": entry["api_format"],
        "description": entry["description"],
        "setup_url": f"{_PUBLIC_BASE}/{slug}",
    }


async def handler(_arguments: dict[str, Any], _db: Any) -> dict[str, Any]:
    """Materialise the integration list. No filters, no auth-specific data."""
    _ = current_principal()  # auth assertion

    return {
        "integrations": [_render(i) for i in _INTEGRATIONS],
        "total": len(_INTEGRATIONS),
    }


__all__ = ["DESCRIPTION", "INPUT_SCHEMA", "NAME", "handler"]
