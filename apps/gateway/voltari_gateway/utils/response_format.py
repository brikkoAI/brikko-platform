"""``response_format`` validation + cross-provider mapping (Sprint 9).

OpenAI deprecated ``response_format: { "type": "json_object" }`` on
2026-04-24 in favour of strict JSON Schema:

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "<schema_name>",
            "strict": true,
            "schema": { /* valid JSON Schema */ }
        }
    }

Brikko historically forwarded ``response_format`` verbatim. After the
deprecation we want to:

1. **Validate** at the gateway. If the schema is invalid, refuse with
   400 ``invalid_json_schema`` instead of paying OpenAI for a 4xx.
2. **Refuse strict on incapable providers.** Yandex/Sber don't
   support strict JSON; we 400 with ``model_does_not_support_strict_json``
   so the client picks a different model rather than getting random
   text back.
3. **Map cross-provider.** Anthropic has no native ``json_schema`` —
   we synthesize a tool with the schema and force ``tool_choice``.
   Gemini has ``response_schema`` but uses uppercase type names; we
   translate the JSON Schema to Gemini's shape.

This module is pure: no I/O, no logging side-effects. The chat handler
calls :func:`validate_response_format` early; the per-provider adapters
call :func:`to_anthropic_tool` / :func:`to_gemini_response_schema` at
``_build_kwargs`` time when they see a ``response_format`` in
``ChatCompletionRequest.extra``.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from voltari_gateway.router.catalog import ModelSpec
from voltari_gateway.utils.errors import GatewayError, invalid_request

# OpenAI's strict JSON Schema subset is conservative — explicit type on
# every property, ``additionalProperties: false``, no ``oneOf``/``anyOf``
# at root. We don't enforce the strict subset on input (OpenAI itself
# does, and we'd diverge on minor releases) — we only validate that the
# schema is **a valid JSON Schema** so a typo doesn't slip through.

# Sentinel returned when ``response_format`` is absent/null.
NO_RESPONSE_FORMAT: dict[str, Any] | None = None


class ParsedResponseFormat:
    """Lightweight parse result for a ``response_format`` block.

    We deliberately don't use a frozen dataclass because the chat handler
    needs a couple of attribute lookups; keeping this as a plain class
    avoids dataclass ceremony in a hot path.
    """

    __slots__ = ("raw", "schema", "schema_name", "strict", "type")

    def __init__(
        self,
        *,
        type: str,
        strict: bool,
        schema_name: str | None,
        schema: dict[str, Any] | None,
        raw: dict[str, Any],
    ) -> None:
        self.type = type
        self.strict = strict
        self.schema_name = schema_name
        self.schema = schema
        self.raw = raw


def validate_response_format(
    rf: dict[str, Any] | None,
    *,
    model: ModelSpec,
) -> ParsedResponseFormat | None:
    """Validate the request's ``response_format`` against the chosen model.

    Returns ``None`` if the client didn't pass ``response_format``. Raises
    :class:`GatewayError` (400) for any of:

    * ``response_format`` is not a dict.
    * ``type`` is missing or unknown.
    * ``type == "json_schema"`` but ``json_schema`` block is malformed or
      ``schema`` isn't a valid JSON Schema.
    * ``strict: true`` requested for a model whose ``supports_strict_json``
      flag is False (Yandex/Sber).

    The ``json_object`` legacy type is still accepted (OpenAI hasn't
    removed it; only marked it legacy on 2026-04-24). We forward it as-is.
    """
    if rf is None:
        return None
    if not isinstance(rf, dict):
        raise invalid_request(
            "response_format must be an object.",
            param="response_format",
            code="invalid_response_format",
        )
    rf_type = rf.get("type")
    if rf_type not in ("json_object", "json_schema", "text"):
        raise invalid_request(
            f"Unknown response_format.type {rf_type!r}; expected one of "
            f"'text', 'json_object', 'json_schema'.",
            param="response_format.type",
            code="invalid_response_format",
        )

    if rf_type != "json_schema":
        # No further validation; pass through.
        return ParsedResponseFormat(
            type=rf_type,
            strict=False,
            schema_name=None,
            schema=None,
            raw=rf,
        )

    js = rf.get("json_schema")
    if not isinstance(js, dict):
        raise invalid_request(
            "response_format.json_schema must be an object.",
            param="response_format.json_schema",
            code="invalid_json_schema",
        )

    schema = js.get("schema")
    if not isinstance(schema, dict):
        raise invalid_request(
            "response_format.json_schema.schema must be an object.",
            param="response_format.json_schema.schema",
            code="invalid_json_schema",
        )

    # Validate schema validity (meta-schema). jsonschema raises SchemaError
    # if the schema itself is malformed (e.g. ``type: "stirng"``). We use
    # Draft 2020-12 — what OpenAI's strict mode targets — but accept any
    # earlier draft because their meta-schemas are forward-compatible.
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        # Surface the JSON-pointer of the offending node so the client
        # can fix it without guessing.
        path = "/".join(str(p) for p in exc.absolute_path)
        msg = f"Invalid JSON Schema at {path or '<root>'}: {exc.message}"
        raise invalid_request(
            msg,
            param="response_format.json_schema.schema",
            code="invalid_json_schema",
        ) from exc

    strict = bool(js.get("strict", False))
    if strict and not model.supports_strict_json:
        raise GatewayError(
            status_code=400,
            message=(
                f"Model {model.id!r} does not support response_format with "
                f"strict=true. Use a different model (e.g. gpt-5.5, "
                f"claude-sonnet-4.6, gemini-3-flash, deepseek-v4-flash) "
                f"or set strict=false."
            ),
            type="invalid_request_error",
            code="model_does_not_support_strict_json",
            param="response_format.json_schema.strict",
        )

    schema_name_raw = js.get("name")
    schema_name = (
        str(schema_name_raw) if isinstance(schema_name_raw, str) and schema_name_raw else None
    )

    return ParsedResponseFormat(
        type="json_schema",
        strict=strict,
        schema_name=schema_name,
        schema=schema,
        raw=rf,
    )


# ---------------------------------------------------------------------------
# Anthropic mapping — JSON Schema → tool with ``input_schema``.
# Anthropic doesn't have a native response_format=json_schema. The standard
# workaround (per Anthropic docs, "Cookbook → Structured outputs") is to
# define a single tool whose ``input_schema`` is the desired JSON shape
# and force ``tool_choice: { type: "tool", name: "<schema_name>" }``.
# The model returns a tool_use block whose ``input`` matches the schema.
# Our adapter already extracts tool_use as OpenAI-style tool_calls, so the
# client gets back ``message.tool_calls[0].function.arguments`` containing
# the JSON. For maximum OpenAI-compat we additionally surface the parsed
# JSON in ``message.content`` so naive clients (that read .content) don't
# break.
# ---------------------------------------------------------------------------


def to_anthropic_tool(parsed: ParsedResponseFormat) -> dict[str, Any]:
    """Synthesize an Anthropic tool from a parsed JSON Schema response_format.

    Caller (anthropic_provider) MUST also set ``tool_choice`` to force
    the model to use this tool. Returns the tool dict in Anthropic's
    shape (matches what ``_convert_tools_to_anthropic`` would produce
    for an equivalent OpenAI tool).
    """
    if parsed.type != "json_schema" or parsed.schema is None:
        raise ValueError("to_anthropic_tool requires type=json_schema with a schema")
    name = parsed.schema_name or "structured_response"
    return {
        "name": name,
        "description": (
            "Return the structured response that matches the requested "
            "JSON Schema. Always call this tool exactly once with the "
            "fields populated from the user's request."
        ),
        "input_schema": parsed.schema,
    }


def to_anthropic_tool_choice(parsed: ParsedResponseFormat) -> dict[str, Any]:
    """Build the matching Anthropic tool_choice forcing the synthesized tool."""
    if parsed.type != "json_schema":
        raise ValueError("to_anthropic_tool_choice requires type=json_schema")
    return {
        "type": "tool",
        "name": parsed.schema_name or "structured_response",
    }


# ---------------------------------------------------------------------------
# Gemini mapping — JSON Schema → Gemini ``response_schema`` + MIME type.
# Gemini's structured-output API expects ``response_mime_type:
# "application/json"`` and a ``response_schema`` whose ``type`` field uses
# the OpenAPI 3.0 type strings: STRING, NUMBER, INTEGER, BOOLEAN, ARRAY,
# OBJECT. JSON Schema uses lowercase. We do a recursive walk to flip
# ``type`` strings; we drop fields Gemini doesn't accept (``additionalProperties``,
# ``$schema``, ``title``, ``examples``, ``definitions``).
# ---------------------------------------------------------------------------


_GEMINI_TYPE_MAP: dict[str, str] = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
    "null": "STRING",  # Gemini lacks NULL; pass as nullable STRING
}

# JSON Schema fields Gemini either rejects or silently ignores. We strip
# them upfront to keep the request body small and avoid validation noise.
_GEMINI_DROP_FIELDS: frozenset[str] = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "examples",
        "definitions",
        "additionalProperties",
        "patternProperties",
        "default",
    }
)


def to_gemini_response_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively translate a JSON Schema to Gemini's ``response_schema``.

    Pure function; safe to call on user-supplied input. Unknown JSON
    Schema fields are passed through untouched (Gemini ignores what it
    doesn't recognise rather than 4xx-ing on extras as of 2026-04).
    """
    translated = _translate_node(schema)
    # Top-level of a JSON Schema is always an object; the recursive walker
    # returns Any for flexibility. We assert the dict shape so the public
    # API has a stable return type for mypy.
    assert isinstance(translated, dict)
    return translated


def _translate_node(node: Any) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in _GEMINI_DROP_FIELDS:
                continue
            if k == "type" and isinstance(v, str):
                out["type"] = _GEMINI_TYPE_MAP.get(v.lower(), v.upper())
            elif k == "type" and isinstance(v, list):
                # JSON Schema allows ``type: ["string", "null"]`` for
                # nullable fields. Gemini supports ``nullable: true``.
                non_null = [t for t in v if t != "null"]
                if non_null:
                    out["type"] = _GEMINI_TYPE_MAP.get(
                        str(non_null[0]).lower(), str(non_null[0]).upper()
                    )
                if "null" in v:
                    out["nullable"] = True
            elif k in ("properties", "$defs"):
                out[k] = (
                    {pk: _translate_node(pv) for pk, pv in v.items()} if isinstance(v, dict) else v
                )
            elif k == "items":
                out[k] = _translate_node(v)
            elif k in ("enum", "required"):
                out[k] = v  # arrays of literals; pass through
            else:
                out[k] = _translate_node(v)
        return out
    if isinstance(node, list):
        return [_translate_node(item) for item in node]
    return node


__all__ = [
    "NO_RESPONSE_FORMAT",
    "ParsedResponseFormat",
    "to_anthropic_tool",
    "to_anthropic_tool_choice",
    "to_gemini_response_schema",
    "validate_response_format",
]
