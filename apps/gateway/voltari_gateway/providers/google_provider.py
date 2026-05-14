"""Google Gemini provider adapter.

Uses the official ``google-genai>=0.3`` SDK (the new unified SDK that
replaced ``google-generativeai`` and ``google-cloud-aiplatform`` for
Gemini API access). The SDK has both sync and async surfaces; we use
``client.aio.*`` exclusively.

Translation contract:

* **Input**: Gemini uses ``contents`` (alternating ``user``/``model``
  turns) plus a top-level ``system_instruction``. We pull system
  messages out, map ``assistant`` → ``model``, and pack each message's
  text into a single-part list.
* **Output**: re-shape ``GenerateContentResponse`` into the OpenAI
  ``chat.completion`` envelope. Function calls are surfaced as
  OpenAI-style ``tool_calls`` so the existing client code keeps working.
* **Streaming**: translate Gemini's chunked response into OpenAI's
  ``chat.completion.chunk`` SSE format, plus a final usage chunk and
  ``data: [DONE]``.

Tiered pricing: ``gemini-3.1-pro`` charges 2× the per-token rate above
200k tokens of input. We don't apply pricing at the adapter — billing
calls ``model.expected_cost_kop`` / ``compute_cost_kopecks`` which read
``model.tiered_pricing`` and pick the right rate based on the actual
input-token count from the response. The adapter just records token
counts faithfully; the catalog knows the rates.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

# google-genai is optional at import time so unrelated tests don't blow up
# when the package isn't installed in CI.
try:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover — adapter-level guard
    genai = None
    genai_errors = None
    genai_types = None

from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    Provider,
    ProviderAuthError,
    ProviderClientError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
)
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


def _map_status_error(status: int, message: str) -> ProviderError:
    if status in (401, 403):
        return ProviderAuthError(f"google_status_{status}: {message}", status_code=status)
    if status == 429:
        return ProviderRateLimitError(f"google_status_{status}: {message}", status_code=status)
    if 500 <= status < 600:
        return ProviderServerError(f"google_status_{status}: {message}", status_code=status)
    if 400 <= status < 500:
        return ProviderClientError(f"google_status_{status}: {message}", status_code=status)
    return ProviderError(f"google_status_{status}: {message}", status_code=status)


def _split_system(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Pull system messages into a single ``system_instruction`` string."""
    sys_chunks: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content")
            if isinstance(content, str):
                sys_chunks.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        sys_chunks.append(str(part.get("text", "")))
        else:
            rest.append(m)
    return ("\n\n".join(sys_chunks) if sys_chunks else None), rest


def _to_gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI messages → Gemini ``contents`` array.

    We build plain dicts (the SDK accepts them directly via duck typing)
    so the adapter stays mock-friendly without needing genai_types in
    the test path.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            # OpenAI tool result → Gemini function_response.
            tool_name = m.get("name") or ""
            content = m.get("content")
            try:
                response = json.loads(content) if isinstance(content, str) else content
            except json.JSONDecodeError:
                response = {"content": content}
            out.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "name": tool_name,
                                "response": response or {},
                            }
                        }
                    ],
                }
            )
            continue
        gem_role = "model" if role == "assistant" else "user"
        content = m.get("content")
        text: str
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            text = ""
        if not text:
            continue
        out.append({"role": gem_role, "parts": [{"text": text}]})
    return out


def _content_to_openai(parts: Any) -> tuple[str, list[dict[str, Any]]]:
    """Extract text + function-call tool_calls from Gemini parts."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    if not parts:
        return "", []
    for p in parts:
        # SDK objects expose attribute access; dicts behave the same with .get.
        if isinstance(p, dict):
            text = p.get("text")
            fc = p.get("function_call")
        else:
            text = getattr(p, "text", None)
            fc = getattr(p, "function_call", None)
        if text:
            text_parts.append(str(text))
        if fc is not None:
            name = fc.get("name") if isinstance(fc, dict) else getattr(fc, "name", "")
            args = fc.get("args") if isinstance(fc, dict) else getattr(fc, "args", {})
            tool_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:16]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args or {}, ensure_ascii=False),
                    },
                }
            )
    return "".join(text_parts), tool_calls


def _finish_reason_to_openai(reason: Any) -> str:
    """Map Gemini ``FinishReason`` to OpenAI's enum."""
    name = getattr(reason, "name", None) or str(reason or "")
    name = name.upper()
    return {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
    }.get(name, "stop")


class GoogleProvider(Provider):
    """Adapter wrapping the google-genai async client."""

    name = "google"

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 60.0,
        outbound_proxy: str | None = None,
    ) -> None:
        if genai is None:  # pragma: no cover
            raise RuntimeError("google-genai is not installed; add it to dependencies.")
        # Двухсерверная архитектура (CEO 2026-04-30): google-genai 1.2 использует
        # ``requests`` под капотом (asyncio.to_thread + requests.Session) — нет
        # параметра передать httpx-клиент. ``HttpOptions`` тоже без proxy-полей.
        # Самый чистый путь — выставить HTTPS_PROXY для текущего процесса при
        # инициализации провайдера (только если он ещё не выставлен пользователем
        # вручную). ``requests`` подхватит env-vars автоматически.
        #
        # ВАЖНО: Yandex/Sber провайдеры строят httpx.AsyncClient с trust_env=False
        # и поэтому игнорируют этот HTTPS_PROXY — их трафик остаётся в РФ.
        if outbound_proxy and not os.environ.get("HTTPS_PROXY"):
            os.environ["HTTPS_PROXY"] = outbound_proxy
            os.environ.setdefault("HTTP_PROXY", outbound_proxy)
            log.info("google_outbound_proxy_configured", proxy=outbound_proxy)
        self._client = genai.Client(api_key=api_key)
        self._timeout_seconds = timeout_seconds
        self._outbound_proxy = outbound_proxy

    async def aclose(self) -> None:
        # google-genai client has no explicit close; underlying httpx is GC'd.
        return None

    @staticmethod
    def _wrap_error(exc: Exception) -> ProviderError:
        # google-genai raises APIError with .code/.status fields.
        if genai_errors is not None and isinstance(exc, genai_errors.APIError):
            status = int(getattr(exc, "code", 0) or 0)
            return _map_status_error(status, str(exc))
        return ProviderError(str(exc))

    @staticmethod
    def _build_config(req: ChatCompletionRequest) -> dict[str, Any]:
        """Render the per-request generation config dict (SDK accepts dicts)."""
        cfg: dict[str, Any] = {}
        if req.temperature is not None:
            cfg["temperature"] = req.temperature
        if req.top_p is not None:
            cfg["top_p"] = req.top_p
        if req.max_tokens is not None:
            cfg["max_output_tokens"] = req.max_tokens
        if req.stop is not None:
            cfg["stop_sequences"] = [req.stop] if isinstance(req.stop, str) else list(req.stop)
        # Tools
        tools = req.extra.get("tools")
        if tools:
            cfg["tools"] = _convert_tools_to_gemini(tools)
        # Sprint 9 — JSON Schema strict mode → Gemini response_schema.
        # Gemini wants ``response_mime_type: application/json`` plus a
        # ``response_schema`` with OpenAPI-3.0-style uppercase types.
        # ``json_object`` (legacy) maps to plain JSON mode without schema.
        rf = req.extra.get("response_format")
        if isinstance(rf, dict):
            rf_type = rf.get("type")
            if rf_type == "json_object":
                cfg["response_mime_type"] = "application/json"
            elif rf_type == "json_schema":
                from voltari_gateway.utils.response_format import (
                    to_gemini_response_schema,
                    validate_response_format,
                )

                parsed = validate_response_format(rf, model=req.model)
                if parsed is not None and parsed.schema is not None:
                    cfg["response_mime_type"] = "application/json"
                    cfg["response_schema"] = to_gemini_response_schema(parsed.schema)
        return cfg

    # ---- non-stream ----------------------------------------------------------

    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        system, msgs = _split_system(req.messages)
        contents = _to_gemini_contents(msgs)
        config = self._build_config(req)
        if system is not None:
            config["system_instruction"] = system

        try:
            response = await self._client.aio.models.generate_content(
                model=req.model.upstream_id,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            log.warning("google_error", model=req.model.id, error=str(exc))
            raise self._wrap_error(exc) from exc

        # Pull text + tool_calls
        candidates = getattr(response, "candidates", None) or []
        cand0 = candidates[0] if candidates else None
        parts = getattr(getattr(cand0, "content", None), "parts", None) if cand0 else None
        text, tool_calls = _content_to_openai(parts)
        finish_reason = _finish_reason_to_openai(
            getattr(cand0, "finish_reason", None) if cand0 else None
        )

        # Usage
        usage_meta = getattr(response, "usage_metadata", None)
        prompt_tokens = int(getattr(usage_meta, "prompt_token_count", 0) or 0)
        cached_tokens = int(getattr(usage_meta, "cached_content_token_count", 0) or 0)
        completion_tokens = int(getattr(usage_meta, "candidates_token_count", 0) or 0)

        msg: dict[str, Any] = {"role": "assistant", "content": text or None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
            if not text:
                msg["content"] = None

        envelope: dict[str, Any] = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model.id,
            "choices": [
                {"index": 0, "message": msg, "finish_reason": finish_reason},
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "prompt_tokens_details": {"cached_tokens": cached_tokens},
            },
        }

        usage = ChatCompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cached_tokens=cached_tokens,
        )
        return ChatCompletionResponse(
            raw=envelope, usage=usage, model_id=req.model.id, provider=self.name
        )

    # ---- stream --------------------------------------------------------------

    async def chat_completion_stream(self, req: ChatCompletionRequest) -> AsyncIterator[bytes]:
        system, msgs = _split_system(req.messages)
        contents = _to_gemini_contents(msgs)
        config = self._build_config(req)
        if system is not None:
            config["system_instruction"] = system

        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=req.model.upstream_id,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            log.warning("google_stream_error", model=req.model.id, error=str(exc))
            raise self._wrap_error(exc) from exc

        chat_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        model_id = req.model.id

        async def _gen() -> AsyncIterator[bytes]:
            # Mixed-type aggregator: token counts (int) + finish reason (str).
            # mypy strict не выводит union самостоятельно, поэтому Any (строгая
            # проверка идёт на boundary в _wrap_openai_chunk).
            agg: dict[str, Any] = {"prompt": 0, "cached": 0, "completion": 0, "finish": "stop"}
            tool_emitted: set[str] = set()
            try:
                yield _wrap_openai_chunk(
                    chat_id,
                    created,
                    model_id,
                    delta={"role": "assistant"},
                    finish_reason=None,
                )
                async for chunk in stream:
                    candidates = getattr(chunk, "candidates", None) or []
                    cand0 = candidates[0] if candidates else None
                    parts = (
                        getattr(getattr(cand0, "content", None), "parts", None) if cand0 else None
                    )
                    if parts:
                        for p in parts:
                            text = (
                                p.get("text") if isinstance(p, dict) else getattr(p, "text", None)
                            )
                            fc = (
                                p.get("function_call")
                                if isinstance(p, dict)
                                else getattr(p, "function_call", None)
                            )
                            if text:
                                yield _wrap_openai_chunk(
                                    chat_id,
                                    created,
                                    model_id,
                                    delta={"content": str(text)},
                                    finish_reason=None,
                                )
                            if fc is not None:
                                name = (
                                    fc.get("name")
                                    if isinstance(fc, dict)
                                    else getattr(fc, "name", "")
                                )
                                args = (
                                    fc.get("args")
                                    if isinstance(fc, dict)
                                    else getattr(fc, "args", {})
                                )
                                # Single emission per call; Gemini sends complete
                                # function_call objects, not partials.
                                key = f"{name}:{json.dumps(args or {}, sort_keys=True)}"
                                if key in tool_emitted:
                                    continue
                                tool_emitted.add(key)
                                tcall_idx = len(tool_emitted) - 1
                                yield _wrap_openai_chunk(
                                    chat_id,
                                    created,
                                    model_id,
                                    delta={
                                        "tool_calls": [
                                            {
                                                "index": tcall_idx,
                                                "id": f"call_{uuid.uuid4().hex[:16]}",
                                                "type": "function",
                                                "function": {
                                                    "name": name,
                                                    "arguments": json.dumps(
                                                        args or {}, ensure_ascii=False
                                                    ),
                                                },
                                            }
                                        ]
                                    },
                                    finish_reason=None,
                                )
                    if cand0 is not None:
                        fr = getattr(cand0, "finish_reason", None)
                        if fr is not None:
                            agg["finish"] = _finish_reason_to_openai(fr)
                    usage_meta = getattr(chunk, "usage_metadata", None)
                    if usage_meta is not None:
                        agg["prompt"] = int(
                            getattr(usage_meta, "prompt_token_count", agg["prompt"]) or 0
                        )
                        agg["cached"] = int(
                            getattr(usage_meta, "cached_content_token_count", agg["cached"]) or 0
                        )
                        agg["completion"] = int(
                            getattr(usage_meta, "candidates_token_count", agg["completion"]) or 0
                        )
                # Final usage chunk
                yield _wrap_openai_chunk(
                    chat_id,
                    created,
                    model_id,
                    delta={},
                    finish_reason=agg["finish"],
                    usage={
                        "prompt_tokens": agg["prompt"],
                        "completion_tokens": agg["completion"],
                        "total_tokens": agg["prompt"] + agg["completion"],
                        "prompt_tokens_details": {"cached_tokens": agg["cached"]},
                    },
                )
                yield b"data: [DONE]\n\n"
            except Exception as exc:
                log.warning("google_stream_mid_error", model=model_id, error=str(exc))
                raise self._wrap_error(exc) from exc

        return _gen()


def _wrap_openai_chunk(
    chat_id: str,
    created: int,
    model_id: str,
    *,
    delta: dict[str, Any],
    finish_reason: str | None,
    usage: dict[str, Any] | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_id,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = usage
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _convert_tools_to_gemini(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI tools → Gemini tool config dicts."""
    function_declarations: list[dict[str, Any]] = []
    for t in tools:
        if t.get("type") != "function":
            continue
        fn = t.get("function") or {}
        function_declarations.append(
            {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return [{"function_declarations": function_declarations}] if function_declarations else []
