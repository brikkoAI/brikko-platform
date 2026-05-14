"""Tests for Sprint 9 Task 3 — strict JSON Schema response_format.

Coverage:

* Validation rejects malformed schemas with 400 invalid_json_schema.
* strict=true on Yandex/Sber surfaces 400 model_does_not_support_strict_json.
* strict=true on capable models is accepted.
* Anthropic mapping converts JSON Schema → tool with input_schema +
  forces tool_choice.
* Google mapping converts JSON Schema → uppercase Gemini types and sets
  response_mime_type.
* OpenAI / DeepSeek forward the response_format verbatim (pass-through
  contract — they natively support it).
"""

from __future__ import annotations

import pytest

from voltari_gateway.providers.base import ChatCompletionRequest
from voltari_gateway.router.catalog import get_model
from voltari_gateway.utils.errors import GatewayError
from voltari_gateway.utils.response_format import (
    to_anthropic_tool,
    to_anthropic_tool_choice,
    to_gemini_response_schema,
    validate_response_format,
)

# ---------------------------- validation ----------------------------------


def _gpt_5_5():
    return get_model("gpt-5.5")


def _yandex_lite():
    return get_model("yandexgpt-5-lite")


def test_validate_returns_none_for_missing_response_format():
    parsed = validate_response_format(None, model=_gpt_5_5())
    assert parsed is None


def test_validate_passes_text_type():
    parsed = validate_response_format({"type": "text"}, model=_gpt_5_5())
    assert parsed is not None and parsed.type == "text"
    assert parsed.strict is False


def test_validate_passes_legacy_json_object():
    parsed = validate_response_format({"type": "json_object"}, model=_gpt_5_5())
    assert parsed is not None and parsed.type == "json_object"


def test_validate_passes_valid_json_schema():
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    }
    parsed = validate_response_format(rf, model=_gpt_5_5())
    assert parsed is not None
    assert parsed.type == "json_schema"
    assert parsed.strict is True
    assert parsed.schema_name == "answer"
    assert parsed.schema is not None
    assert parsed.schema["properties"]["answer"]["type"] == "string"


def test_validate_rejects_non_dict_response_format():
    with pytest.raises(GatewayError) as excinfo:
        validate_response_format("oops", model=_gpt_5_5())  # type: ignore[arg-type]
    assert excinfo.value.status_code == 400
    assert excinfo.value.error_code == "invalid_response_format"


def test_validate_rejects_unknown_type():
    with pytest.raises(GatewayError) as excinfo:
        validate_response_format({"type": "yaml"}, model=_gpt_5_5())
    assert excinfo.value.error_code == "invalid_response_format"


def test_validate_rejects_malformed_schema():
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "broken",
            "strict": True,
            # ``stirng`` is a typo — not a valid JSON Schema type.
            "schema": {"type": "stirng"},
        },
    }
    with pytest.raises(GatewayError) as excinfo:
        validate_response_format(rf, model=_gpt_5_5())
    assert excinfo.value.status_code == 400
    assert excinfo.value.error_code == "invalid_json_schema"


def test_validate_rejects_strict_on_yandex():
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "strict": True,
            "schema": {"type": "object", "properties": {"a": {"type": "string"}}},
        },
    }
    with pytest.raises(GatewayError) as excinfo:
        validate_response_format(rf, model=_yandex_lite())
    assert excinfo.value.status_code == 400
    assert excinfo.value.error_code == "model_does_not_support_strict_json"


def test_validate_allows_non_strict_schema_on_yandex():
    """strict=false is OK for any provider — no semantic guarantee, just
    a hint that the model should try to return JSON."""
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "strict": False,
            "schema": {"type": "object", "properties": {"a": {"type": "string"}}},
        },
    }
    parsed = validate_response_format(rf, model=_yandex_lite())
    assert parsed is not None
    assert parsed.strict is False


# ---------------------------- Anthropic mapping ----------------------------


def test_to_anthropic_tool_uses_schema_name():
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "strict": True,
            "schema": {"type": "object", "properties": {"a": {"type": "string"}}},
        },
    }
    parsed = validate_response_format(rf, model=_gpt_5_5())
    assert parsed is not None
    tool = to_anthropic_tool(parsed)
    assert tool["name"] == "answer"
    assert tool["input_schema"]["properties"]["a"]["type"] == "string"


def test_to_anthropic_tool_falls_back_to_default_name():
    rf = {
        "type": "json_schema",
        "json_schema": {
            "strict": True,
            "schema": {"type": "object"},
        },
    }
    parsed = validate_response_format(rf, model=_gpt_5_5())
    assert parsed is not None
    tool = to_anthropic_tool(parsed)
    assert tool["name"] == "structured_response"


def test_anthropic_provider_synthesizes_tool_for_json_schema():
    """End-to-end on the adapter's _build_kwargs — passing response_format
    of type json_schema MUST produce a tools entry + tool_choice forcing it."""
    from voltari_gateway.providers.anthropic_provider import AnthropicProvider

    sonnet = get_model("claude-sonnet-4.6")
    assert sonnet is not None
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    }
    req = ChatCompletionRequest(
        model=sonnet,
        messages=[{"role": "user", "content": "Hi"}],
        extra={"response_format": rf},
    )
    kwargs = AnthropicProvider._build_kwargs(req, stream=False)
    # Exactly one synthesized tool, and the tool_choice forces it.
    assert isinstance(kwargs["tools"], list)
    names = [t["name"] for t in kwargs["tools"]]
    assert "answer" in names
    assert kwargs["tool_choice"] == {"type": "tool", "name": "answer"}
    # response_format must NOT be forwarded to Anthropic upstream.
    assert "response_format" not in kwargs


# ---------------------------- Google mapping -------------------------------


def test_to_gemini_response_schema_flips_lowercase_to_uppercase():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["name"],
    }
    out = to_gemini_response_schema(schema)
    assert out["type"] == "OBJECT"
    assert out["properties"]["name"]["type"] == "STRING"
    assert out["properties"]["age"]["type"] == "INTEGER"
    assert out["properties"]["tags"]["type"] == "ARRAY"
    assert out["properties"]["tags"]["items"]["type"] == "STRING"
    assert out["required"] == ["name"]


def test_to_gemini_response_schema_handles_nullable():
    """JSON Schema ``type: ["string", "null"]`` → Gemini ``nullable: true``."""
    schema = {"type": ["string", "null"]}
    out = to_gemini_response_schema(schema)
    assert out["type"] == "STRING"
    assert out["nullable"] is True


def test_to_gemini_response_schema_drops_unsupported_fields():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Answer",
        "type": "object",
        "additionalProperties": False,
        "properties": {"a": {"type": "string"}},
    }
    out = to_gemini_response_schema(schema)
    assert "$schema" not in out
    assert "title" not in out
    assert "additionalProperties" not in out
    assert out["type"] == "OBJECT"


def test_google_provider_sets_response_schema_for_json_schema():
    from voltari_gateway.providers.google_provider import GoogleProvider

    flash = get_model("gemini-3-flash")
    assert flash is not None
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    }
    req = ChatCompletionRequest(
        model=flash,
        messages=[{"role": "user", "content": "Hi"}],
        extra={"response_format": rf},
    )
    cfg = GoogleProvider._build_config(req)
    assert cfg["response_mime_type"] == "application/json"
    assert cfg["response_schema"]["type"] == "OBJECT"
    assert cfg["response_schema"]["properties"]["answer"]["type"] == "STRING"


def test_google_provider_handles_legacy_json_object():
    from voltari_gateway.providers.google_provider import GoogleProvider

    flash = get_model("gemini-3-flash")
    assert flash is not None
    req = ChatCompletionRequest(
        model=flash,
        messages=[{"role": "user", "content": "Hi"}],
        extra={"response_format": {"type": "json_object"}},
    )
    cfg = GoogleProvider._build_config(req)
    assert cfg["response_mime_type"] == "application/json"
    assert "response_schema" not in cfg


# ---------------------------- OpenAI / DeepSeek pass-through ----------------


def test_openai_provider_forwards_response_format_verbatim():
    from voltari_gateway.providers.openai_provider import OpenAIProvider

    gpt = get_model("gpt-5.5")
    assert gpt is not None
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "strict": True,
            "schema": {"type": "object", "properties": {"a": {"type": "string"}}},
        },
    }
    req = ChatCompletionRequest(
        model=gpt,
        messages=[{"role": "user", "content": "Hi"}],
        extra={"response_format": rf},
    )
    kwargs = OpenAIProvider._build_kwargs(req, stream=False)
    assert kwargs["response_format"] == rf


def test_deepseek_provider_forwards_response_format_verbatim():
    from voltari_gateway.providers.deepseek_provider import DeepSeekProvider

    flash = get_model("deepseek-v4-flash")
    assert flash is not None
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "strict": True,
            "schema": {"type": "object", "properties": {"a": {"type": "string"}}},
        },
    }
    req = ChatCompletionRequest(
        model=flash,
        messages=[{"role": "user", "content": "Hi"}],
        extra={"response_format": rf},
    )
    kwargs = DeepSeekProvider._build_kwargs(req, stream=False)
    assert kwargs["response_format"] == rf


# ---------------------------- to_anthropic_tool_choice raise on misuse ------


def test_to_anthropic_tool_choice_raises_on_non_json_schema():
    rf = {"type": "text"}
    parsed = validate_response_format(rf, model=_gpt_5_5())
    assert parsed is not None
    with pytest.raises(ValueError):
        to_anthropic_tool_choice(parsed)
