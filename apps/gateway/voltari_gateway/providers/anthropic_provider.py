"""Anthropic provider adapter.

Uses the official ``anthropic>=0.39`` Python SDK. Anthropic's wire protocol
differs from OpenAI's — separate ``system`` parameter (not a message),
``messages.create`` instead of ``chat.completions.create``, distinct
streaming event types — so we translate both directions.

Translation contract:

* **Input**: extract leading ``system`` messages into the top-level
  ``system`` arg (with cache_control on the last block when the system
  prompt is large enough to warrant prompt caching). Forward the rest as
  ``messages`` with ``role`` ∈ {user, assistant} only — Anthropic
  rejects ``role="system"`` mid-conversation.
* **Output**: re-shape ``Message`` (non-stream) into the OpenAI
  ``chat.completion`` envelope. Tool-use content blocks are surfaced as
  ``tool_calls`` so existing OpenAI client code keeps working.
* **Streaming**: translate Anthropic's event stream
  (``message_start`` / ``content_block_start`` / ``content_block_delta``
  / ``content_block_stop`` / ``message_delta`` / ``message_stop``) into
  OpenAI's ``chat.completion.chunk`` SSE format. Per arch decision 4 —
  we are an OpenAI-compatible gateway, the client should never see
  Anthropic-shaped chunks.

Prompt caching: Anthropic charges 0.1× input rate for cached blocks. We
attach ``cache_control={"type":"ephemeral"}`` to the last system block
once the system prompt exceeds ``CACHE_THRESHOLD_CHARS`` (≈1024 tokens —
Anthropic's minimum). System prompts shorter than that are not worth
caching (Anthropic charges a 25% surcharge on the cache write, so the
break-even is around the 2nd reuse of a 1k+ token prompt).
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    RateLimitError,
)

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
    ProviderTimeoutError,
)
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

# Anthropic minimum cacheable prompt size: ~1024 tokens for Sonnet/Opus.
# We use a char proxy (≈4 chars/token) so we don't need a tokenizer in
# the hot path; under-counting just means we miss a few cache opportunities.
CACHE_THRESHOLD_CHARS: int = 4096


def _map_status_error(exc: APIStatusError) -> ProviderError:
    status = exc.status_code
    msg = f"anthropic_status_{status}: {getattr(exc, 'message', str(exc))}"
    if status in (401, 403):
        return ProviderAuthError(msg, status_code=status)
    if status == 429:
        retry_after: float | None = None
        resp = getattr(exc, "response", None)
        if resp is not None and hasattr(resp, "headers"):
            ra = resp.headers.get("retry-after")
            try:
                retry_after = float(ra) if ra is not None else None
            except (TypeError, ValueError):
                retry_after = None
        return ProviderRateLimitError(msg, retry_after_s=retry_after, status_code=status)
    if 500 <= status < 600:
        return ProviderServerError(msg, status_code=status)
    if 400 <= status < 500:
        return ProviderClientError(msg, status_code=status)
    return ProviderError(msg, status_code=status)


def _split_system(
    messages: list[dict[str, Any]],
    *,
    extended_cache: bool = False,
) -> tuple[Any, list[dict[str, Any]]]:
    """Pull ``role=system`` messages out into the Anthropic ``system`` param.

    Returns ``(system_value, remaining_messages)``. ``system_value`` is:

    * ``None`` if no system messages,
    * a plain string if all systems are short and don't need caching,
    * a list of content blocks (with ``cache_control`` on the last block)
      if the combined system text is long enough to benefit from caching.

    Sprint 9 — when ``extended_cache=True`` (header X-Brikko-Cache:
    anthropic-extended) any auto-emitted cache_control gets ``ttl: "1h"``
    so RAG/batch workloads with hour-scale system-prompt reuse get the
    longer cache window.
    """
    system_chunks: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content")
            if isinstance(content, str):
                system_chunks.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        system_chunks.append(str(part.get("text", "")))
        else:
            rest.append(m)

    if not system_chunks:
        return None, rest

    combined = "\n\n".join(system_chunks)
    if len(combined) >= CACHE_THRESHOLD_CHARS:
        cache_control: dict[str, Any] = {"type": "ephemeral"}
        if extended_cache:
            cache_control["ttl"] = "1h"
        # Block list with cache_control on last (and only) block.
        return [
            {
                "type": "text",
                "text": combined,
                "cache_control": cache_control,
            }
        ], rest

    return combined, rest


def _apply_extended_ttl_to_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rewrite caller-supplied ``cache_control`` on text blocks to ttl=1h.

    Walks each message's ``content`` (when it's a list of blocks) and
    upgrades any ``cache_control: { "type": "ephemeral" }`` to add
    ``"ttl": "1h"``. Leaves blocks without cache_control untouched —
    we never *add* caching, only extend the TTL of caching the caller
    already opted into. Mutates a shallow copy; the input list is not
    modified.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        new_blocks: list[Any] = []
        mutated = False
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("cache_control"), dict):
                cc = dict(block["cache_control"])
                if cc.get("type") == "ephemeral":
                    cc.setdefault("ttl", "1h")
                    new_block = {**block, "cache_control": cc}
                    new_blocks.append(new_block)
                    mutated = True
                    continue
            new_blocks.append(block)
        if mutated:
            out.append({**m, "content": new_blocks})
        else:
            out.append(m)
    return out


def _normalise_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce OpenAI-shaped messages into Anthropic's accepted shape.

    Anthropic supports ``role`` ∈ {user, assistant} and content as either
    a string or a list of blocks (text, image, tool_use, tool_result).
    OpenAI ``tool`` role messages map to ``user`` with a ``tool_result``
    content block. Empty content is dropped — Anthropic rejects it.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "tool":
            # Convert OpenAI tool result → Anthropic tool_result block.
            tool_use_id = m.get("tool_call_id") or ""
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": text,
                        }
                    ],
                }
            )
            continue
        if role == "developer":
            # Treat developer == system (Anthropic has no developer role).
            # The caller already extracted system messages; this branch only
            # runs if a developer message somehow arrived after system split.
            role = "user"
        if role not in ("user", "assistant"):
            # Defensive — skip unknown roles rather than 4xx the upstream.
            continue
        if content is None or content == "":
            continue
        out.append({"role": role, "content": content})
    return out


def _content_blocks_to_text(blocks: Any) -> tuple[str, list[dict[str, Any]]]:
    """Extract text and tool_use calls from Anthropic content blocks.

    Returns ``(joined_text, tool_calls_in_openai_shape)``.
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    if not isinstance(blocks, list):
        return "", []
    for b in blocks:
        # SDK pydantic-shaped objects expose ``.type``; raw API responses come as dicts.
        btype = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
        if btype == "text":
            text = b.get("text") if isinstance(b, dict) else getattr(b, "text", "")
            text_parts.append(str(text))
        elif btype == "tool_use":
            tu_id = b.get("id") if isinstance(b, dict) else getattr(b, "id", "")
            tu_name = b.get("name") if isinstance(b, dict) else getattr(b, "name", "")
            tu_input = b.get("input") if isinstance(b, dict) else getattr(b, "input", {})
            tool_calls.append(
                {
                    "id": tu_id,
                    "type": "function",
                    "function": {
                        "name": tu_name,
                        "arguments": json.dumps(tu_input or {}, ensure_ascii=False),
                    },
                }
            )
    return "".join(text_parts), tool_calls


def _stop_reason_to_finish(stop_reason: str | None) -> str:
    """Map Anthropic ``stop_reason`` to OpenAI ``finish_reason``."""
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
    }.get(stop_reason or "", "stop")


class AnthropicProvider(Provider):
    """Adapter wrapping the Anthropic Python SDK."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        timeout_seconds: float = 60.0,
        outbound_proxy: str | None = None,
    ) -> None:
        # Двухсерверная архитектура (CEO 2026-04-30): outbound_proxy → пробрасываем
        # кастомный httpx-клиент в SDK через ``http_client``. Anthropic SDK
        # тоже принимает ``http_client`` (см. AsyncAnthropic.__init__).
        timeout = httpx.Timeout(timeout_seconds, read=None, connect=10.0)
        sdk_kwargs: dict[str, object] = {
            "api_key": api_key,
            "base_url": base_url,
            "max_retries": 0,
        }
        if outbound_proxy:
            self._http_client: httpx.AsyncClient | None = httpx.AsyncClient(
                proxy=outbound_proxy,
                timeout=timeout,
            )
            sdk_kwargs["http_client"] = self._http_client
        else:
            self._http_client = None
            sdk_kwargs["timeout"] = timeout
        self._client = AsyncAnthropic(**sdk_kwargs)  # type: ignore[arg-type]

    async def aclose(self) -> None:
        await self._client.close()
        if self._http_client is not None:
            await self._http_client.aclose()

    # ---- helpers -------------------------------------------------------------

    @staticmethod
    def _wrap_error(exc: Exception) -> ProviderError:
        if isinstance(exc, RateLimitError):
            return _map_status_error(exc)
        if isinstance(exc, APITimeoutError):
            return ProviderTimeoutError(str(exc))
        if isinstance(exc, APIConnectionError):
            return ProviderTimeoutError(str(exc))
        if isinstance(exc, APIStatusError):
            return _map_status_error(exc)
        return ProviderError(str(exc))

    @staticmethod
    def _build_kwargs(req: ChatCompletionRequest, *, stream: bool) -> dict[str, Any]:
        # Sprint 9 Task 4 — header X-Brikko-Cache: anthropic-extended bumps
        # the ephemeral cache TTL from 5 minutes to 1 hour. We rewrite any
        # caller-supplied cache_control on system + content blocks to add
        # ttl=1h. Anthropic's default 5m TTL is fine for chat-style traffic;
        # 1h is the right knob for batch / RAG workloads where the same
        # system prompt is reused for ≥1k requests over an hour.
        extended_cache = bool(req.extra.get("_brikko_anthropic_cache_extended"))

        system, msgs = _split_system(messages=req.messages, extended_cache=extended_cache)
        normalised_msgs = _normalise_messages(msgs)
        if extended_cache:
            normalised_msgs = _apply_extended_ttl_to_messages(normalised_msgs)
        kwargs: dict[str, Any] = {
            "model": req.model.upstream_id,
            "messages": normalised_msgs,
            # Anthropic requires max_tokens. Default to 4096 if caller didn't
            # set one — same default as the official cookbook examples.
            "max_tokens": req.max_tokens or 4096,
        }
        if system is not None:
            kwargs["system"] = system
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if req.top_p is not None:
            kwargs["top_p"] = req.top_p
        if req.stop is not None:
            kwargs["stop_sequences"] = [req.stop] if isinstance(req.stop, str) else list(req.stop)

        # Forward tools and tool_choice if present.
        tools = req.extra.get("tools")
        tool_choice = req.extra.get("tool_choice")

        # Sprint 9 — JSON Schema strict mode workaround for Anthropic.
        # Anthropic has no native ``response_format`` — the supported
        # idiom is "define a tool with the schema as input_schema, force
        # tool_choice". We add the synthetic tool to whatever ``tools``
        # the user already had; if they passed a tool_choice we leave it
        # alone (their explicit choice wins; if they want strict JSON
        # they should drop their tool_choice).
        rf = req.extra.get("response_format")
        if isinstance(rf, dict) and rf.get("type") == "json_schema":
            from voltari_gateway.utils.response_format import (
                to_anthropic_tool,
                to_anthropic_tool_choice,
                validate_response_format,
            )

            parsed = validate_response_format(rf, model=req.model)
            if parsed is not None:
                synth_tool = to_anthropic_tool(parsed)
                merged_tools = list(_convert_tools_to_anthropic(tools)) if tools else []
                merged_tools.append(synth_tool)
                kwargs["tools"] = merged_tools
                # Force the synthesized tool unless caller already pinned
                # a different tool_choice.
                if tool_choice is None:
                    kwargs["tool_choice"] = to_anthropic_tool_choice(parsed)
                else:
                    kwargs["tool_choice"] = _convert_tool_choice(tool_choice)
            tools = None  # consumed
            tool_choice = None
        if tools:
            kwargs["tools"] = _convert_tools_to_anthropic(tools)
        if tool_choice:
            kwargs["tool_choice"] = _convert_tool_choice(tool_choice)

        # Pass-through of any anthropic-native fields user opted into via extra.
        for k in ("metadata", "top_k", "thinking"):
            if k in req.extra and req.extra[k] is not None:
                kwargs[k] = req.extra[k]
        if stream:
            kwargs["stream"] = True
        return kwargs

    # ---- non-stream ----------------------------------------------------------

    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        kwargs = self._build_kwargs(req, stream=False)
        try:
            message = await self._client.messages.create(**kwargs)
        except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
            log.warning("anthropic_error", model=req.model.id, error=str(exc))
            raise self._wrap_error(exc) from exc
        except Exception as exc:  # pragma: no cover — defensive
            log.exception("anthropic_unexpected_error", model=req.model.id)
            raise ProviderError(str(exc)) from exc

        # ``message`` is a pydantic model in the SDK; .model_dump for raw access.
        raw_msg: dict[str, Any] = (
            message.model_dump() if hasattr(message, "model_dump") else dict(message)
        )

        text, tool_calls = _content_blocks_to_text(raw_msg.get("content"))
        usage_dict = raw_msg.get("usage") or {}
        prompt_tokens = int(usage_dict.get("input_tokens", 0))
        cached_tokens = int(usage_dict.get("cache_read_input_tokens", 0))
        # ``cache_creation_input_tokens`` is billed at +25% — we count those
        # as regular input tokens (compute_cost_kopecks doesn't have a separate
        # bucket, and the difference is small once the cache warms up).
        cache_creation = int(usage_dict.get("cache_creation_input_tokens", 0))
        completion_tokens = int(usage_dict.get("output_tokens", 0))

        message_msg: dict[str, Any] = {"role": "assistant", "content": text or None}
        if tool_calls:
            message_msg["tool_calls"] = tool_calls
            if not text:
                message_msg["content"] = None
        finish_reason = _stop_reason_to_finish(raw_msg.get("stop_reason"))

        # Build OpenAI-shaped response envelope.
        envelope: dict[str, Any] = {
            "id": raw_msg.get("id") or f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model.id,
            "choices": [
                {
                    "index": 0,
                    "message": message_msg,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens + cache_creation,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + cache_creation + completion_tokens,
                "prompt_tokens_details": {"cached_tokens": cached_tokens},
            },
        }

        usage = ChatCompletionUsage(
            prompt_tokens=prompt_tokens + cache_creation,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + cache_creation + completion_tokens,
            cached_tokens=cached_tokens,
        )
        return ChatCompletionResponse(
            raw=envelope, usage=usage, model_id=req.model.id, provider=self.name
        )

    # ---- native pass-through (used by /v1/messages) -------------------------

    async def chat_completion_anthropic_native(
        self,
        *,
        model_upstream_id: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: Any = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        stop_sequences: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        thinking: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Call Anthropic ``messages.create`` and return the **raw Anthropic** dict.

        Unlike ``chat_completion()`` (which re-shapes into OpenAI envelope),
        this method passes the body through and returns the upstream
        ``Message`` as a plain dict. Used by ``/v1/messages`` when the
        routed primary is Anthropic — we forward every field 1:1.

        Caller is responsible for assembling the ``messages`` and ``system``
        per the Anthropic spec. We do NOT auto-attach ``cache_control``
        here — the caller (the /v1/messages route) is the right place to
        decide caching policy because it knows the inbound shape.
        """
        kwargs: dict[str, Any] = {
            "model": model_upstream_id,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if system is not None:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p
        if top_k is not None:
            kwargs["top_k"] = top_k
        if stop_sequences:
            kwargs["stop_sequences"] = stop_sequences
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if metadata is not None:
            kwargs["metadata"] = metadata
        if thinking is not None:
            kwargs["thinking"] = thinking
        if extra_headers:
            kwargs["extra_headers"] = extra_headers

        try:
            message = await self._client.messages.create(**kwargs)
        except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
            log.warning("anthropic_native_error", model=model_upstream_id, error=str(exc))
            raise self._wrap_error(exc) from exc

        if hasattr(message, "model_dump"):
            dumped: dict[str, Any] = message.model_dump()
            return dumped
        return dict(message)

    async def chat_completion_anthropic_native_stream(
        self,
        *,
        model_upstream_id: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: Any = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        stop_sequences: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        thinking: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream Anthropic events as raw SSE bytes (Anthropic native shape).

        Unlike ``chat_completion_stream()`` (which translates to OpenAI
        chunks), this yields ``event: <name>\\n data: {...}\\n\\n`` exactly
        as Anthropic emits — so we can pipe straight to a Claude Code SDK
        client without re-encoding.

        We aggregate the stream's ``output_tokens`` count into a
        ``usage`` dict on the closing ``message_delta`` so the /v1/messages
        post-flight billing has a real number to bill against. The caller
        peeks the events as they pass through.
        """
        kwargs: dict[str, Any] = {
            "model": model_upstream_id,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if system is not None:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p
        if top_k is not None:
            kwargs["top_k"] = top_k
        if stop_sequences:
            kwargs["stop_sequences"] = stop_sequences
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if metadata is not None:
            kwargs["metadata"] = metadata
        if thinking is not None:
            kwargs["thinking"] = thinking
        if extra_headers:
            kwargs["extra_headers"] = extra_headers

        try:
            ctx = self._client.messages.stream(**kwargs)
            stream = await ctx.__aenter__()
        except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
            log.warning(
                "anthropic_native_stream_error",
                model=model_upstream_id,
                error=str(exc),
            )
            raise self._wrap_error(exc) from exc

        async def _gen() -> AsyncIterator[bytes]:
            try:
                async for event in stream:
                    # Each event already carries .type. Re-render as SSE
                    # so the route can stream it through unchanged.
                    etype = getattr(event, "type", None) or "unknown"
                    data = event.model_dump() if hasattr(event, "model_dump") else {"type": etype}
                    yield (
                        f"event: {etype}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    ).encode()
            except (
                RateLimitError,
                APITimeoutError,
                APIConnectionError,
                APIStatusError,
            ) as exc:
                log.warning(
                    "anthropic_native_stream_mid_error",
                    model=model_upstream_id,
                    error=str(exc),
                )
                raise self._wrap_error(exc) from exc
            finally:
                with contextlib.suppress(Exception):  # pragma: no cover
                    await ctx.__aexit__(None, None, None)

        return _gen()

    # ---- stream --------------------------------------------------------------

    async def chat_completion_stream(self, req: ChatCompletionRequest) -> AsyncIterator[bytes]:
        kwargs = self._build_kwargs(req, stream=False)  # SDK chooses stream via .stream()
        # Open the stream eagerly so connection/auth errors surface synchronously.
        try:
            ctx = self._client.messages.stream(**kwargs)
            stream = await ctx.__aenter__()
        except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
            log.warning("anthropic_stream_error", model=req.model.id, error=str(exc))
            raise self._wrap_error(exc) from exc

        chat_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        model_id = req.model.id

        async def _gen() -> AsyncIterator[bytes]:
            # Aggregator for the final usage chunk we emit at the end.
            # Mixed-type aggregator: token counts (int) + stop_reason (str|None).
            agg: dict[str, Any] = {
                "input_tokens": 0,
                "cached_tokens": 0,
                "cache_creation": 0,
                "output_tokens": 0,
                "stop_reason": None,
            }
            # Track tool-use blocks across deltas so we can emit OpenAI-style
            # ``tool_calls`` deltas with stable indices.
            tool_indices: dict[int, int] = {}  # anthropic block index → openai tool index
            try:
                # First chunk — role only — matches OpenAI's behaviour.
                first_chunk = _wrap_openai_chunk(
                    chat_id,
                    created,
                    model_id,
                    delta={"role": "assistant"},
                    finish_reason=None,
                )
                yield first_chunk

                async for event in stream:
                    etype = getattr(event, "type", None)
                    if etype == "message_start":
                        msg = getattr(event, "message", None)
                        if msg is not None:
                            usage = getattr(msg, "usage", None)
                            if usage is not None:
                                agg["input_tokens"] = int(getattr(usage, "input_tokens", 0) or 0)
                                agg["cached_tokens"] = int(
                                    getattr(usage, "cache_read_input_tokens", 0) or 0
                                )
                                agg["cache_creation"] = int(
                                    getattr(usage, "cache_creation_input_tokens", 0) or 0
                                )
                    elif etype == "content_block_start":
                        block = getattr(event, "content_block", None)
                        idx = int(getattr(event, "index", 0) or 0)
                        btype = getattr(block, "type", None) if block else None
                        if btype == "tool_use":
                            tcall_idx = len(tool_indices)
                            tool_indices[idx] = tcall_idx
                            yield _wrap_openai_chunk(
                                chat_id,
                                created,
                                model_id,
                                delta={
                                    "tool_calls": [
                                        {
                                            "index": tcall_idx,
                                            "id": getattr(block, "id", ""),
                                            "type": "function",
                                            "function": {
                                                "name": getattr(block, "name", ""),
                                                "arguments": "",
                                            },
                                        }
                                    ]
                                },
                                finish_reason=None,
                            )
                    elif etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        idx = int(getattr(event, "index", 0) or 0)
                        dtype = getattr(delta, "type", None) if delta else None
                        if dtype == "text_delta":
                            text = getattr(delta, "text", "") or ""
                            if text:
                                yield _wrap_openai_chunk(
                                    chat_id,
                                    created,
                                    model_id,
                                    delta={"content": text},
                                    finish_reason=None,
                                )
                        elif dtype == "input_json_delta":
                            partial = getattr(delta, "partial_json", "") or ""
                            existing_tcall_idx = tool_indices.get(idx)
                            if existing_tcall_idx is not None and partial:
                                yield _wrap_openai_chunk(
                                    chat_id,
                                    created,
                                    model_id,
                                    delta={
                                        "tool_calls": [
                                            {
                                                "index": existing_tcall_idx,
                                                "function": {"arguments": partial},
                                            }
                                        ]
                                    },
                                    finish_reason=None,
                                )
                    elif etype == "message_delta":
                        delta = getattr(event, "delta", None)
                        if delta is not None:
                            agg["stop_reason"] = getattr(delta, "stop_reason", None)
                        usage = getattr(event, "usage", None)
                        if usage is not None:
                            # message_delta carries final output_tokens.
                            agg["output_tokens"] = int(
                                getattr(usage, "output_tokens", agg["output_tokens"]) or 0
                            )
                    elif etype == "message_stop":
                        # final-usage chunk
                        finish = _stop_reason_to_finish(agg["stop_reason"])
                        yield _wrap_openai_chunk(
                            chat_id,
                            created,
                            model_id,
                            delta={},
                            finish_reason=finish,
                            usage={
                                "prompt_tokens": agg["input_tokens"] + agg["cache_creation"],
                                "completion_tokens": agg["output_tokens"],
                                "total_tokens": (
                                    agg["input_tokens"]
                                    + agg["cache_creation"]
                                    + agg["output_tokens"]
                                ),
                                "prompt_tokens_details": {"cached_tokens": agg["cached_tokens"]},
                            },
                        )
                yield b"data: [DONE]\n\n"
            except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
                log.warning("anthropic_stream_mid_error", model=model_id, error=str(exc))
                raise self._wrap_error(exc) from exc
            finally:
                with contextlib.suppress(Exception):  # pragma: no cover
                    await ctx.__aexit__(None, None, None)

        return _gen()


# ---- helpers (module-level so tests can poke at them) -------------------------


def _wrap_openai_chunk(
    chat_id: str,
    created: int,
    model_id: str,
    *,
    delta: dict[str, Any],
    finish_reason: str | None,
    usage: dict[str, Any] | None = None,
) -> bytes:
    """Render a single OpenAI ``chat.completion.chunk`` SSE line."""
    payload: dict[str, Any] = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _convert_tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI-shaped ``tools`` into Anthropic's tool-spec shape."""
    out: list[dict[str, Any]] = []
    for t in tools:
        if t.get("type") != "function":
            continue
        fn = t.get("function") or {}
        out.append(
            {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def _convert_tool_choice(tool_choice: Any) -> dict[str, Any]:
    """Translate OpenAI ``tool_choice`` to Anthropic's shape."""
    if tool_choice == "auto":
        return {"type": "auto"}
    if tool_choice == "none":
        return {"type": "auto", "disable_parallel_tool_use": True}
    if tool_choice == "required":
        return {"type": "any"}
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        return {
            "type": "tool",
            "name": (tool_choice.get("function") or {}).get("name", ""),
        }
    return {"type": "auto"}
