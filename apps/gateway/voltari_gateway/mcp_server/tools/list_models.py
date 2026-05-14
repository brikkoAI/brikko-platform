"""``list_models`` MCP tool — public catalog with RUB pricing.

Returns the same shape the marketing endpoint ``/v1/models/public``
serves, but filtered to text-chat models and trimmed to the fields an
agent actually needs ("show me what's available, what does it cost in
rubles, what can it do"). We deliberately reuse ``ModelSpec.to_public_dict``
so the RUB conversion (USD × 80 × 1.15 markup) stays in one place.

Filters:

* ``filter_provider`` — exact match on ``provider.value`` (``openai``,
  ``anthropic``, ``deepseek``, …). Unknown provider → empty list, not
  an error: agent might ask for a typo'd provider and we don't want to
  inflate the audit log with 400s.
* ``filter_capability`` — one of ``vision``, ``tools``, ``streaming``,
  ``strict_json``, ``prompt_caching``, ``ru_legal``. Maps onto the
  ``to_public_dict()`` capabilities block.

Deprecated models are surfaced but flagged. The agent should narrate
"don't pin this one, it sunsets <date>" rather than silently routing
around them — that policy lives in client code.

Pagination: not yet. The MVP catalog is ~40 entries; one MCP call
returns the whole list and the agent filters client-side. When we cross
~200 models we'll add ``cursor`` here.
"""

from __future__ import annotations

from typing import Any

from voltari_gateway.mcp_server.context import current_principal
from voltari_gateway.router.catalog import CATALOG
from voltari_gateway.utils.errors import GatewayError

NAME = "list_models"
DESCRIPTION = (
    "List Brikko's model catalog with RUB prices and capabilities. "
    "Optional filters: provider (openai/anthropic/google/deepseek/yandex/"
    "sber/together/moonshot/minimax/zhipu) and capability (vision, tools, "
    "streaming, strict_json, prompt_caching, ru_legal). Read-only."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "filter_provider": {
            "type": ["string", "null"],
            "description": "Provider id to filter by (e.g. 'openai'). Case-insensitive.",
        },
        "filter_capability": {
            "type": ["string", "null"],
            "enum": [
                None,
                "vision",
                "tools",
                "streaming",
                "strict_json",
                "prompt_caching",
                "ru_legal",
            ],
            "description": "Capability flag the model must have.",
        },
    },
    "required": [],
    "additionalProperties": False,
}


# Capability flag → key in ``to_public_dict()['capabilities']`` (and a
# couple of aliases that map onto a different surface). Keeping the
# mapping in one dict means a new capability is a one-line addition.
_CAPABILITY_KEYS: dict[str, tuple[str, ...]] = {
    "vision": ("vision",),
    "tools": ("tool_calling",),
    "streaming": ("streaming",),
    "strict_json": ("json_schema_strict",),
    "prompt_caching": ("prompt_caching",),
    "ru_legal": ("ru_legal",),
}


def _passes_capability(public: dict[str, Any], capability: str) -> bool:
    """True iff any of the public-dict keys mapped from ``capability`` is True."""
    keys = _CAPABILITY_KEYS.get(capability)
    if keys is None:
        return False
    caps = public.get("capabilities", {})
    return any(bool(caps.get(k)) for k in keys)


def _trim_for_mcp(public: dict[str, Any]) -> dict[str, Any]:
    """Slim ``to_public_dict()`` payload to the agent-relevant subset.

    The full marketing payload carries USD prices and `cache_write` /
    `cached_input` legs we don't need in an MCP recommendation flow.
    Agents quote ``input_rub_per_1m`` / ``output_rub_per_1m`` directly,
    everything else is noise for them.
    """
    pricing = public.get("pricing_rub_per_1m", {}) or {}
    return {
        "id": public["id"],
        "display_name": public.get("display_name") or public["id"],
        "provider": public["provider"],
        "provider_display_name": public.get("provider_display_name", public["provider"]),
        "tier": public.get("tier"),
        "context_window": public.get("context_tokens"),
        "input_rub_per_1m": pricing.get("input"),
        "output_rub_per_1m": pricing.get("output"),
        "cached_input_rub_per_1m": pricing.get("cached_input"),
        "capabilities": public.get("capabilities", {}),
        "modalities": public.get("modalities", {}),
        "description": public.get("description"),
        "deprecated_at": public.get("deprecated_at"),
        "best_for": public.get("best_for", []),
    }


async def handler(arguments: dict[str, Any], _db: Any) -> dict[str, Any]:
    """Materialise the catalog, apply filters, render."""
    _ = current_principal()  # auth assertion

    filter_provider_raw = arguments.get("filter_provider")
    filter_capability_raw = arguments.get("filter_capability")

    # Coerce empty strings (which agents sometimes pass) to None so
    # ``"" == "openai"`` doesn't filter every model out.
    filter_provider: str | None = None
    if isinstance(filter_provider_raw, str) and filter_provider_raw.strip():
        filter_provider = filter_provider_raw.strip().lower()

    filter_capability: str | None = None
    if isinstance(filter_capability_raw, str) and filter_capability_raw.strip():
        filter_capability = filter_capability_raw.strip().lower()
        if filter_capability not in _CAPABILITY_KEYS:
            raise GatewayError(
                status_code=400,
                message=(
                    f"Unknown capability '{filter_capability}'. "
                    f"Allowed: {sorted(_CAPABILITY_KEYS)}."
                ),
                type="invalid_request_error",
                code="invalid_capability",
            )

    models: list[dict[str, Any]] = []
    for spec in CATALOG:
        if filter_provider is not None and spec.provider.value != filter_provider:
            continue
        public = spec.to_public_dict()
        if filter_capability is not None and not _passes_capability(public, filter_capability):
            continue
        models.append(_trim_for_mcp(public))

    return {
        "models": models,
        "total": len(models),
        "filters": {
            "provider": filter_provider,
            "capability": filter_capability,
        },
    }


__all__ = ["DESCRIPTION", "INPUT_SCHEMA", "NAME", "handler"]
