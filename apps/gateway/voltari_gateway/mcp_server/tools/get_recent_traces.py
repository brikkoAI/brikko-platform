"""``get_recent_traces`` MCP tool — last N traces from BrikkoLens.

Queries ``gateway_request_log`` for the calling account, descending by
``created_at``. Each row condenses to an agent-quotable shape:

  - ``request_id`` — opaque server-issued id
  - ``model`` / ``provider``
  - ``status`` — ``success`` | ``error``
  - ``latency_ms`` / ``ttft_ms``
  - ``cost_kopecks`` — already kopecks, NOT RUB; we expose both
  - ``cost_rub``
  - ``prompt_preview`` — first 200 chars of the request body if
    ``store_prompts`` is on, else ``None``. Per CEO 2026-05-11 we never
    surface raw bodies if the account opted out.
  - ``error_message`` — only on ``status=error``.

Defaults: limit=20, no filter. The agent calls it cold ("show me what
just happened?"); subsequent calls can narrow to a specific model or to
errors only.

Index coverage: ``idx_gwrl_account_recent`` (account_id, created_at)
serves the unfiltered path. ``idx_gwrl_model_recent`` (account_id, model,
created_at) serves the model-filtered path. Both are descending-friendly
because Postgres can walk a B-tree in either direction at constant cost.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.db.models import Account, GatewayRequestLog
from voltari_gateway.mcp_server.context import current_principal
from voltari_gateway.utils.errors import GatewayError

NAME = "get_recent_traces"
DESCRIPTION = (
    "Return the most recent gateway requests (default 20, max 50) for the "
    "calling account. Optional filters by model id or by status "
    "('success' | 'error'). Includes latency, cost, error message, and "
    "(if store_prompts is on) a 200-char preview of the prompt. Read-only."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 20,
            "description": "How many traces to return (1..50, default 20).",
        },
        "filter_model": {
            "type": ["string", "null"],
            "description": "Restrict to a single model id (e.g. 'gpt-5.4-mini').",
        },
        "filter_status": {
            "type": ["string", "null"],
            "enum": [None, "success", "error"],
            "description": "Restrict to successful or failed requests only.",
        },
    },
    "required": [],
    "additionalProperties": False,
}

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50
_PROMPT_PREVIEW_CHARS = 200


def _extract_prompt_preview(request_body: dict[str, Any] | None) -> str | None:
    """First 200 chars of the user/system prompts joined.

    Layout follows OpenAI chat-completions: ``{"messages": [{"role":..,
    "content":..}]}``. We coerce non-string content (image-content lists)
    to repr-ish JSON-safe strings via ``str()`` so a vision request
    doesn't blow up the preview path. Non-OpenAI shapes (Anthropic
    ``messages`` is identical, Gemini ``contents`` is different) fall
    through to a top-level repr.
    """
    if not isinstance(request_body, dict):
        return None
    messages = request_body.get("messages")
    if isinstance(messages, list) and messages:
        parts: list[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                # Anthropic/OpenAI-vision shape: list of {"type": "text"|"image", ...}.
                for chunk in content:
                    if isinstance(chunk, dict) and chunk.get("type") == "text":
                        text = chunk.get("text")
                        if isinstance(text, str):
                            parts.append(text)
            if sum(len(p) for p in parts) >= _PROMPT_PREVIEW_CHARS:
                break
        combined = " ".join(parts).strip()
        if combined:
            return combined[:_PROMPT_PREVIEW_CHARS]

    # Gemini-style ``contents`` fallback.
    contents = request_body.get("contents")
    if isinstance(contents, list) and contents:
        for item in contents:
            if isinstance(item, dict):
                parts_field = item.get("parts")
                if isinstance(parts_field, list):
                    for p in parts_field:
                        if isinstance(p, dict):
                            txt = p.get("text")
                            if isinstance(txt, str):
                                return txt[:_PROMPT_PREVIEW_CHARS]
    return None


def _normalise_status(raw: str | None) -> str:
    """Map the raw ``status`` column to one of ``success | error``.

    The gateway writes ``success``, ``error``, sometimes ``timeout`` /
    ``cancelled``. For the MCP shape we collapse anything that isn't
    a clean success into ``error`` so the agent's branch logic is
    binary. The raw value is still returned in ``raw_status`` for the
    rare case an agent wants to triage.
    """
    if raw == "success":
        return "success"
    return "error"


async def handler(arguments: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """Page the most recent rows out of ``gateway_request_log``."""
    principal = current_principal()

    limit_raw = arguments.get("limit", _DEFAULT_LIMIT)
    if not isinstance(limit_raw, int):
        raise GatewayError(
            status_code=400,
            message="limit must be an integer between 1 and 50.",
            type="invalid_request_error",
            code="invalid_limit",
        )
    if limit_raw < 1 or limit_raw > _MAX_LIMIT:
        raise GatewayError(
            status_code=400,
            message=f"limit must be in [1, {_MAX_LIMIT}], got {limit_raw}.",
            type="invalid_request_error",
            code="invalid_limit",
        )

    filter_model_raw = arguments.get("filter_model")
    filter_model: str | None = None
    if isinstance(filter_model_raw, str) and filter_model_raw.strip():
        filter_model = filter_model_raw.strip()

    filter_status_raw = arguments.get("filter_status")
    filter_status: str | None = None
    if isinstance(filter_status_raw, str) and filter_status_raw.strip():
        filter_status = filter_status_raw.strip().lower()
        if filter_status not in ("success", "error"):
            raise GatewayError(
                status_code=400,
                message="filter_status must be 'success' or 'error'.",
                type="invalid_request_error",
                code="invalid_filter_status",
            )

    # Look up the account once for ``store_prompts``. If the account
    # opted out, we suppress every prompt preview — an agent should
    # never see content the account doesn't store.
    account = await db.get(Account, principal.account_id)
    if account is None:
        raise GatewayError(
            status_code=500,
            message="Account row vanished between auth and tool dispatch.",
            type="internal_error",
            code="account_not_found_post_auth",
        )
    can_preview = bool(account.store_prompts)

    stmt = (
        select(GatewayRequestLog)
        .where(GatewayRequestLog.account_id == principal.account_id)
        .order_by(GatewayRequestLog.created_at.desc())
        .limit(limit_raw)
    )
    if filter_model is not None:
        stmt = stmt.where(GatewayRequestLog.model == filter_model)
    if filter_status == "success":
        stmt = stmt.where(GatewayRequestLog.status == "success")
    elif filter_status == "error":
        stmt = stmt.where(GatewayRequestLog.status != "success")

    rows = (await db.execute(stmt)).scalars().all()

    traces: list[dict[str, Any]] = []
    for row in rows:
        normalised = _normalise_status(row.status)
        traces.append(
            {
                "request_id": row.request_id,
                "model": row.model,
                "provider": row.provider,
                "status": normalised,
                "raw_status": row.status,
                "latency_ms": int(row.latency_ms),
                "ttft_ms": int(row.ttft_ms) if row.ttft_ms is not None else None,
                "prompt_tokens": int(row.prompt_tokens),
                "completion_tokens": int(row.completion_tokens),
                "cost_kopecks": int(row.cost_kop),
                "cost_rub": round(int(row.cost_kop) / 100.0, 2),
                "prompt_preview": (
                    _extract_prompt_preview(row.request_body) if can_preview else None
                ),
                "error_code": row.error_code,
                "error_message": (row.error_message if normalised == "error" else None),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    return {
        "traces": traces,
        "total": len(traces),
        "limit": limit_raw,
        "filters": {
            "model": filter_model,
            "status": filter_status,
        },
        "store_prompts_enabled": can_preview,
    }


__all__ = ["DESCRIPTION", "INPUT_SCHEMA", "NAME", "handler"]
